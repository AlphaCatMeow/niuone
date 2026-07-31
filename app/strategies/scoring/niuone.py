"""牛牛战法: infer market mainlines from cross-sectional strong-stock resonance."""
from __future__ import annotations

import math
import re
import statistics
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime
from typing import Any, Mapping

from ..niuone_risk import (
    NIUONE_ABSOLUTE_POSITION_CAP_PCT,
    niuone_chase_limits,
    niuone_risk_budget,
    niuone_structure_risk_ok,
    niuone_structural_stop_limits,
)
from ..sector_tide_risk import (
    SECTOR_TIDE_EXECUTION_BUFFER_PCT,
    downside_gap_buffer_pct,
    effective_loss_distance_pct,
    risk_sized_position_cap_pct,
    structural_stop_distance_pct,
)
from .common import safe_float, safe_round, with_strategy_profile


NIUONE_STRATEGY_IDS = frozenset({
    "niu_leader",
    "niu_pullback",
    "niu_emerging",
    "niu_reversal_probe",
})
NIUONE_MIN_ROWS = 55
NIUONE_MIN_THEME_MEMBERS = 3
NIUONE_STRONG_SCORE_THRESHOLD = 70.0
NIUONE_CORE_STOCK_LIMIT = 5
NIUONE_LEADER_TIER_LIMIT = 3
NIUONE_MIN_CROSS_DAY_CORE_OVERLAP = 2
NIUONE_TODAY_MIN_QUOTE_COVERAGE = 0.8
NIUONE_TODAY_OBSERVATION_THRESHOLD = 60.0
NIUONE_REVERSAL_MIN_SAMPLE_GAP_MINUTES = 20.0
NIUONE_REVERSAL_MIN_QUOTE_COVERAGE = 0.70
NIUONE_REVERSAL_MIN_BREADTH_PCT = 60.0
NIUONE_REVERSAL_MIN_MEDIAN_CHANGE_PCT = 0.5
NIUONE_REVERSAL_MIN_REBOUND_PCT = 1.5
NIUONE_REVERSAL_MIN_STRENGTH_SCORE = 60.0
NIUONE_REVERSAL_MIN_CORE_COUNT = 2


def _mean(values: list[float], default: float = 0.0) -> float:
    return statistics.mean(values) if values else default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _industry_name(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    for suffix in ("行业", "板块", "概念", "指数"):
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)]
    return text


def _stock_code(value: Any) -> str:
    matched = re.search(r"\d{6}", str(value or ""))
    return matched.group(0) if matched else ""


def _percentile(value: float, population: list[float]) -> float:
    clean = sorted(float(item) for item in population if math.isfinite(float(item)))
    return _percentile_from_sorted(value, clean)


def _percentile_from_sorted(value: float, clean: list[float]) -> float:
    if not clean or len(clean) == 1:
        return 50.0
    below = bisect_left(clean, value)
    equal = bisect_right(clean, value) - below
    return _clamp((below + max(0, equal - 1) / 2) / (len(clean) - 1) * 100)


def _return_pct(
    rows: list[dict[str, Any]],
    lookback: int,
    *,
    current_close: float | None = None,
) -> float | None:
    if len(rows) <= lookback:
        return None
    close = current_close if current_close is not None else safe_float(rows[-1].get("close"))
    base = safe_float(rows[-lookback - 1].get("close"))
    if close is None or base is None or base <= 0:
        return None
    return (close / base - 1) * 100


NIUONE_ATR_LOOKBACK = 14


def _atr(rows: list[dict[str, Any]], lookback: int = NIUONE_ATR_LOOKBACK) -> float | None:
    ranges: list[float] = []
    for index in range(max(1, len(rows) - lookback), len(rows)):
        high = safe_float(rows[index].get("high"))
        low = safe_float(rows[index].get("low"))
        prior = safe_float(rows[index - 1].get("close"))
        if high is not None and low is not None and prior is not None:
            ranges.append(max(high - low, abs(high - prior), abs(low - prior)))
    return _mean(ranges) if ranges else None


def _member_metrics(item: dict[str, Any]) -> dict[str, Any] | None:
    rows = item.get("rows") if isinstance(item.get("rows"), list) else []
    if len(rows) < NIUONE_MIN_ROWS:
        return None
    latest = rows[-1]
    quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
    close = safe_float(quote.get("price"))
    if close is None or close <= 0:
        close = safe_float(latest.get("close"))
    ema20 = safe_float(latest.get("ema20"))
    ema50 = safe_float(latest.get("ema50"))
    ret5 = _return_pct(rows, 5, current_close=close)
    ret20 = _return_pct(rows, 20, current_close=close)
    if close is None or close <= 0 or ret5 is None or ret20 is None:
        return None
    recent_volumes = [safe_float(row.get("volume")) for row in rows[-5:]]
    prior_volumes = [safe_float(row.get("volume")) for row in rows[-25:-5]]
    recent = [value for value in recent_volumes if value is not None and value >= 0]
    prior = [value for value in prior_volumes if value is not None and value >= 0]
    volume_ratio = _mean(recent) / _mean(prior) if prior and _mean(prior) > 0 else 1.0
    prior_highs = [safe_float(row.get("high")) for row in rows[-21:-1]]
    highs = [value for value in prior_highs if value is not None and value > 0]
    live_change = safe_float(quote.get("change_pct"))
    previous_close = safe_float(quote.get("prev_close"))
    if previous_close is None or previous_close <= 0:
        previous_close = safe_float(rows[-2].get("close")) if len(rows) >= 2 else None
    prior_5_base = safe_float(rows[-7].get("close")) if len(rows) >= 7 else None
    prior_ret5 = (
        (previous_close / prior_5_base - 1) * 100
        if previous_close is not None and previous_close > 0 and prior_5_base is not None and prior_5_base > 0
        else None
    )
    intraday_low = safe_float(quote.get("low"))
    if intraday_low is None or intraday_low <= 0:
        intraday_low = safe_float(latest.get("low"))
    rebound_from_low_pct = (
        (close / intraday_low - 1) * 100
        if intraday_low is not None and intraday_low > 0
        else None
    )
    return {
        "code": _stock_code(item.get("code") or latest.get("symbol_code")),
        "name": str(item.get("name") or latest.get("stock_name") or ""),
        "industry": _industry_name(item.get("industry") or latest.get("industry")),
        "ret5": ret5,
        "ret20": ret20,
        "above_ema20": bool(ema20 and close >= ema20),
        "trend_aligned": bool(ema20 and ema50 and close >= ema20 >= ema50),
        "new_high20": bool(highs and close >= max(highs)),
        "volume_ratio": volume_ratio,
        "amount": safe_float(quote.get("amount")) or safe_float(latest.get("quote_amount")) or 0.0,
        "change_pct": live_change if live_change is not None else (safe_float(latest.get("change_pct")) or 0.0),
        "live_change_available": live_change is not None,
        "previous_close": previous_close,
        "prior_ret5": prior_ret5,
        "intraday_low": intraday_low,
        "rebound_from_low_pct": rebound_from_low_pct,
        "reclaim_previous_close": bool(previous_close and close > previous_close),
    }


def _today_theme_metrics(theme_members: list[dict[str, Any]]) -> dict[str, Any]:
    """Return quote-only intraday participation metrics without changing strategy gates."""
    total_count = len(theme_members)
    quoted_members = [member for member in theme_members if member.get("live_change_available")]
    quote_count = len(quoted_members)
    coverage = quote_count / total_count if total_count else 0.0
    eligible = bool(
        total_count >= NIUONE_MIN_THEME_MEMBERS
        and quote_count >= NIUONE_MIN_THEME_MEMBERS
        and coverage >= NIUONE_TODAY_MIN_QUOTE_COVERAGE
    )
    if not quoted_members:
        return {
            "today_eligible_data": False,
            "today_quote_count": 0,
            "today_data_coverage": 0.0,
            "today_up_count": 0,
            "today_1_5pct_count": 0,
            "today_3pct_count": 0,
            "today_5pct_count": 0,
            "today_breadth_pct": None,
            "today_median_change_pct": None,
            "today_strength_score": None,
            "today_leadership_score": None,
            "today_median_rebound_pct": None,
            "today_prior_median_ret5_pct": None,
            "today_leaders": [],
        }

    changes = [float(member["change_pct"]) for member in quoted_members]
    up_count = sum(change > 0 for change in changes)
    advance_1_5_count = sum(change >= 1.5 for change in changes)
    advance_3_count = sum(change >= 3.0 for change in changes)
    advance_5_count = sum(change >= 5.0 for change in changes)
    breadth_pct = up_count / quote_count * 100
    advance_3_pct = advance_3_count / quote_count * 100
    advance_5_pct = advance_5_count / quote_count * 100
    median_change = statistics.median(changes)
    rebound_values = [
        float(value)
        for member in quoted_members
        if (value := safe_float(member.get("rebound_from_low_pct"))) is not None
    ]
    prior_ret5_values = [
        float(value)
        for member in quoted_members
        if (value := safe_float(member.get("prior_ret5"))) is not None
    ]
    positive_median_score = _clamp(max(0.0, median_change) / 5.0 * 100)
    strength_score = _clamp(
        breadth_pct * 0.45
        + advance_3_pct * 0.25
        + advance_5_pct * 0.15
        + positive_median_score * 0.15
    )
    leaders = sorted(
        quoted_members,
        key=lambda member: (float(member["change_pct"]), float(member["amount"])),
        reverse=True,
    )[:NIUONE_CORE_STOCK_LIMIT]
    top_positive_changes = [max(0.0, float(member["change_pct"])) for member in leaders[:3]]
    leadership_score = _clamp(_mean(top_positive_changes) / 10.0 * 100)
    return {
        "today_eligible_data": eligible,
        "today_quote_count": quote_count,
        "today_data_coverage": round(coverage, 4),
        "today_up_count": up_count,
        "today_1_5pct_count": advance_1_5_count,
        "today_3pct_count": advance_3_count,
        "today_5pct_count": advance_5_count,
        "today_breadth_pct": round(breadth_pct, 2),
        "today_median_change_pct": round(median_change, 2),
        "today_strength_score": round(strength_score, 2),
        "today_leadership_score": round(leadership_score, 2),
        "today_median_rebound_pct": (
            round(statistics.median(rebound_values), 2) if rebound_values else None
        ),
        "today_prior_median_ret5_pct": (
            round(statistics.median(prior_ret5_values), 2) if prior_ret5_values else None
        ),
        "today_leaders": [
            {
                "code": member["code"],
                "name": member["name"],
                "strong_score": round(float(member["strong_score"]), 2),
                "change_pct": round(float(member["change_pct"]), 2),
                "rebound_from_low_pct": safe_round(member.get("rebound_from_low_pct"), 2),
                "reclaim_previous_close": bool(member.get("reclaim_previous_close")),
                "role": "today_leader" if index == 0 else "today_core",
            }
            for index, member in enumerate(leaders)
        ],
    }


def _sample_time(value: Any) -> datetime | None:
    text = str(value or "").strip()[:19]
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _reversal_state(
    *,
    today_metrics: Mapping[str, Any],
    previous: Mapping[str, Any],
    flow_value: float | None,
    sample_at: str,
    as_of_date: str,
) -> dict[str, Any]:
    """Detect a broad V-reversal and require two meaningfully spaced samples."""
    previous_date = str(previous.get("as_of_date") or "")[:10]
    same_day = bool(as_of_date and previous_date == as_of_date)
    previous_state = str(previous.get("state") or previous.get("raw_state") or "")
    prior_median_ret5 = safe_float(today_metrics.get("today_prior_median_ret5_pct"))
    origin_weak = bool(
        (same_day and previous.get("reversal_origin_weak") is True)
        or (not same_day and previous_state in {"inactive", "fading", "candidate"})
        or (prior_median_ret5 is not None and prior_median_ret5 <= 0)
    )
    previous_flow = safe_float(previous.get("flow_net_yi"))
    flow_available = flow_value is not None
    flow_positive = bool(flow_value is not None and flow_value > 0)
    flow_flip = bool(flow_positive and previous_flow is not None and previous_flow <= 0)
    flow_improving = bool(
        flow_positive
        and (previous_flow is None or flow_value > previous_flow)
    )
    breadth = safe_float(today_metrics.get("today_breadth_pct")) or 0.0
    median_change = safe_float(today_metrics.get("today_median_change_pct")) or 0.0
    median_rebound = safe_float(today_metrics.get("today_median_rebound_pct")) or 0.0
    strength = safe_float(today_metrics.get("today_strength_score")) or 0.0
    core_count = int(safe_float(today_metrics.get("today_1_5pct_count")) or 0)
    quote_count = int(safe_float(today_metrics.get("today_quote_count")) or 0)
    quote_coverage = safe_float(today_metrics.get("today_data_coverage")) or 0.0
    quote_coverage_ok = bool(
        quote_count >= NIUONE_MIN_THEME_MEMBERS
        and quote_coverage >= NIUONE_REVERSAL_MIN_QUOTE_COVERAGE
    )
    current_candidate = bool(
        quote_coverage_ok
        and origin_weak
        and not previous.get("mainline_confirmed")
        and breadth >= NIUONE_REVERSAL_MIN_BREADTH_PCT
        and median_change >= NIUONE_REVERSAL_MIN_MEDIAN_CHANGE_PCT
        and median_rebound >= NIUONE_REVERSAL_MIN_REBOUND_PCT
        and strength >= NIUONE_REVERSAL_MIN_STRENGTH_SCORE
        and core_count >= NIUONE_REVERSAL_MIN_CORE_COUNT
        and (not flow_available or flow_positive)
    )

    confirmation_count = 0
    last_confirmation_at = ""
    sample_gap_minutes: float | None = None
    if current_candidate:
        previous_count = max(0, int(safe_float(previous.get("reversal_confirmation_count")) or 0))
        prior_candidate = bool(
            same_day
            and (
                previous.get("reversal_candidate") is True
                or previous.get("reversal_confirmed") is True
                or previous_count > 0
            )
        )
        confirmation_count = max(1, previous_count) if prior_candidate else 1
        previous_confirmation_at = str(
            previous.get("reversal_last_confirmation_at")
            or previous.get("sample_at")
            or ""
        )[:19]
        current_time = _sample_time(sample_at)
        previous_time = _sample_time(previous_confirmation_at)
        if current_time is not None and previous_time is not None:
            sample_gap_minutes = max(0.0, (current_time - previous_time).total_seconds() / 60.0)
        if (
            prior_candidate
            and sample_gap_minutes is not None
            and sample_gap_minutes >= NIUONE_REVERSAL_MIN_SAMPLE_GAP_MINUTES
        ):
            confirmation_count += 1
            last_confirmation_at = str(sample_at or "")[:19]
        else:
            last_confirmation_at = previous_confirmation_at or str(sample_at or "")[:19]

    rebound_score = _clamp(median_rebound / 3.0 * 100)
    flow_score = (
        100.0 if flow_flip
        else 85.0 if flow_improving
        else 70.0 if flow_positive
        else 50.0 if not flow_available
        else 0.0
    )
    reversal_score = _clamp(
        strength * 0.30
        + breadth * 0.20
        + rebound_score * 0.20
        + (safe_float(today_metrics.get("today_leadership_score")) or 0.0) * 0.15
        + flow_score * 0.15
    )
    return {
        "reversal_candidate": current_candidate,
        "reversal_confirmed": bool(current_candidate and confirmation_count >= 2),
        "reversal_confirmation_count": confirmation_count,
        "reversal_min_sample_gap_minutes": NIUONE_REVERSAL_MIN_SAMPLE_GAP_MINUTES,
        "reversal_sample_gap_minutes": safe_round(sample_gap_minutes, 2),
        "reversal_last_confirmation_at": last_confirmation_at,
        "reversal_origin_weak": origin_weak,
        "reversal_quote_coverage_ok": quote_coverage_ok,
        "reversal_flow_available": flow_available,
        "reversal_flow_positive": flow_positive,
        "reversal_flow_flip": flow_flip,
        "reversal_flow_improving": flow_improving,
        "reversal_score": round(reversal_score, 2),
    }


def _theme_core_codes(theme: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(theme, Mapping):
        return []
    explicit = theme.get("core_stock_codes")
    if isinstance(explicit, list):
        codes = [_stock_code(value) for value in explicit]
    else:
        strong_stocks = theme.get("strong_stocks") if isinstance(theme.get("strong_stocks"), list) else []
        codes = [_stock_code(item.get("code")) for item in strong_stocks if isinstance(item, Mapping)]
    return list(dict.fromkeys(code for code in codes if code))[:NIUONE_CORE_STOCK_LIMIT]


def _flow_map(flow_rows: Any) -> dict[str, float]:
    if isinstance(flow_rows, dict):
        rows = [*(flow_rows.get("inflow") or []), *(flow_rows.get("outflow") or [])]
    else:
        rows = flow_rows if isinstance(flow_rows, list) else []
    result: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        industry = _industry_name(row.get("name") or row.get("industry") or row.get("行业"))
        value = safe_float(row.get("net_flow_yi") if row.get("net_flow_yi") is not None else row.get("net_flow"))
        if industry and value is not None:
            result[industry] = value
    return result


def _matched_flow(industry: str, flows: dict[str, float]) -> float | None:
    if industry in flows:
        return flows[industry]
    matches = [value for name, value in flows.items() if industry in name or name in industry]
    return _mean(matches) if matches else None


def _external_context(
    dragon_tiger_snapshot: Any,
    news_snapshot: Any,
    members: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    member_codes = {str(member["code"]) for member in members if member.get("code")}
    dragon_source = dragon_tiger_snapshot if isinstance(dragon_tiger_snapshot, Mapping) else {}
    dragon_items = dragon_source.get("items") if isinstance(dragon_source.get("items"), list) else []
    dragon_stocks: dict[str, dict[str, Any]] = {}
    for item in dragon_items:
        if not isinstance(item, Mapping):
            continue
        code = _stock_code(item.get("code"))
        if not code or code not in member_codes or code in dragon_stocks:
            continue
        net = safe_float(item.get("net_amount_yuan"))
        buy = safe_float(item.get("buy_amount_yuan")) or 0.0
        sell = safe_float(item.get("sell_amount_yuan")) or 0.0
        if net is None:
            net = buy - sell
        ratio = safe_float(item.get("net_ratio_pct"))
        if ratio is not None:
            strength = _clamp(ratio / 15.0, -1.0, 1.0)
        elif buy + sell > 0:
            strength = _clamp(net / (buy + sell) / 0.4, -1.0, 1.0)
        else:
            strength = _clamp(net / 50_000_000.0, -1.0, 1.0)
        dragon_stocks[code] = {
            "listed": True,
            "strength": round(strength, 4),
            "score": round(50 + strength * 35, 2),
            "signal": "positive" if strength >= 0.15 else ("negative" if strength <= -0.15 else "neutral"),
            "net_amount_yuan": net,
        }
    dragon = {
        "available": dragon_source.get("available") is True and bool(dragon_items),
        "source": str(dragon_source.get("source") or "local_dragon_tiger_snapshot"),
        "as_of_date": str(dragon_source.get("date") or ""),
        "matched_stock_count": len(dragon_stocks),
        "usage": "previous_trading_day_mainline_confirmation",
    }

    news_source = news_snapshot if isinstance(news_snapshot, Mapping) else {}
    news_records = news_source.get("records") if isinstance(news_source.get("records"), list) else []
    news_stocks: dict[str, dict[str, Any]] = {}
    for record in news_records:
        if not isinstance(record, Mapping):
            continue
        code = _stock_code(record.get("code"))
        if not code or code not in member_codes or code in news_stocks:
            continue
        available = record.get("available") is True
        tone = str(record.get("tone") or "neutral") if available else "neutral"
        news_stocks[code] = {
            "checked": record.get("checked") is True,
            "available": available,
            "tone": tone,
            "tone_label": str(record.get("tone_label") or "中性"),
            "summary": str(record.get("summary") or "")[:600],
            "fetched_at": str(record.get("fetched_at") or ""),
            "window_days": int(safe_float(record.get("window_days")) or 3),
            "adjustment": 0.15 if tone == "positive" else (-0.35 if tone == "negative" else 0.0),
            "error": str(record.get("error") or ""),
        }
    news = {
        "configured": news_source.get("configured") is True,
        "available": any(record.get("available") for record in news_stocks.values()),
        "matched_stock_count": len(news_stocks),
        "usage": "shortlisted_candidate_confirmation",
    }
    return dragon, dragon_stocks, news, news_stocks


def _market_context(
    members: list[dict[str, Any]],
    snapshot: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    up = int(safe_float(snapshot.get("up")) or 0)
    down = int(safe_float(snapshot.get("down")) or 0)
    active = up + down
    breadth = up / active * 100 if active else 50.0
    median_change = safe_float(snapshot.get("median_change_pct")) or 0.0
    limit_up = int(safe_float(snapshot.get("limit_up")) or 0)
    limit_down = int(safe_float(snapshot.get("limit_down")) or 0)
    limit_total = limit_up + limit_down
    limit_score = 50 + (limit_up - limit_down) / limit_total * 50 if limit_total else 50.0
    core_count = int(safe_float(snapshot.get("core_index_count")) or 0)
    below_count = int(safe_float(snapshot.get("index_below_ma20_count")) or 0)
    index_score = 100 - below_count / core_count * 100 if core_count else 50.0
    trend_score = _clamp(50 + _mean([float(member["ret20"]) for member in members]) * 4)
    score = (
        breadth * 0.30
        + _clamp(50 + median_change * 20) * 0.20
        + limit_score * 0.15
        + index_score * 0.20
        + trend_score * 0.15
    )
    hard_stop = bool(
        core_count >= 3
        and below_count >= 2
        and down >= max(100, int(up * 1.5))
        and median_change <= -0.8
        and limit_down >= max(5, limit_up)
    )
    raw_state = "defensive" if hard_stop or score < 40 else ("offensive" if score >= 65 and breadth >= 55 else "rotation")
    previous = previous if isinstance(previous, dict) else {}
    prior_state = str(previous.get("state") or "")
    prior_raw = str(previous.get("raw_state") or prior_state)
    confirmation_count = int(previous.get("confirmation_count") or 0) + 1 if prior_raw == raw_state else 1
    if hard_stop:
        state = "defensive"
    elif prior_state == "defensive" and raw_state != "defensive":
        state = "recovery"
    elif confirmation_count >= 2 or not prior_state:
        state = raw_state
    else:
        state = prior_state
    risk_state = "defensive" if raw_state == "defensive" else state
    return {
        "score": round(score, 2),
        "raw_state": raw_state,
        "state": state,
        "confirmation_count": confirmation_count,
        "hard_stop": hard_stop,
        "allow_new_buys": raw_state != "defensive" and state != "defensive" and not hard_stop,
        "breadth_score": round(breadth, 2),
        "median_change_pct": round(median_change, 3),
        "limit_up": limit_up,
        "limit_down": limit_down,
        "risk_state": risk_state,
        **niuone_risk_budget(risk_state),
    }


def _theme_state(
    *,
    score: float,
    eligible: bool,
    strong_count: int,
    effective_count: float,
    previous: dict[str, Any],
    core_codes: list[str],
    as_of_date: str,
    previous_context_date: str,
    previous_trading_day: str,
) -> dict[str, Any]:
    if not eligible or score < 45:
        raw_state = "inactive"
    elif score < 55:
        raw_state = "fading"
    elif score >= 75 and strong_count >= 3 and effective_count >= 2.4:
        raw_state = "mainline"
    elif score >= 65 and strong_count >= 2 and effective_count >= 1.7:
        raw_state = "emerging"
    else:
        raw_state = "candidate"

    prior_state = str(previous.get("state") or "")
    prior_raw = str(previous.get("raw_state") or prior_state)
    prior_date = str(previous.get("as_of_date") or previous_context_date or "")[:10]
    same_day = bool(as_of_date and prior_date == as_of_date)
    consecutive_trading_day = bool(
        as_of_date
        and previous_trading_day
        and prior_date == previous_trading_day
        and prior_date != as_of_date
    )
    previous_core_codes = _theme_core_codes(previous)
    continued_core_codes = sorted(set(core_codes).intersection(previous_core_codes))
    core_overlap_count = len(continued_core_codes)
    overlap_base = min(len(core_codes), len(previous_core_codes))
    core_overlap_ratio = core_overlap_count / overlap_base if overlap_base else 0.0
    core_continuity_met = bool(
        consecutive_trading_day
        and core_overlap_count >= NIUONE_MIN_CROSS_DAY_CORE_OVERLAP
    )
    qualified_states = {"emerging", "mainline"}
    cross_day_persistent_now = bool(
        core_continuity_met
        and raw_state in qualified_states
        and prior_raw in qualified_states
    )
    prior_cross_day_persistent = bool(previous.get("cross_day_persistent"))
    cross_day_persistent = cross_day_persistent_now or bool(same_day and prior_cross_day_persistent)
    prior_mainline_confirmed = bool(
        previous.get("mainline_confirmed")
        or previous.get("cross_day_confirmed")
    )

    prior_confirmation = max(1, int(previous.get("confirmation_count") or 1))
    if raw_state in qualified_states:
        if same_day and prior_raw in qualified_states:
            confirmation = prior_confirmation
        elif cross_day_persistent_now:
            confirmation = prior_confirmation + 1
        else:
            confirmation = 1
    else:
        confirmation = 0
    intraday_confirmation = (
        int(previous.get("intraday_confirmation_count") or 1) + 1
        if same_day and prior_raw == raw_state
        else 1
    )

    if raw_state == "mainline":
        if cross_day_persistent_now or (same_day and prior_mainline_confirmed):
            state = "mainline"
        else:
            state = "emerging"
    elif raw_state == "emerging":
        if prior_mainline_confirmed and (same_day or consecutive_trading_day) and score >= 62:
            state = "diverging"
        else:
            state = "emerging"
    elif raw_state == "candidate" and prior_mainline_confirmed and (same_day or consecutive_trading_day):
        state = "diverging"
    elif (
        raw_state in {"fading", "inactive"}
        and prior_mainline_confirmed
        and (same_day or consecutive_trading_day)
        and score >= 45
    ):
        state = "fading"
    else:
        state = raw_state
    if same_day and prior_state == state:
        streak = max(1, int(previous.get("state_streak") or 1))
    elif consecutive_trading_day and prior_state == state:
        streak = max(1, int(previous.get("state_streak") or 1)) + 1
    else:
        streak = 1
    cross_day_confirmed = bool(
        state == "mainline"
        and (cross_day_persistent_now or (same_day and prior_mainline_confirmed))
    )
    mainline_confirmed = bool(
        cross_day_confirmed
        or (
            state in {"diverging", "fading"}
            and prior_mainline_confirmed
            and (same_day or core_continuity_met)
        )
    )
    intraday_state = "intraday_mainline" if raw_state == "mainline" and not cross_day_confirmed else raw_state
    return {
        "raw_state": raw_state,
        "state": state,
        "intraday_state": intraday_state,
        "confirmation_count": confirmation,
        "intraday_confirmation_count": intraday_confirmation,
        "state_streak": streak,
        "same_day_previous_scan": same_day,
        "consecutive_trading_day": consecutive_trading_day,
        "cross_day_persistent": cross_day_persistent,
        "cross_day_confirmed": cross_day_confirmed,
        "mainline_confirmed": mainline_confirmed,
        "previous_as_of_date": prior_date,
        "core_overlap_count": core_overlap_count,
        "core_overlap_ratio": round(core_overlap_ratio, 4),
        "core_continuity_met": core_continuity_met,
        "continued_core_codes": continued_core_codes,
    }


def build_niuone_context(
    prepared_items: list[dict[str, Any]],
    *,
    reference_pool_count: int | None = None,
    market_snapshot: dict[str, Any] | None = None,
    flow_rows: Any = None,
    previous_context: dict[str, Any] | None = None,
    dragon_tiger_snapshot: dict[str, Any] | None = None,
    news_snapshot: dict[str, Any] | None = None,
    as_of_date: str = "",
    previous_trading_day: str = "",
    sample_at: str = "",
    reuse_previous_external_context: bool = False,
) -> dict[str, Any]:
    """Build a market-mainline context without forcing a winner.

    Industry is the deterministic theme proxy. A future concept-tag provider can
    supply a richer mapping without changing the state or execution contracts.
    """
    members: list[dict[str, Any]] = []
    insufficient_history_count = 0
    invalid_metrics_count = 0
    for item in prepared_items:
        rows = item.get("rows") if isinstance(item.get("rows"), list) else []
        if len(rows) < NIUONE_MIN_ROWS:
            insufficient_history_count += 1
            continue
        metric = _member_metrics(item)
        if metric is None:
            invalid_metrics_count += 1
            continue
        members.append(metric)
    resolved_reference_pool_count = max(
        len(prepared_items),
        int(reference_pool_count or 0),
    )
    unavailable_kline_count = max(0, resolved_reference_pool_count - len(prepared_items))
    missing_industry_count = sum(1 for member in members if not member.get("industry"))
    previous_context = previous_context if isinstance(previous_context, dict) else {}
    as_of_date = str(as_of_date or "")[:10]
    previous_trading_day = str(previous_trading_day or "")[:10]
    sample_at = str(
        sample_at
        or (market_snapshot or {}).get("quote_time")
        or (market_snapshot or {}).get("captured_at")
        or ""
    )[:19]
    previous_context_date = str(previous_context.get("as_of_date") or "")[:10]
    market = _market_context(
        members,
        market_snapshot if isinstance(market_snapshot, dict) else {},
        previous_context.get("market") if isinstance(previous_context.get("market"), dict) else None,
    )
    ret5_population = [float(member["ret5"]) for member in members]
    ret20_population = [float(member["ret20"]) for member in members]
    volume_population = [float(member["volume_ratio"]) for member in members]
    amount_population = [float(member["amount"]) for member in members]
    sorted_ret5 = sorted(ret5_population)
    sorted_ret20 = sorted(ret20_population)
    sorted_volume = sorted(volume_population)
    sorted_amount = sorted(amount_population)
    for member in members:
        member["ret5_percentile"] = _percentile_from_sorted(float(member["ret5"]), sorted_ret5)
        member["ret20_percentile"] = _percentile_from_sorted(float(member["ret20"]), sorted_ret20)
        member["volume_percentile"] = _percentile_from_sorted(float(member["volume_ratio"]), sorted_volume)
        member["amount_percentile"] = _percentile_from_sorted(float(member["amount"]), sorted_amount)
        member["strong_score"] = _clamp(
            member["ret20_percentile"] * 0.30
            + member["ret5_percentile"] * 0.25
            + member["volume_percentile"] * 0.15
            + member["amount_percentile"] * 0.10
            + (100.0 if member["trend_aligned"] else (60.0 if member["above_ema20"] else 0.0)) * 0.10
            + (100.0 if member["new_high20"] else 0.0) * 0.10
        )
        member["strong"] = bool(
            member["strong_score"] >= NIUONE_STRONG_SCORE_THRESHOLD
            and (member["ret5"] > 0 or member["ret20"] > 0 or member["new_high20"])
        )

    dragon, dragon_stocks, news, news_stocks = _external_context(
        dragon_tiger_snapshot,
        news_snapshot,
        members,
    )
    if reuse_previous_external_context:
        if isinstance(previous_context.get("dragon_tiger"), Mapping):
            dragon = dict(previous_context["dragon_tiger"])
        if isinstance(previous_context.get("news"), Mapping):
            news = dict(previous_context["news"])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for member in members:
        if member["industry"]:
            grouped[str(member["industry"])].append(member)
    flows = _flow_map(flow_rows)
    flow_population = list(flows.values())
    theme_amounts = [sum(float(member["amount"]) for member in group) for group in grouped.values()]
    previous_themes = previous_context.get("themes") if isinstance(previous_context.get("themes"), dict) else {}
    themes: dict[str, dict[str, Any]] = {}
    stocks: dict[str, dict[str, Any]] = {}

    for industry, theme_members in grouped.items():
        today_metrics = _today_theme_metrics(theme_members)
        today_ranked_members = sorted(
            (member for member in theme_members if member.get("live_change_available")),
            key=lambda member: (float(member["change_pct"]), float(member["amount"])),
            reverse=True,
        )
        today_rank_by_code = {
            str(member.get("code") or ""): index
            for index, member in enumerate(today_ranked_members, start=1)
            if member.get("code")
        }
        strong_members = sorted(
            (member for member in theme_members if member["strong"]),
            key=lambda member: float(member["strong_score"]),
            reverse=True,
        )
        weights = [max(1.0, float(member["strong_score"])) * math.sqrt(max(1.0, float(member["amount"]))) for member in strong_members]
        weight_total = sum(weights)
        normalized = [weight / weight_total for weight in weights] if weight_total > 0 else []
        concentration = max(normalized) if normalized else 0.0
        effective_count = 1.0 / sum(weight * weight for weight in normalized) if normalized else 0.0
        effective_breadth_pct = _clamp(
            effective_count / len(theme_members) * 100 if theme_members else 0.0
        )
        strong_count = len(strong_members)
        core_codes = [str(member["code"]) for member in strong_members[:NIUONE_CORE_STOCK_LIMIT] if member.get("code")]
        strong_ratio = strong_count / len(theme_members) * 100 if theme_members else 0.0
        top_scores = [float(member["strong_score"]) for member in strong_members[:3]]
        strength_component = _mean(top_scores) * 0.25
        breadth_signal = _clamp(strong_ratio * 1.2) * 0.55 + _clamp(effective_count / 3 * 100) * 0.45
        breadth_component = breadth_signal * 0.20
        leadership_signal = (_mean(top_scores[:1]) * 0.45 + _mean(top_scores[1:3]) * 0.55) if top_scores else 0.0
        leadership_component = leadership_signal * 0.15
        amount = sum(float(member["amount"]) for member in theme_members)
        flow_value = _matched_flow(industry, flows)
        amount_percentile = _percentile(amount, theme_amounts)
        flow_score = _percentile(flow_value, flow_population) if flow_value is not None else amount_percentile
        capital_component = (flow_score * 0.65 + amount_percentile * 0.35) * 0.15
        previous = previous_themes.get(industry) if isinstance(previous_themes.get(industry), dict) else {}
        previous_score = safe_float(previous.get("score"))
        persistence_signal = 50.0
        if str(previous.get("state") or "") in {"mainline", "emerging"}:
            persistence_signal += 20.0
        if previous_score is not None:
            provisional = strength_component + breadth_component + leadership_component + capital_component
            persistence_signal += _clamp(provisional - previous_score, -20.0, 20.0)
        persistence_component = _clamp(persistence_signal) * 0.15
        dragon_values = [float((dragon_stocks.get(str(member["code"])) or {}).get("strength") or 0.0) for member in strong_members]
        news_values = [
            1.0 if (news_stocks.get(str(member["code"])) or {}).get("tone") == "positive"
            else -1.0 if (news_stocks.get(str(member["code"])) or {}).get("tone") == "negative"
            else 0.0
            for member in strong_members
        ]
        confirmation_signal = _clamp(50 + _mean(dragon_values) * 30 + _mean(news_values) * 20)
        confirmation_component = confirmation_signal * 0.10
        if reuse_previous_external_context:
            previous_confirmation = safe_float(previous.get("confirmation_component"))
            if previous_confirmation is not None:
                confirmation_component = _clamp(previous_confirmation, 0.0, 10.0)
        concentration_penalty = _clamp((concentration - 0.45) / 0.45 * 10, 0.0, 10.0)
        sample_penalty = 5.0 if len(theme_members) < NIUONE_MIN_THEME_MEMBERS else 0.0
        score = _clamp(
            strength_component
            + breadth_component
            + leadership_component
            + capital_component
            + persistence_component
            + confirmation_component
            - concentration_penalty
            - sample_penalty
        )
        eligible = len(theme_members) >= NIUONE_MIN_THEME_MEMBERS
        state_detail = _theme_state(
            score=score,
            eligible=eligible,
            strong_count=strong_count,
            effective_count=effective_count,
            previous=previous,
            core_codes=core_codes,
            as_of_date=as_of_date,
            previous_context_date=previous_context_date,
            previous_trading_day=previous_trading_day,
        )
        reversal_detail = _reversal_state(
            today_metrics=today_metrics,
            previous=previous,
            flow_value=flow_value,
            sample_at=sample_at,
            as_of_date=as_of_date,
        )
        raw_state = str(state_detail["raw_state"])
        state = str(state_detail["state"])
        themes[industry] = {
            "industry": industry,
            "theme_basis": "industry_proxy",
            "member_count": len(theme_members),
            **today_metrics,
            "eligible_data": eligible,
            "score": round(score, 2),
            "raw_state": raw_state,
            "state": state,
            "intraday_state": state_detail["intraday_state"],
            "confirmation_count": state_detail["confirmation_count"],
            "intraday_confirmation_count": state_detail["intraday_confirmation_count"],
            "state_streak": state_detail["state_streak"],
            "as_of_date": as_of_date,
            "sample_at": sample_at,
            "previous_as_of_date": state_detail["previous_as_of_date"],
            "same_day_previous_scan": state_detail["same_day_previous_scan"],
            "consecutive_trading_day": state_detail["consecutive_trading_day"],
            "cross_day_persistent": state_detail["cross_day_persistent"],
            "cross_day_confirmed": state_detail["cross_day_confirmed"],
            "mainline_confirmed": state_detail["mainline_confirmed"],
            "core_stock_codes": core_codes,
            "core_overlap_count": state_detail["core_overlap_count"],
            "core_overlap_ratio": state_detail["core_overlap_ratio"],
            "core_continuity_met": state_detail["core_continuity_met"],
            "continued_core_codes": state_detail["continued_core_codes"],
            "previous_score": safe_round(previous_score, 2),
            "score_change": safe_round(score - previous_score, 2) if previous_score is not None else None,
            "strong_stock_count": strong_count,
            "strong_stock_ratio": round(strong_ratio, 2),
            "effective_strong_count": round(effective_count, 2),
            "effective_breadth_pct": round(effective_breadth_pct, 2),
            "leader_concentration": round(concentration, 4),
            "single_stock_dominated": bool(strong_count == 1 or concentration > 0.70),
            "flow_net_yi": safe_round(flow_value, 2),
            "flow_source": "industry_net_flow" if flow_value is not None else "liquidity_fallback",
            **reversal_detail,
            "strength_component": round(strength_component, 2),
            "breadth_component": round(breadth_component, 2),
            "leadership_component": round(leadership_component, 2),
            "capital_component": round(capital_component, 2),
            "persistence_component": round(persistence_component, 2),
            "confirmation_component": round(confirmation_component, 2),
            "concentration_penalty": round(concentration_penalty, 2),
            "strong_stocks": [
                {
                    "code": member["code"],
                    "name": member["name"],
                    "strong_score": round(float(member["strong_score"]), 2),
                    "change_pct": round(float(member["change_pct"]), 2),
                    "role": "leader" if index == 0 else "core",
                    "leader_rank": index + 1,
                    "leader_tier": index < NIUONE_LEADER_TIER_LIMIT,
                }
                for index, member in enumerate(strong_members[:NIUONE_CORE_STOCK_LIMIT])
            ],
        }

        theme_ret5 = [float(member["ret5"]) for member in theme_members]
        theme_ret20 = [float(member["ret20"]) for member in theme_members]
        for rank_index, member in enumerate(sorted(theme_members, key=lambda item: float(item["strong_score"]), reverse=True), start=1):
            code = str(member["code"])
            today_rank = today_rank_by_code.get(code)
            today_rank_score = (
                100 - (today_rank - 1) / max(1, len(today_ranked_members) - 1) * 100
                if today_rank is not None
                else 0.0
            )
            reversal_strong = bool(
                today_rank is not None
                and today_rank <= NIUONE_LEADER_TIER_LIMIT
                and float(member.get("change_pct") or 0.0) >= 1.5
                and float(member.get("rebound_from_low_pct") or 0.0) >= NIUONE_REVERSAL_MIN_REBOUND_PCT
                and member.get("reclaim_previous_close") is True
            )
            dragon_stock = dragon_stocks.get(code) or {}
            news_stock = news_stocks.get(code) or {}
            role = "leader" if rank_index == 1 and member["strong"] else ("core" if member["strong"] else "follower")
            stocks[code] = {
                "industry": industry,
                "theme_state": state,
                "theme_score": round(score, 2),
                "strong_score": round(float(member["strong_score"]), 2),
                "strong": bool(member["strong"]),
                "role": role,
                "leader_rank": rank_index,
                "leader_tier": bool(member["strong"] and rank_index <= NIUONE_LEADER_TIER_LIMIT),
                "today_leader_rank": today_rank,
                "today_leader_tier": bool(
                    today_rank is not None and today_rank <= NIUONE_LEADER_TIER_LIMIT
                ),
                "today_rank_score": round(today_rank_score, 2),
                "reversal_strong": reversal_strong,
                "prior_ret5": safe_round(member.get("prior_ret5"), 2),
                "intraday_low": safe_round(member.get("intraday_low"), 3),
                "rebound_from_low_pct": safe_round(member.get("rebound_from_low_pct"), 2),
                "reclaim_previous_close": bool(member.get("reclaim_previous_close")),
                "theme_rank": round(100 - (rank_index - 1) / max(1, len(theme_members) - 1) * 100, 2),
                "theme_ret5_rank": round(_percentile(float(member["ret5"]), theme_ret5), 2),
                "theme_ret20_rank": round(_percentile(float(member["ret20"]), theme_ret20), 2),
                "market_rank": round(float(member["ret20_percentile"]), 2),
                "dragon_tiger_listed": bool(dragon_stock.get("listed")),
                "dragon_tiger_signal": dragon_stock.get("signal", "neutral"),
                "dragon_tiger_score": dragon_stock.get("score", 50.0),
                "dragon_tiger_adjustment": round(float(dragon_stock.get("strength") or 0.0) * 0.25, 3),
                "news_precheck": {
                    "code": code,
                    "name": member.get("name") or "",
                    "checked": bool(news_stock.get("checked")),
                    "available": bool(news_stock.get("available")),
                    "tone": news_stock.get("tone", "neutral"),
                    "tone_label": news_stock.get("tone_label", "中性"),
                    "summary": news_stock.get("summary", ""),
                    "fetched_at": news_stock.get("fetched_at", ""),
                    "window_days": news_stock.get("window_days", 3),
                    "error": news_stock.get("error", ""),
                },
                "news_adjustment": float(news_stock.get("adjustment") or 0.0),
            }

    ordered = sorted(themes.values(), key=lambda theme: float(theme["score"]), reverse=True)
    confirmed = [theme for theme in ordered if theme["state"] == "mainline"]
    intraday = [theme for theme in ordered if theme.get("intraday_state") == "intraday_mainline"]
    today_ordered = sorted(
        (theme for theme in themes.values() if theme.get("today_eligible_data")),
        key=lambda theme: (
            float(theme.get("today_strength_score") or 0.0),
            float(theme.get("today_median_change_pct") or 0.0),
        ),
        reverse=True,
    )
    today_primary = (
        today_ordered[0]
        if today_ordered
        and float(today_ordered[0].get("today_strength_score") or 0.0) >= NIUONE_TODAY_OBSERVATION_THRESHOLD
        else None
    )
    reversal_ordered = sorted(
        (theme for theme in themes.values() if theme.get("reversal_candidate")),
        key=lambda theme: float(theme.get("reversal_score") or 0.0),
        reverse=True,
    )
    reversal_primary = next(
        (theme for theme in reversal_ordered if theme.get("reversal_confirmed")),
        None,
    )
    primary = confirmed[0] if confirmed else None
    secondary = confirmed[1] if len(confirmed) > 1 and float(confirmed[0]["score"]) - float(confirmed[1]["score"]) <= 8 else None
    summary = {
        "mode": "dual" if secondary else ("single" if primary else "none"),
        "primary": primary["industry"] if primary else "",
        "primary_score": primary["score"] if primary else None,
        "secondary": secondary["industry"] if secondary else "",
        "secondary_score": secondary["score"] if secondary else None,
        "score_gap": round(float(ordered[0]["score"]) - float(ordered[1]["score"]), 2) if len(ordered) > 1 else None,
        "reason": "强势股形成多点共振" if primary else "尚无主题完成主线确认",
        "intraday_primary": intraday[0]["industry"] if intraday else "",
        "intraday_primary_score": intraday[0]["score"] if intraday else None,
        "observation_reason": (
            "日内强势仅作观察，等待下一交易日核心股延续"
            if intraday and not primary
            else ""
        ),
        "today_primary": today_primary["industry"] if today_primary else "",
        "today_primary_score": today_primary["today_strength_score"] if today_primary else None,
        "today_primary_breadth_pct": today_primary["today_breadth_pct"] if today_primary else None,
        "reversal_primary": reversal_primary["industry"] if reversal_primary else "",
        "reversal_primary_score": reversal_primary["reversal_score"] if reversal_primary else None,
        "reversal_confirmation_count": (
            reversal_primary["reversal_confirmation_count"] if reversal_primary else 0
        ),
        "today_observation_reason": (
            "V型反转已完成分时双确认，仅允许牛牛反转小仓试错"
            if reversal_primary
            else "V型反转正在等待间隔确认，不降低原有跨日主线门槛"
            if reversal_ordered
            else "今日强度仅作观察，不改变原有跨日主线确认门槛"
            if today_primary
            else ""
        ),
    }
    covered_count = len(stocks)
    uncovered_count = max(0, resolved_reference_pool_count - covered_count)
    coverage_reasons = [
        {
            "key": "kline_unavailable",
            "label": "K线不可用或少于30根",
            "count": unavailable_kline_count,
            "description": "行情请求失败、返回空数据，或可用日K少于30根",
        },
        {
            "key": "insufficient_history",
            "label": "历史不足55根",
            "count": insufficient_history_count,
            "description": "已有日K不少于30根，但未达到题材强度计算要求的55根",
        },
        {
            "key": "invalid_metrics",
            "label": "关键指标无效",
            "count": invalid_metrics_count,
            "description": "收盘价或5日、20日收益等关键输入无法形成有效指标",
        },
        {
            "key": "industry_unmapped",
            "label": "行业映射缺失",
            "count": missing_industry_count,
            "description": "强度指标有效，但没有可用于题材聚类的行业归属",
        },
    ]
    classified_uncovered_count = sum(int(reason["count"]) for reason in coverage_reasons)
    if classified_uncovered_count < uncovered_count:
        coverage_reasons.append({
            "key": "other",
            "label": "其他数据不完整",
            "count": uncovered_count - classified_uncovered_count,
            "description": "未归入已知数据质量分类",
        })
    return {
        "version": 5,
        "strategy": "niuone",
        "theme_basis": "industry_proxy",
        "as_of_date": as_of_date,
        "previous_trading_day": previous_trading_day,
        "sample_at": sample_at,
        "market": market,
        "mainline": summary,
        "theme_count": len(themes),
        "mapped_stock_count": covered_count,
        "strong_stock_count": sum(1 for member in members if member["strong"]),
        "data_coverage": (
            round(covered_count / resolved_reference_pool_count, 4)
            if resolved_reference_pool_count
            else 0.0
        ),
        "coverage_diagnostics": {
            "reference_pool_count": resolved_reference_pool_count,
            "prepared_stock_count": len(prepared_items),
            "covered_stock_count": covered_count,
            "uncovered_stock_count": uncovered_count,
            "reasons": coverage_reasons,
        },
        "dragon_tiger": dragon,
        "news": news,
        "themes": themes,
        "stocks": stocks,
    }


def _entry_metrics(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    if len(rows) < NIUONE_MIN_ROWS or not isinstance(context, dict):
        return None
    latest = rows[-1]
    code = _stock_code(latest.get("symbol_code"))
    industry = _industry_name(latest.get("industry"))
    theme = (context.get("themes") or {}).get(industry)
    stock = (context.get("stocks") or {}).get(code)
    market = context.get("market") if isinstance(context.get("market"), dict) else {}
    if not isinstance(theme, dict) or not isinstance(stock, dict):
        return None
    close = safe_float(latest.get("quote_price"))
    if close is None or close <= 0:
        close = safe_float(latest.get("close"))
    ema20 = safe_float(latest.get("ema20"))
    ema50 = safe_float(latest.get("ema50"))
    atr = _atr(rows)
    if close is None or close <= 0 or ema20 is None or ema50 is None or atr is None or atr <= 0:
        return None
    prior_ema20 = safe_float(rows[-2].get("ema20"))
    prior_close = safe_float(rows[-2].get("close")) or close
    prior_highs = [safe_float(row.get("high")) for row in rows[-21:-1]]
    highs = [value for value in prior_highs if value is not None and value > 0]
    current_volume = safe_float(latest.get("volume")) or 0.0
    prior_volumes = [safe_float(row.get("volume")) for row in rows[-21:-1]]
    volumes = [value for value in prior_volumes if value is not None and value > 0]
    volume_ratio = current_volume / _mean(volumes) if volumes else 1.0
    live_change = safe_float(latest.get("quote_change_pct"))
    change_pct = live_change if live_change is not None else (safe_float(latest.get("change_pct")) or 0.0)
    breakout = bool(highs and close >= max(highs) * 1.002 and 1.15 <= volume_ratio <= 2.5)
    recent_lows = [safe_float(row.get("low")) for row in rows[-4:]]
    lows = [value for value in recent_lows if value is not None and value > 0]
    pullback = bool(lows and min(lows) <= ema20 * 1.02 and close >= ema20 and volume_ratio <= 1.15 and change_pct >= -0.8)
    reclaim = bool(prior_close <= ema20 * 1.01 and close > ema20 and change_pct > 0 and volume_ratio >= 1.0)
    trend_aligned = bool(close >= ema20 >= ema50 and (prior_ema20 is None or ema20 >= prior_ema20))
    intraday_low = safe_float(latest.get("low"))
    if intraday_low is None or intraday_low <= 0:
        intraday_low = safe_float(stock.get("intraday_low"))
    rebound_from_low_pct = (
        (close / intraday_low - 1) * 100
        if intraday_low is not None and intraday_low > 0
        else 0.0
    )
    previous_close = safe_float(rows[-2].get("close")) if len(rows) >= 2 else None
    reclaim_previous_close = bool(
        previous_close is not None
        and previous_close > 0
        and close > previous_close
        and change_pct > 0
    )
    structure_low = min(lows) if lows else close - atr * 1.5
    stop_distance = structural_stop_distance_pct(close, structure_low)
    stop_atr = (close - structure_low) / atr
    regime = str(market.get("risk_state") or market.get("state") or "defensive")
    structural_limits = niuone_structural_stop_limits(regime)
    risk_ok = niuone_structure_risk_ok(stop_distance, stop_atr, regime)
    gap_buffer = downside_gap_buffer_pct(rows, atr=atr, close=close)
    effective_distance = effective_loss_distance_pct(
        close,
        structure_low,
        gap_buffer_pct=gap_buffer,
        execution_buffer_pct=SECTOR_TIDE_EXECUTION_BUFFER_PCT,
    )
    extension_atr = (close - ema20) / atr
    score_before_external = (float(theme["score"]) * 0.55 + float(stock["strong_score"]) * 0.45) / 10
    raw_external = float(stock.get("dragon_tiger_adjustment") or 0.0) + float(stock.get("news_adjustment") or 0.0)
    positive_suppressed = bool(raw_external > 0 and (change_pct > 7 or extension_atr > 1.5))
    external = 0.0 if positive_suppressed else _clamp(raw_external, -0.6, 0.4)
    return {
        "code": code,
        "industry": industry,
        "theme": theme,
        "stock": stock,
        "market": market,
        "mainline": context.get("mainline") if isinstance(context.get("mainline"), dict) else {},
        "dragon_tiger": context.get("dragon_tiger") if isinstance(context.get("dragon_tiger"), dict) else {},
        "news": context.get("news") if isinstance(context.get("news"), dict) else {},
        "close": close,
        "ema20": ema20,
        "ema50": ema50,
        "atr": atr,
        "atr_period": NIUONE_ATR_LOOKBACK,
        # Compatibility alias retained for historical candidate and position data.
        "atr20": atr,
        "distance_pct": (close / ema20 - 1) * 100,
        "extension_atr": extension_atr,
        "volume_ratio": volume_ratio,
        "change_pct": change_pct,
        "breakout": breakout,
        "pullback": pullback,
        "reclaim": reclaim,
        "intraday_low": intraday_low,
        "rebound_from_low_pct": rebound_from_low_pct,
        "reclaim_previous_close": reclaim_previous_close,
        "trend_aligned": trend_aligned,
        "stop_price": structure_low,
        "stop_distance_pct": stop_distance,
        "stop_atr": stop_atr,
        "max_stop_distance_pct": structural_limits["max_stop_distance_pct"],
        "max_stop_atr": structural_limits["max_stop_atr"],
        "gap_buffer_pct": gap_buffer,
        "effective_loss_distance_pct": effective_distance,
        "risk_ok": risk_ok,
        "score_before_external_context": score_before_external,
        "raw_external_context_adjustment": raw_external,
        "external_context_adjustment": external,
        "external_positive_suppressed": positive_suppressed,
        "composite_score": _clamp(score_before_external + external, 0.0, 10.0),
    }


def _payload(
    strategy_name: str,
    metrics: dict[str, Any],
    *,
    verdict: str,
    risk_flags: list[str],
) -> dict[str, Any]:
    theme = metrics["theme"]
    stock = metrics["stock"]
    market = metrics["market"]
    mainline = metrics["mainline"]
    selected_mainlines = {
        str(mainline.get("primary") or ""),
        str(mainline.get("secondary") or ""),
    }
    regime = str(market.get("risk_state") or market.get("state") or "")
    budget = niuone_risk_budget(regime, strategy_name)
    chase_limits = niuone_chase_limits(
        strategy_name,
        str(market.get("risk_state") or market.get("state") or ""),
    )
    absolute_cap = NIUONE_ABSOLUTE_POSITION_CAP_PCT[strategy_name]
    reversal_probe = strategy_name == "niu_reversal_probe"
    stop_price = metrics["intraday_low"] if reversal_probe else metrics["stop_price"]
    stop_distance_pct = structural_stop_distance_pct(metrics["close"], stop_price)
    stop_atr = (
        (metrics["close"] - stop_price) / metrics["atr"]
        if stop_price is not None and stop_price > 0 and metrics["atr"] > 0
        else 0.0
    )
    structural_limits = niuone_structural_stop_limits(regime, strategy_name)
    risk_ok = niuone_structure_risk_ok(
        stop_distance_pct,
        stop_atr,
        regime,
        strategy_name,
    )
    effective_loss_distance = effective_loss_distance_pct(
        metrics["close"],
        stop_price,
        gap_buffer_pct=metrics["gap_buffer_pct"],
        execution_buffer_pct=SECTOR_TIDE_EXECUTION_BUFFER_PCT,
    )
    dynamic_cap = risk_sized_position_cap_pct(
        per_trade_risk_pct=budget["per_trade_risk_pct"],
        effective_loss_distance_pct_value=effective_loss_distance,
        absolute_cap_pct=absolute_cap,
    )
    news_precheck = stock.get("news_precheck") if isinstance(stock.get("news_precheck"), dict) else {}
    return {
        "score": metrics.get("strategy_score", metrics["composite_score"]),
        "score_total": 10,
        "verdict": verdict,
        "industry": metrics["industry"],
        "theme_basis": "industry_proxy",
        "mainline_state": theme.get("state"),
        "mainline_raw_state": theme.get("raw_state"),
        "mainline_intraday_state": theme.get("intraday_state"),
        "mainline_score": theme.get("score"),
        "mainline_mode": mainline.get("mode", "none"),
        "mainline_primary": mainline.get("primary", ""),
        "mainline_secondary": mainline.get("secondary", ""),
        "mainline_selected": metrics["industry"] in selected_mainlines,
        "sector_status": theme.get("state"),
        "sector_score": theme.get("score"),
        "sector_member_count": theme.get("member_count"),
        "sector_data_eligible": bool(theme.get("eligible_data")),
        "strong_stock_count": theme.get("strong_stock_count"),
        "effective_strong_count": theme.get("effective_strong_count"),
        "leader_concentration": theme.get("leader_concentration"),
        "single_stock_dominated": bool(theme.get("single_stock_dominated")),
        "mainline_confirmation_count": theme.get("confirmation_count"),
        "mainline_intraday_confirmation_count": theme.get("intraday_confirmation_count"),
        "mainline_cross_day_persistent": bool(theme.get("cross_day_persistent")),
        "mainline_cross_day_confirmed": bool(theme.get("cross_day_confirmed")),
        "mainline_confirmed": bool(theme.get("mainline_confirmed")),
        "mainline_core_overlap_count": theme.get("core_overlap_count"),
        "mainline_core_overlap_ratio": theme.get("core_overlap_ratio"),
        "mainline_continued_core_codes": list(theme.get("continued_core_codes") or []),
        "mainline_as_of_date": theme.get("as_of_date"),
        "mainline_previous_as_of_date": theme.get("previous_as_of_date"),
        "mainline_state_streak": theme.get("state_streak"),
        "mainline_score_change": theme.get("score_change"),
        "today_eligible_data": bool(theme.get("today_eligible_data")),
        "today_up_count": theme.get("today_up_count"),
        "today_1_5pct_count": theme.get("today_1_5pct_count"),
        "today_breadth_pct": theme.get("today_breadth_pct"),
        "today_median_change_pct": theme.get("today_median_change_pct"),
        "today_median_rebound_pct": theme.get("today_median_rebound_pct"),
        "today_prior_median_ret5_pct": theme.get("today_prior_median_ret5_pct"),
        "today_strength_score": theme.get("today_strength_score"),
        "today_leadership_score": theme.get("today_leadership_score"),
        "reversal_candidate": bool(theme.get("reversal_candidate")),
        "reversal_confirmed": bool(theme.get("reversal_confirmed")),
        "reversal_confirmation_count": theme.get("reversal_confirmation_count"),
        "reversal_min_sample_gap_minutes": theme.get("reversal_min_sample_gap_minutes"),
        "reversal_sample_gap_minutes": theme.get("reversal_sample_gap_minutes"),
        "reversal_origin_weak": bool(theme.get("reversal_origin_weak")),
        "reversal_quote_coverage_ok": bool(theme.get("reversal_quote_coverage_ok")),
        "reversal_flow_available": bool(theme.get("reversal_flow_available")),
        "reversal_flow_positive": bool(theme.get("reversal_flow_positive")),
        "reversal_flow_flip": bool(theme.get("reversal_flow_flip")),
        "reversal_flow_improving": bool(theme.get("reversal_flow_improving")),
        "reversal_score": theme.get("reversal_score"),
        "market_regime": market.get("state"),
        "market_score": market.get("score"),
        "market_hard_stop": bool(market.get("hard_stop")),
        "market_allows_buys": bool(market.get("allow_new_buys")),
        "stock_role": stock.get("role"),
        "stock_leader_rank": stock.get("leader_rank"),
        "stock_leader_tier": bool(stock.get("leader_tier")),
        "stock_strong": bool(stock.get("strong")),
        "stock_strong_score": stock.get("strong_score"),
        "stock_reversal_leader_rank": stock.get("today_leader_rank"),
        "stock_reversal_leader_tier": bool(stock.get("today_leader_tier")),
        "stock_reversal_strong": bool(stock.get("reversal_strong")),
        "stock_today_rank_score": stock.get("today_rank_score"),
        "stock_sector_rank": stock.get("theme_rank"),
        "stock_market_rank": stock.get("market_rank"),
        "score_before_external_context": safe_round(metrics["score_before_external_context"], 3),
        "raw_external_context_adjustment": safe_round(metrics["raw_external_context_adjustment"], 3),
        "external_context_adjustment": safe_round(metrics["external_context_adjustment"], 3),
        "external_positive_suppressed": metrics["external_positive_suppressed"],
        "dragon_tiger_available": bool(metrics["dragon_tiger"].get("available")),
        "dragon_tiger_as_of_date": metrics["dragon_tiger"].get("as_of_date"),
        "dragon_tiger_listed": bool(stock.get("dragon_tiger_listed")),
        "dragon_tiger_signal": stock.get("dragon_tiger_signal", "neutral"),
        "dragon_tiger_score": stock.get("dragon_tiger_score", 50.0),
        "dragon_tiger_adjustment": stock.get("dragon_tiger_adjustment", 0.0),
        "news_precheck_configured": bool(metrics["news"].get("configured")),
        "news_precheck": dict(news_precheck),
        "news_checked": bool(news_precheck.get("checked")),
        "news_available": bool(news_precheck.get("available")),
        "news_tone": news_precheck.get("tone", "neutral"),
        "news_tone_label": news_precheck.get("tone_label", "中性"),
        "news_summary": news_precheck.get("summary", ""),
        "news_fetched_at": news_precheck.get("fetched_at", ""),
        "news_adjustment": stock.get("news_adjustment", 0.0),
        "ema20": safe_round(metrics["ema20"], 3),
        "ema50": safe_round(metrics["ema50"], 3),
        "atr": safe_round(metrics["atr"], 3),
        "atr_period": metrics["atr_period"],
        "atr20": safe_round(metrics["atr20"], 3),
        "distance_pct": safe_round(metrics["distance_pct"], 2),
        "extension_atr": safe_round(metrics["extension_atr"], 2),
        "volume_ratio": safe_round(metrics["volume_ratio"], 2),
        "change_pct": safe_round(metrics["change_pct"], 2),
        "trend_aligned": metrics["trend_aligned"],
        "breakout": metrics["breakout"],
        "pullback": metrics["pullback"],
        "reclaim": metrics["reclaim"],
        "reclaim_previous_close": metrics["reclaim_previous_close"],
        "intraday_low": safe_round(metrics["intraday_low"], 3),
        "rebound_from_low_pct": safe_round(metrics["rebound_from_low_pct"], 2),
        "stop_price": safe_round(stop_price, 3),
        "stop_source": "niu_reversal_low" if reversal_probe else "niu_structure_low",
        "stop_distance_pct": safe_round(stop_distance_pct, 2),
        "stop_atr": safe_round(stop_atr, 2),
        "max_stop_distance_pct": structural_limits["max_stop_distance_pct"],
        "max_stop_atr": structural_limits["max_stop_atr"],
        "max_entry_change_pct": chase_limits["max_entry_change_pct"],
        "max_entry_extension_atr": chase_limits["max_entry_extension_atr"],
        "gap_buffer_pct": safe_round(metrics["gap_buffer_pct"], 3),
        "execution_buffer_pct": SECTOR_TIDE_EXECUTION_BUFFER_PCT,
        "effective_loss_distance_pct": safe_round(effective_loss_distance, 3),
        "per_trade_risk_budget_pct": budget["per_trade_risk_pct"],
        "max_open_risk_pct": budget["max_open_risk_pct"],
        "max_sector_risk_pct": budget["max_sector_risk_pct"],
        "max_total_position_pct": budget["max_total_position_pct"],
        "max_sector_position_pct": budget["max_sector_position_pct"],
        "absolute_position_cap_pct": absolute_cap,
        "max_position_pct_by_risk": dynamic_cap,
        "risk_ok": risk_ok,
        "risk_flags": risk_flags,
        "recent_close": safe_round(metrics["close"], 3),
    }


def _common_risks(
    metrics: dict[str, Any],
    *,
    require_mature_leader: bool = True,
) -> list[str]:
    risks: list[str] = []
    if not metrics["risk_ok"]:
        risks.append(
            "结构止损超过当前行情上限"
            f"({metrics['max_stop_distance_pct']:g}%或{metrics['max_stop_atr']:g}ATR)"
        )
    if require_mature_leader and metrics["theme"].get("single_stock_dominated"):
        risks.append("主题由单只强股主导")
    if require_mature_leader and (
        metrics["stock"].get("leader_tier") is not True
        or metrics["stock"].get("strong") is not True
    ):
        risks.append("个股未进入强势行业龙头梯队")
    news = metrics["stock"].get("news_precheck") or {}
    if news.get("available") and news.get("tone") == "negative":
        risks.append("近3日个股消息面偏利空")
    return risks


def _reversal_strategy_score(metrics: dict[str, Any]) -> float:
    theme = metrics["theme"]
    stock = metrics["stock"]
    change_quality = _clamp(float(metrics["change_pct"]) / 4.0 * 100)
    rebound_quality = _clamp(float(metrics["rebound_from_low_pct"]) / 3.0 * 100)
    stock_score = _clamp(
        float(stock.get("today_rank_score") or 0.0) * 0.35
        + change_quality * 0.25
        + rebound_quality * 0.20
        + (100.0 if metrics["reclaim_previous_close"] else 0.0) * 0.20
    )
    base_score = (
        float(theme.get("reversal_score") or 0.0) * 0.60
        + stock_score * 0.40
    ) / 10.0
    raw_external = float(metrics.get("raw_external_context_adjustment") or 0.0)
    positive_suppressed = bool(
        raw_external > 0
        and (metrics["change_pct"] > 5 or metrics["extension_atr"] > 1.0)
    )
    external = 0.0 if positive_suppressed else _clamp(raw_external, -0.6, 0.4)
    metrics["external_context_adjustment"] = external
    metrics["external_positive_suppressed"] = positive_suppressed
    metrics["reversal_stock_score"] = stock_score
    return _clamp(base_score + external, 0.0, 10.0)


def score_niu_leader(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context)
    if metrics is None:
        return None
    risks = _common_risks(metrics)
    if metrics["theme"].get("state") != "mainline":
        risks.append("主题尚未确认为市场主线")
    if not metrics["theme"].get("cross_day_confirmed"):
        risks.append("主线未完成跨交易日核心股延续确认")
    if not (metrics["breakout"] or metrics["pullback"]):
        risks.append("未形成突破或首次缩量回踩")
    if metrics["change_pct"] > 4 or metrics["extension_atr"] > 1.0:
        risks.append("领航买点偏扩张，已按行情弹性上限复核")
    verdict = "高匹配牛牛领航" if metrics["composite_score"] >= 8 else ("观察牛牛领航" if metrics["composite_score"] >= 6.5 else "不匹配")
    return with_strategy_profile("niu_leader", _payload("niu_leader", metrics, verdict=verdict, risk_flags=risks))


def score_niu_pullback(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context)
    if metrics is None:
        return None
    risks = _common_risks(metrics)
    if metrics["theme"].get("state") not in {"mainline", "diverging"} or float(metrics["theme"].get("score") or 0) < 70:
        risks.append("主线强度不足以参与分歧")
    if not metrics["theme"].get("mainline_confirmed"):
        risks.append("主题没有有效的跨交易日主线确认记录")
    if not (metrics["pullback"] or metrics["reclaim"]):
        risks.append("未出现EMA20承接或收复买点")
    if metrics["change_pct"] > 4 or metrics["extension_atr"] > 1.0:
        risks.append("回踩买点偏扩张，已按行情弹性上限复核")
    verdict = "高匹配牛牛回踩" if metrics["composite_score"] >= 8.2 else ("观察牛牛回踩" if metrics["composite_score"] >= 6.5 else "不匹配")
    return with_strategy_profile("niu_pullback", _payload("niu_pullback", metrics, verdict=verdict, risk_flags=risks))


def score_niu_emerging(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context)
    if metrics is None:
        return None
    risks = _common_risks(metrics)
    if metrics["theme"].get("state") != "emerging":
        risks.append("主题不是待确认的新主线")
    if not metrics["theme"].get("cross_day_persistent"):
        risks.append("启动主题尚未跨交易日延续")
    if int(metrics["theme"].get("strong_stock_count") or 0) < 2:
        risks.append("少于两只强势股共同确认")
    if not (metrics["breakout"] or metrics["reclaim"]):
        risks.append("启动买点尚未确认")
    if metrics["change_pct"] > 7 or metrics["extension_atr"] > 1.5:
        risks.append("启动战法拒绝追高")
    verdict = "高匹配牛牛启动" if metrics["composite_score"] >= 8.4 else ("观察牛牛启动" if metrics["composite_score"] >= 6.5 else "不匹配")
    return with_strategy_profile("niu_emerging", _payload("niu_emerging", metrics, verdict=verdict, risk_flags=risks))


def score_niu_reversal_probe(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context)
    if metrics is None:
        return None
    metrics = dict(metrics)
    metrics["strategy_score"] = _reversal_strategy_score(metrics)
    risks = _common_risks(metrics, require_mature_leader=False)
    theme = metrics["theme"]
    stock = metrics["stock"]
    if not theme.get("reversal_candidate"):
        risks.append("题材尚未形成广度型V型反转")
    elif not theme.get("reversal_confirmed"):
        risks.append("V型反转尚未完成分时间隔确认")
    if stock.get("reversal_strong") is not True:
        risks.append("个股未进入反转领涨前三并完成低点回升")
    if not metrics["reclaim_previous_close"]:
        risks.append("个股尚未收复昨收")
    if metrics["change_pct"] > 5 or metrics["extension_atr"] > 1.0:
        risks.append("反转试仓拒绝追高")
    score = float(metrics["strategy_score"])
    verdict = "高匹配牛牛反转" if score >= 8.4 else ("观察牛牛反转" if score >= 7.0 else "不匹配")
    payload = _payload("niu_reversal_probe", metrics, verdict=verdict, risk_flags=risks)
    payload["reversal_stock_score"] = safe_round(metrics.get("reversal_stock_score"), 2)
    return with_strategy_profile("niu_reversal_probe", payload)


score_niu_leader.requires_context = True  # type: ignore[attr-defined]
score_niu_pullback.requires_context = True  # type: ignore[attr-defined]
score_niu_emerging.requires_context = True  # type: ignore[attr-defined]
score_niu_reversal_probe.requires_context = True  # type: ignore[attr-defined]
