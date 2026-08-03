"""牛牛战法: infer market mainlines from cross-sectional strong-stock resonance."""
from __future__ import annotations

import math
import re
import statistics
from bisect import bisect_left, bisect_right
from collections import defaultdict
from typing import Any, Mapping

from ..niuone_risk import (
    NIUONE_ABSOLUTE_POSITION_CAP_PCT,
    NIUONE_MARKUP_MOMENTUM_PROBE_MAX_ENTRY_EXTENSION_ATR,
    NIUONE_MARKUP_MOMENTUM_PROBE_MIN_SCORE,
    NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_SCORE,
    NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
    NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
    niuone_chase_limits,
    niuone_markup_momentum_probe_eligible,
    niuone_markup_momentum_probe_is_acceleration,
    niuone_risk_budget,
    niuone_structure_risk_ok,
    niuone_structural_stop_limits,
)
from ..policy import (
    NIUONE_DAILY_V_MIN_RECOVERY_RATIO,
    NIUONE_TODAY_OBSERVATION_THRESHOLD,
)
from ..lifecycle import (
    niuone_lifecycle_entry_blocker,
    niuone_lifecycle_metadata,
)
from ..sector_tide_risk import (
    SECTOR_TIDE_EXECUTION_BUFFER_PCT,
    downside_gap_buffer_pct,
    effective_loss_distance_pct,
    risk_sized_position_cap_pct,
    structural_stop_distance_pct,
)
from .common import (
    niu_emerging_theme_eligible,
    safe_float,
    safe_round,
    with_strategy_profile,
)


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
NIUONE_REVERSAL_MIN_REBOUND_PCT = 1.5
NIUONE_DAILY_V_LOOKBACK = 30
NIUONE_DAILY_V_LEFT_LOOKBACK = 15
NIUONE_DAILY_V_MIN_LEFT_DAYS = 5
NIUONE_DAILY_V_MIN_RIGHT_DAYS = 3
NIUONE_DAILY_V_MAX_RIGHT_DAYS = 15
NIUONE_DAILY_V_MIN_DECLINE_PCT = 8.0
NIUONE_DAILY_V_MIN_REBOUND_PCT = 6.0
NIUONE_DAILY_V_MIN_RISING_RATIO = 2 / 3


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


def _theme_names(value: Any, *, fallback: Any = "") -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        values = ()
    labels = tuple(dict.fromkeys(
        label for item in values if (label := _industry_name(item))
    ))
    if labels:
        return labels
    fallback_label = _industry_name(fallback)
    return (fallback_label,) if fallback_label else ()


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


def _daily_v_reversal_metrics(
    rows: list[dict[str, Any]],
    *,
    current_close: float | None = None,
) -> dict[str, Any]:
    """Detect a multi-session V using only information visible in daily bars."""
    defaults = {
        "daily_v_reversal": False,
        "daily_v_left_peak_date": "",
        "daily_v_trough_date": "",
        "daily_v_left_days": 0,
        "daily_v_right_days": 0,
        "daily_v_decline_pct": 0.0,
        "daily_v_rebound_pct": 0.0,
        "daily_v_recovery_ratio": 0.0,
        "daily_v_rising_ratio": 0.0,
        "daily_v_right_trend_confirmed": False,
        "daily_v_pattern_score": 0.0,
        "daily_v_stop_price": None,
    }
    window = rows[-(NIUONE_DAILY_V_LOOKBACK + 1):]
    if len(window) < NIUONE_DAILY_V_MIN_LEFT_DAYS + NIUONE_DAILY_V_MIN_RIGHT_DAYS + 1:
        return defaults

    closes: list[float] = []
    lows: list[float] = []
    for row in window:
        close = safe_float(row.get("close"))
        low = safe_float(row.get("low"))
        if close is None or close <= 0:
            return defaults
        closes.append(close)
        lows.append(low if low is not None and low > 0 else close)
    if current_close is not None and current_close > 0:
        closes[-1] = current_close

    latest_index = len(window) - 1
    first_trough = NIUONE_DAILY_V_MIN_LEFT_DAYS
    last_trough = latest_index - NIUONE_DAILY_V_MIN_RIGHT_DAYS
    first_trough = max(first_trough, latest_index - NIUONE_DAILY_V_MAX_RIGHT_DAYS)
    if first_trough > last_trough:
        return defaults
    trough_index = min(
        range(first_trough, last_trough + 1),
        key=lambda index: closes[index],
    )
    left_start = max(0, trough_index - NIUONE_DAILY_V_LEFT_LOOKBACK)
    left_candidates = range(left_start, trough_index)
    if not left_candidates:
        return defaults
    left_peak_index = max(left_candidates, key=lambda index: closes[index])
    left_peak = closes[left_peak_index]
    trough = closes[trough_index]
    current = closes[-1]
    if left_peak <= trough or current <= trough:
        return defaults

    left_days = trough_index - left_peak_index
    right_days = latest_index - trough_index
    decline_pct = (left_peak / trough - 1.0) * 100.0
    rebound_pct = (current / trough - 1.0) * 100.0
    recovery_ratio = (current - trough) / (left_peak - trough)
    right_steps = [
        closes[index] > closes[index - 1]
        for index in range(trough_index + 1, latest_index + 1)
    ]
    rising_ratio = sum(right_steps) / len(right_steps) if right_steps else 0.0
    recent_right = closes[max(trough_index + 1, latest_index - 4):]
    right_trend_confirmed = bool(
        right_steps
        and rising_ratio >= NIUONE_DAILY_V_MIN_RISING_RATIO
        and current >= max(closes[max(trough_index + 1, latest_index - 2):])
        and current >= _mean(recent_right, current)
    )
    pattern_score = _clamp(
        _clamp(decline_pct / 12.0 * 100.0) * 0.20
        + _clamp(rebound_pct / 10.0 * 100.0) * 0.30
        + _clamp(recovery_ratio / 0.80 * 100.0) * 0.30
        + rising_ratio * 100.0 * 0.20
    )
    stop_start = max(trough_index + 1, latest_index - 2)
    stop_price = min(lows[stop_start:]) if lows[stop_start:] else trough
    matched = bool(
        left_days >= NIUONE_DAILY_V_MIN_LEFT_DAYS
        and NIUONE_DAILY_V_MIN_RIGHT_DAYS <= right_days <= NIUONE_DAILY_V_MAX_RIGHT_DAYS
        and decline_pct >= NIUONE_DAILY_V_MIN_DECLINE_PCT
        and rebound_pct >= NIUONE_DAILY_V_MIN_REBOUND_PCT
        and recovery_ratio >= NIUONE_DAILY_V_MIN_RECOVERY_RATIO
        and right_trend_confirmed
    )
    return {
        "daily_v_reversal": matched,
        "daily_v_left_peak_date": str(window[left_peak_index].get("date") or "")[:10],
        "daily_v_trough_date": str(window[trough_index].get("date") or "")[:10],
        "daily_v_left_days": left_days,
        "daily_v_right_days": right_days,
        "daily_v_decline_pct": round(decline_pct, 2),
        "daily_v_rebound_pct": round(rebound_pct, 2),
        "daily_v_recovery_ratio": round(recovery_ratio, 4),
        "daily_v_rising_ratio": round(rising_ratio, 4),
        "daily_v_right_trend_confirmed": right_trend_confirmed,
        "daily_v_pattern_score": round(pattern_score, 2),
        "daily_v_stop_price": stop_price,
    }


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
    industry = _industry_name(item.get("industry") or latest.get("industry"))
    themes = _theme_names(
        item.get("themes") or latest.get("themes"),
        fallback=industry,
    )
    return {
        "code": _stock_code(item.get("code") or latest.get("symbol_code")),
        "name": str(item.get("name") or latest.get("stock_name") or ""),
        "industry": industry,
        "themes": themes,
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
        "today_leaders": [
            {
                "code": member["code"],
                "name": member["name"],
                "strong_score": round(float(member["strong_score"]), 2),
                "change_pct": round(float(member["change_pct"]), 2),
                "role": "today_leader" if index == 0 else "today_core",
            }
            for index, member in enumerate(leaders)
        ],
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
        "allow_new_buys": not hard_stop,
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
    theme_basis: str = "industry_proxy",
) -> dict[str, Any]:
    """Build a market-mainline context without forcing a winner.

    A member may belong to multiple themes. ``industry_proxy`` is
    retained for legacy callers; production supplies Eastmoney concept labels.
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
    resolved_theme_basis = str(theme_basis or "industry_proxy").strip() or "industry_proxy"
    missing_theme_count = sum(1 for member in members if not member.get("themes"))
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
        for theme_name in member.get("themes") or ():
            grouped[str(theme_name)].append(member)
    flows = _flow_map(flow_rows)
    flow_population = list(flows.values())
    theme_amounts = [sum(float(member["amount"]) for member in group) for group in grouped.values()]
    previous_themes = previous_context.get("themes") if isinstance(previous_context.get("themes"), dict) else {}
    themes: dict[str, dict[str, Any]] = {}
    stock_profiles: dict[str, list[dict[str, Any]]] = defaultdict(list)

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
        raw_state = str(state_detail["raw_state"])
        state = str(state_detail["state"])
        lifecycle = niuone_lifecycle_metadata(
            {
                "state": state,
                "score": score,
                "cross_day_persistent": state_detail[
                    "cross_day_persistent"
                ],
                "cross_day_confirmed": state_detail[
                    "cross_day_confirmed"
                ],
                "mainline_confirmed": state_detail[
                    "mainline_confirmed"
                ],
            },
            previous=previous,
        )
        themes[industry] = {
            "industry": industry,
            "theme_basis": resolved_theme_basis,
            "member_count": len(theme_members),
            **today_metrics,
            "eligible_data": eligible,
            "score": round(score, 2),
            "raw_state": raw_state,
            "state": state,
            **lifecycle,
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

        theme_ret5 = sorted(float(member["ret5"]) for member in theme_members)
        theme_ret20 = sorted(float(member["ret20"]) for member in theme_members)
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
            stock_profiles[code].append({
                "industry": industry,
                "theme_basis": resolved_theme_basis,
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
                "theme_ret5_rank": round(
                    _percentile_from_sorted(float(member["ret5"]), theme_ret5), 2
                ),
                "theme_ret20_rank": round(
                    _percentile_from_sorted(float(member["ret20"]), theme_ret20), 2
                ),
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
            })

    state_priority = {
        "mainline": 4,
        "emerging": 3,
        "candidate": 2,
        "diverging": 1,
        "fading": 0,
    }
    stocks: dict[str, dict[str, Any]] = {}
    for code, profiles in stock_profiles.items():
        ordered_profiles = sorted(
            profiles,
            key=lambda profile: (
                -state_priority.get(str(profile.get("theme_state") or ""), -1),
                -float(profile.get("theme_score") or 0.0),
                -float(profile.get("strong_score") or 0.0),
                -float(profile.get("theme_rank") or 0.0),
                str(profile.get("industry") or ""),
            ),
        )
        selected = dict(ordered_profiles[0])
        selected["theme_memberships"] = [
            str(profile.get("industry") or "")
            for profile in ordered_profiles
            if str(profile.get("industry") or "")
        ]
        # A stock may lead one concept while being only a follower in another.
        # Keep the compact per-theme routing fields so every NiuOne action can
        # choose a lifecycle-compatible branch instead of inheriting one global
        # profile selected before the action is known.  External/news fields are
        # intentionally not duplicated for every membership.
        selected["theme_profiles"] = [
            {
                key: profile.get(key)
                for key in (
                    "industry",
                    "theme_basis",
                    "role",
                    "leader_rank",
                    "leader_tier",
                    "today_leader_rank",
                    "today_leader_tier",
                    "today_rank_score",
                    "reversal_strong",
                    "theme_rank",
                    "theme_ret5_rank",
                    "theme_ret20_rank",
                )
            }
            for profile in ordered_profiles
        ]
        stocks[code] = selected

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
        "today_observation_reason": (
            "今日强度仅作观察，不改变原有跨日主线确认门槛"
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
            "label": "题材映射缺失",
            "count": missing_theme_count,
            "description": "强度指标有效，但没有可用于题材聚类的东方财富概念或行业归属",
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
        "version": 7,
        "strategy": "niuone",
        "theme_basis": resolved_theme_basis,
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


def _action_theme_profile(
    stock: Mapping[str, Any],
    context: Mapping[str, Any],
    strategy_name: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Choose the best concept membership that matches an entry action.

    The old global stock profile preferred the most mature membership before
    the action was known.  For multi-concept names that could route a startup
    leader through an unrelated mature theme where it was only a follower.
    Selection is now action-aware while retaining a deterministic fallback so
    blocked candidates still expose useful diagnostics.
    """
    themes = context.get("themes") if isinstance(context.get("themes"), Mapping) else {}
    raw_profiles = stock.get("theme_profiles")
    profiles = (
        [dict(item) for item in raw_profiles if isinstance(item, Mapping)]
        if isinstance(raw_profiles, list)
        else []
    )
    if not profiles:
        profiles = [dict(stock)]

    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for profile in profiles:
        industry = _industry_name(profile.get("industry"))
        theme = themes.get(industry) if isinstance(themes, Mapping) else None
        if not industry or not isinstance(theme, Mapping):
            continue
        merged = dict(stock)
        merged.update(profile)
        candidates.append((merged, dict(theme)))
    if not candidates:
        return None

    def action_compatible(item: tuple[dict[str, Any], dict[str, Any]]) -> bool:
        profile, theme = item
        if niuone_lifecycle_entry_blocker(strategy_name, theme) is not None:
            return False
        state = str(theme.get("state") or "")
        confirmed = bool(
            theme.get("mainline_confirmed")
            or theme.get("cross_day_confirmed")
        )
        if strategy_name == "niu_reversal_probe":
            return bool(
                state in {"candidate", "emerging"}
                and not confirmed
                and not (state == "candidate" and profile.get("strong") is True)
            )
        if strategy_name == "niu_emerging":
            return bool(
                state == "emerging"
                and theme.get("cross_day_persistent") is True
            )
        if strategy_name in {"niu_leader", "niu_pullback"}:
            return bool(state in {"mainline", "diverging"} and confirmed)
        return True

    compatible = [item for item in candidates if action_compatible(item)]
    routed = compatible or candidates
    state_priority = {
        "mainline": 5,
        "diverging": 4,
        "emerging": 3,
        "candidate": 2,
        "fading": 1,
        "inactive": 0,
    }
    routed.sort(
        key=lambda item: (
            -int(item[0].get("leader_tier") is True),
            -float(item[0].get("theme_rank") or 0.0),
            -state_priority.get(str(item[1].get("state") or ""), -1),
            -float(item[1].get("score") or 0.0),
            str(item[0].get("industry") or ""),
        )
    )
    return routed[0]


def _entry_metrics(
    rows: list[dict[str, Any]],
    context: dict[str, Any],
    strategy_name: str,
) -> dict[str, Any] | None:
    if len(rows) < NIUONE_MIN_ROWS or not isinstance(context, dict):
        return None
    latest = rows[-1]
    code = _stock_code(latest.get("symbol_code"))
    source_stock = (context.get("stocks") or {}).get(code)
    routed = (
        _action_theme_profile(source_stock, context, strategy_name)
        if isinstance(source_stock, Mapping)
        else None
    )
    if routed is not None:
        stock, theme = routed
    elif isinstance(source_stock, Mapping):
        # Compatibility for older cached/manual contexts whose stock payload
        # predates per-theme routing fields and relies on the row's industry.
        stock = dict(source_stock)
        fallback_industry = _industry_name(
            stock.get("industry") or latest.get("industry")
        )
        fallback_theme = (context.get("themes") or {}).get(fallback_industry)
        theme = dict(fallback_theme) if isinstance(fallback_theme, Mapping) else None
    else:
        stock, theme = None, None
    industry = _industry_name(
        (stock.get("industry") or latest.get("industry"))
        if isinstance(stock, dict) else latest.get("industry")
    )
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
    breakout_level = max(highs) if highs else None
    current_volume = safe_float(latest.get("volume")) or 0.0
    prior_volumes = [safe_float(row.get("volume")) for row in rows[-21:-1]]
    volumes = [value for value in prior_volumes if value is not None and value > 0]
    volume_ratio = current_volume / _mean(volumes) if volumes else 1.0
    live_change = safe_float(latest.get("quote_change_pct"))
    change_pct = live_change if live_change is not None else (safe_float(latest.get("change_pct")) or 0.0)
    breakout = bool(
        breakout_level is not None
        and close >= breakout_level * 1.002
        and 1.15 <= volume_ratio <= 2.5
    )
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
    daily_v = _daily_v_reversal_metrics(rows, current_close=close)
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
    breakout_extension_atr = (
        max(0.0, (close - breakout_level) / atr)
        if breakout_level is not None
        else None
    )
    breakout_stop_price = (
        breakout_level - atr * 0.5
        if breakout_level is not None and breakout_level - atr * 0.5 > 0
        else None
    )
    score_before_external = (float(theme["score"]) * 0.55 + float(stock["strong_score"]) * 0.45) / 10
    raw_external = float(stock.get("dragon_tiger_adjustment") or 0.0) + float(stock.get("news_adjustment") or 0.0)
    positive_suppressed = bool(raw_external > 0 and extension_atr > 1.5)
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
        "breakout_level": breakout_level,
        "breakout_extension_atr": breakout_extension_atr,
        "breakout_stop_price": breakout_stop_price,
        "volume_ratio": volume_ratio,
        "change_pct": change_pct,
        "breakout": breakout,
        "pullback": pullback,
        "reclaim": reclaim,
        "intraday_low": intraday_low,
        "rebound_from_low_pct": rebound_from_low_pct,
        "reclaim_previous_close": reclaim_previous_close,
        **daily_v,
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


def _strategy_entry_geometry(
    strategy_name: str,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve chase distance and stop from the entry setup's own price anchor."""
    reversal_probe = strategy_name == "niu_reversal_probe"
    breakout_entry = bool(
        strategy_name in {"niu_leader", "niu_emerging"}
        and metrics.get("breakout")
        and safe_float(metrics.get("breakout_level")) is not None
    )
    if reversal_probe:
        stop_price = safe_float(metrics.get("daily_v_stop_price"))
        stop_source = "niu_reversal_right_low"
        entry_extension_atr = safe_float(metrics.get("extension_atr"))
        entry_extension_source = "ema20"
        entry_setup = "daily_v_reversal"
    elif breakout_entry:
        stop_price = safe_float(metrics.get("breakout_stop_price"))
        stop_source = "niu_breakout_pivot"
        entry_extension_atr = safe_float(metrics.get("breakout_extension_atr"))
        entry_extension_source = "breakout_level"
        entry_setup = "breakout"
    else:
        stop_price = safe_float(metrics.get("stop_price"))
        stop_source = "niu_structure_low"
        entry_extension_atr = safe_float(metrics.get("extension_atr"))
        entry_extension_source = "ema20"
        entry_setup = (
            "pullback" if metrics.get("pullback")
            else "reclaim" if metrics.get("reclaim")
            else "none"
        )
    if stop_price is None or stop_price <= 0:
        stop_price = safe_float(metrics.get("stop_price"))
        stop_source = "niu_structure_low"
    return {
        "stop_price": stop_price,
        "stop_source": stop_source,
        "entry_extension_atr": entry_extension_atr,
        "entry_extension_source": entry_extension_source,
        "entry_setup": entry_setup,
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
    geometry = _strategy_entry_geometry(strategy_name, metrics)
    stop_price = geometry["stop_price"]
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
        "theme_basis": str(theme.get("theme_basis") or "industry_proxy"),
        "mainline_state": theme.get("state"),
        "niuone_lifecycle_stage": theme.get("niuone_lifecycle_stage"),
        "niuone_lifecycle_label": theme.get("niuone_lifecycle_label"),
        "niuone_lifecycle_order": theme.get("niuone_lifecycle_order"),
        "niuone_lifecycle_entry_policy": theme.get(
            "niuone_lifecycle_entry_policy"
        ),
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
        "today_strength_score": theme.get("today_strength_score"),
        "today_leadership_score": theme.get("today_leadership_score"),
        "market_regime": regime,
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
        "breakout_level": safe_round(metrics.get("breakout_level"), 3),
        "breakout_extension_atr": safe_round(metrics.get("breakout_extension_atr"), 2),
        "entry_extension_atr": safe_round(geometry["entry_extension_atr"], 2),
        "entry_extension_source": geometry["entry_extension_source"],
        "entry_setup": geometry["entry_setup"],
        "volume_ratio": safe_round(metrics["volume_ratio"], 2),
        "change_pct": safe_round(metrics["change_pct"], 2),
        "trend_aligned": metrics["trend_aligned"],
        "breakout": metrics["breakout"],
        "pullback": metrics["pullback"],
        "reclaim": metrics["reclaim"],
        "reclaim_previous_close": metrics["reclaim_previous_close"],
        "reversal_basis": "daily_v" if strategy_name == "niu_reversal_probe" else "",
        "daily_v_reversal": bool(metrics.get("daily_v_reversal")),
        "daily_v_left_peak_date": metrics.get("daily_v_left_peak_date"),
        "daily_v_trough_date": metrics.get("daily_v_trough_date"),
        "daily_v_left_days": metrics.get("daily_v_left_days"),
        "daily_v_right_days": metrics.get("daily_v_right_days"),
        "daily_v_decline_pct": safe_round(metrics.get("daily_v_decline_pct"), 2),
        "daily_v_rebound_pct": safe_round(metrics.get("daily_v_rebound_pct"), 2),
        "daily_v_recovery_ratio": safe_round(metrics.get("daily_v_recovery_ratio"), 4),
        "daily_v_rising_ratio": safe_round(metrics.get("daily_v_rising_ratio"), 4),
        "daily_v_right_trend_confirmed": bool(
            metrics.get("daily_v_right_trend_confirmed")
        ),
        "daily_v_pattern_score": safe_round(metrics.get("daily_v_pattern_score"), 2),
        "intraday_low": safe_round(metrics["intraday_low"], 3),
        "rebound_from_low_pct": safe_round(metrics["rebound_from_low_pct"], 2),
        "stop_price": safe_round(stop_price, 3),
        "stop_source": geometry["stop_source"],
        "stop_distance_pct": safe_round(stop_distance_pct, 2),
        "stop_atr": safe_round(stop_atr, 2),
        "max_stop_distance_pct": structural_limits["max_stop_distance_pct"],
        "max_stop_atr": structural_limits["max_stop_atr"],
        "min_entry_extension_atr": chase_limits.get(
            "min_entry_extension_atr",
            0.0,
        ),
        # Retained as a compatibility field for older Dashboard payloads.  A
        # NiuOne entry no longer has a fixed daily-gain cap; the execution
        # layer rejects only a quote that is actually at its board limit.
        "max_entry_change_pct": chase_limits.get("max_entry_change_pct"),
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


def _apply_markup_momentum_probe(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Conditionally exchange wider markup geometry for a micro position."""
    if not niuone_markup_momentum_probe_eligible(payload):
        return payload

    acceleration_entry = niuone_markup_momentum_probe_is_acceleration(payload)

    regime = str(payload.get("market_regime") or "")
    stop_distance_pct = safe_float(payload.get("stop_distance_pct")) or 0.0
    stop_atr = safe_float(payload.get("stop_atr")) or 0.0
    limits = niuone_structural_stop_limits(
        regime,
        "niu_emerging",
        NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
    )
    risk_ok = niuone_structure_risk_ok(
        stop_distance_pct,
        stop_atr,
        regime,
        "niu_emerging",
        NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
    )
    budget = niuone_risk_budget(regime, "niu_emerging")
    effective_loss = safe_float(payload.get("effective_loss_distance_pct")) or 0.0
    dynamic_cap = risk_sized_position_cap_pct(
        per_trade_risk_pct=budget["per_trade_risk_pct"],
        effective_loss_distance_pct_value=effective_loss,
        absolute_cap_pct=NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
    )
    standard_only_flags = {
        "启动买点尚未确认",
        "启动战法拒绝追高",
    }
    risk_flags = [
        str(flag)
        for flag in (payload.get("risk_flags") or [])
        if str(flag) not in standard_only_flags
        and not str(flag).startswith("结构止损超过当前行情上限")
    ]
    if not risk_ok:
        risk_flags.append(
            "主升动量试仓结构止损超过"
            f"{limits['max_stop_distance_pct']:g}%或"
            f"{limits['max_stop_atr']:g}ATR"
        )
    extension = safe_float(payload.get("entry_extension_atr"))
    if (
        extension is not None
        and extension > NIUONE_MARKUP_MOMENTUM_PROBE_MAX_ENTRY_EXTENSION_ATR
    ):
        risk_flags.append("主升动量试仓价格扩张过大")

    payload.update({
        "niuone_entry_subroute": NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
        "niuone_entry_subroute_label": "主升动量试仓",
        "niuone_markup_momentum_acceleration": acceleration_entry,
        "entry_threshold_override": (
            NIUONE_MARKUP_MOMENTUM_PROBE_MIN_SCORE
            if acceleration_entry
            else NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_SCORE
        ),
        "entry_setup": NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
        "max_entry_extension_atr": (
            NIUONE_MARKUP_MOMENTUM_PROBE_MAX_ENTRY_EXTENSION_ATR
        ),
        "max_stop_distance_pct": limits["max_stop_distance_pct"],
        "max_stop_atr": limits["max_stop_atr"],
        "absolute_position_cap_pct": (
            NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT
        ),
        "max_position_pct_by_risk": dynamic_cap,
        "risk_ok": risk_ok,
        "risk_flags": list(dict.fromkeys(risk_flags)),
        "verdict": "高匹配主升动量试仓",
    })
    return payload


def _common_risks(
    metrics: dict[str, Any],
    *,
    strategy_name: str,
    require_mature_leader: bool = True,
) -> list[str]:
    risks: list[str] = []
    geometry = _strategy_entry_geometry(strategy_name, metrics)
    stop_price = safe_float(geometry["stop_price"])
    stop_distance_pct = structural_stop_distance_pct(metrics["close"], stop_price)
    stop_atr = (
        (metrics["close"] - stop_price) / metrics["atr"]
        if stop_price is not None and stop_price > 0 and metrics["atr"] > 0
        else 0.0
    )
    regime = str(metrics["market"].get("risk_state") or metrics["market"].get("state") or "")
    if not niuone_structure_risk_ok(
        stop_distance_pct,
        stop_atr,
        regime,
        strategy_name,
    ):
        limits = niuone_structural_stop_limits(regime, strategy_name)
        risks.append(
            "结构止损超过当前行情上限"
            f"({limits['max_stop_distance_pct']:g}%或{limits['max_stop_atr']:g}ATR)"
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
    pattern_score = float(metrics.get("daily_v_pattern_score") or 0.0)
    stock_score = float(stock.get("strong_score") or 0.0)
    base_score = (
        pattern_score * 0.75
        + float(theme.get("score") or 0.0) * 0.15
        + stock_score * 0.10
    ) / 10.0
    raw_external = float(metrics.get("raw_external_context_adjustment") or 0.0)
    positive_suppressed = bool(
        raw_external > 0
        and metrics["extension_atr"] > 1.0
    )
    external = 0.0 if positive_suppressed else _clamp(raw_external, -0.6, 0.4)
    metrics["external_context_adjustment"] = external
    metrics["external_positive_suppressed"] = positive_suppressed
    metrics["reversal_stock_score"] = pattern_score
    return _clamp(base_score + external, 0.0, 10.0)


def score_niu_leader(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context, "niu_leader")
    if metrics is None:
        return None
    risks = _common_risks(metrics, strategy_name="niu_leader")
    if metrics["theme"].get("state") not in {"mainline", "diverging"}:
        risks.append("主题不处于已确认主线或有效分歧")
    if not (
        metrics["theme"].get("mainline_confirmed")
        or metrics["theme"].get("cross_day_confirmed")
    ):
        risks.append("主线未完成跨交易日核心股延续确认")
    if not (metrics["breakout"] or metrics["pullback"]):
        risks.append("未形成突破或首次缩量回踩")
    entry_extension = safe_float(
        _strategy_entry_geometry("niu_leader", metrics).get("entry_extension_atr")
    )
    if entry_extension is not None and entry_extension > 1.0:
        risks.append("领涨买点偏扩张，已按行情弹性上限复核")
    verdict = "高匹配牛牛领涨" if metrics["composite_score"] >= 8 else ("观察牛牛领涨" if metrics["composite_score"] >= 6.5 else "不匹配")
    return with_strategy_profile("niu_leader", _payload("niu_leader", metrics, verdict=verdict, risk_flags=risks))


def score_niu_pullback(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context, "niu_pullback")
    if metrics is None:
        return None
    risks = _common_risks(metrics, strategy_name="niu_pullback")
    if metrics["theme"].get("state") not in {"mainline", "diverging"} or float(metrics["theme"].get("score") or 0) < 70:
        risks.append("主线强度不足以参与分歧")
    if not metrics["theme"].get("mainline_confirmed"):
        risks.append("主题没有有效的跨交易日主线确认记录")
    if not (metrics["pullback"] or metrics["reclaim"]):
        risks.append("未出现EMA20企稳转强或收复买点")
    if metrics["extension_atr"] > 1.0:
        risks.append("转强买点偏扩张，已按行情弹性上限复核")
    verdict = "高匹配牛牛转强" if metrics["composite_score"] >= 8.2 else ("观察牛牛转强" if metrics["composite_score"] >= 6.5 else "不匹配")
    return with_strategy_profile("niu_pullback", _payload("niu_pullback", metrics, verdict=verdict, risk_flags=risks))


def score_niu_emerging(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context, "niu_emerging")
    if metrics is None:
        return None
    risks = _common_risks(metrics, strategy_name="niu_emerging")
    if not niu_emerging_theme_eligible(metrics["theme"]):
        risks.append("主题不处于跨日延续的待确认启动阶段")
    if not metrics["theme"].get("cross_day_persistent"):
        risks.append("启动主题尚未跨交易日延续")
    if int(metrics["theme"].get("strong_stock_count") or 0) < 2:
        risks.append("少于两只强势股共同确认")
    if not (metrics["breakout"] or metrics["reclaim"]):
        risks.append("启动买点尚未确认")
    entry_extension = safe_float(
        _strategy_entry_geometry("niu_emerging", metrics).get("entry_extension_atr")
    )
    if entry_extension is not None and entry_extension > 1.5:
        risks.append("启动战法拒绝追高")
    verdict = "高匹配牛牛启动" if metrics["composite_score"] >= 8.4 else ("观察牛牛启动" if metrics["composite_score"] >= 6.5 else "不匹配")
    payload = _payload(
        "niu_emerging",
        metrics,
        verdict=verdict,
        risk_flags=risks,
    )
    return with_strategy_profile(
        "niu_emerging",
        _apply_markup_momentum_probe(payload),
    )


def score_niu_reversal_probe(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context, "niu_reversal_probe")
    if metrics is None:
        return None
    metrics = dict(metrics)
    metrics["strategy_score"] = _reversal_strategy_score(metrics)
    risks = _common_risks(
        metrics,
        strategy_name="niu_reversal_probe",
        require_mature_leader=False,
    )
    if not metrics.get("daily_v_reversal"):
        risks.append("日线区间尚未形成完整V型趋势反转")
    if metrics["extension_atr"] > 1.0:
        risks.append("日线V型反转买点偏扩张")
    score = float(metrics["strategy_score"])
    verdict = "高匹配牛牛试仓" if score >= 8.4 else ("观察牛牛试仓" if score >= 7.0 else "不匹配")
    payload = _payload("niu_reversal_probe", metrics, verdict=verdict, risk_flags=risks)
    payload["reversal_stock_score"] = safe_round(metrics.get("reversal_stock_score"), 2)
    return with_strategy_profile("niu_reversal_probe", payload)


score_niu_leader.requires_context = True  # type: ignore[attr-defined]
score_niu_pullback.requires_context = True  # type: ignore[attr-defined]
score_niu_emerging.requires_context = True  # type: ignore[attr-defined]
score_niu_reversal_probe.requires_context = True  # type: ignore[attr-defined]
