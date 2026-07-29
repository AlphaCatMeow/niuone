"""牛牛战法: infer market mainlines from cross-sectional strong-stock resonance."""
from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from typing import Any, Mapping

from ..niuone_risk import NIUONE_ABSOLUTE_POSITION_CAP_PCT, niuone_risk_budget
from ..sector_tide_risk import (
    SECTOR_TIDE_EXECUTION_BUFFER_PCT,
    downside_gap_buffer_pct,
    effective_loss_distance_pct,
    risk_sized_position_cap_pct,
    structural_stop_distance_pct,
)
from .common import safe_float, safe_round, with_strategy_profile


NIUONE_STRATEGY_IDS = frozenset({"niu_leader", "niu_pullback", "niu_emerging"})
NIUONE_MIN_ROWS = 55
NIUONE_MIN_THEME_MEMBERS = 3
NIUONE_STRONG_SCORE_THRESHOLD = 70.0
NIUONE_CORE_STOCK_LIMIT = 5
NIUONE_MIN_CROSS_DAY_CORE_OVERLAP = 2


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
    if not clean or len(clean) == 1:
        return 50.0
    below = sum(1 for item in clean if item < value)
    equal = sum(1 for item in clean if item == value)
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


def _atr(rows: list[dict[str, Any]], lookback: int = 14) -> float | None:
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
    for member in members:
        member["ret5_percentile"] = _percentile(float(member["ret5"]), ret5_population)
        member["ret20_percentile"] = _percentile(float(member["ret20"]), ret20_population)
        member["volume_percentile"] = _percentile(float(member["volume_ratio"]), volume_population)
        member["amount_percentile"] = _percentile(float(member["amount"]), amount_population)
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
        strong_members = sorted(
            (member for member in theme_members if member["strong"]),
            key=lambda member: float(member["strong_score"]),
            reverse=True,
        )
        weights = [max(1.0, float(member["strong_score"])) * math.sqrt(max(1.0, float(member["amount"]))) for member in strong_members]
        weight_total = sum(weights)
        normalized = [weight / weight_total for weight in weights] if weight_total > 0 else []
        concentration = max(normalized) if normalized else 1.0
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
        themes[industry] = {
            "industry": industry,
            "theme_basis": "industry_proxy",
            "member_count": len(theme_members),
            "eligible_data": eligible,
            "score": round(score, 2),
            "raw_state": raw_state,
            "state": state,
            "intraday_state": state_detail["intraday_state"],
            "confirmation_count": state_detail["confirmation_count"],
            "intraday_confirmation_count": state_detail["intraday_confirmation_count"],
            "state_streak": state_detail["state_streak"],
            "as_of_date": as_of_date,
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
            "single_stock_dominated": bool(strong_count <= 1 or concentration > 0.70),
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
                }
                for index, member in enumerate(strong_members[:NIUONE_CORE_STOCK_LIMIT])
            ],
        }

        theme_ret5 = [float(member["ret5"]) for member in theme_members]
        theme_ret20 = [float(member["ret20"]) for member in theme_members]
        for rank_index, member in enumerate(sorted(theme_members, key=lambda item: float(item["strong_score"]), reverse=True), start=1):
            code = str(member["code"])
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
        "version": 2,
        "strategy": "niuone",
        "theme_basis": "industry_proxy",
        "as_of_date": as_of_date,
        "previous_trading_day": previous_trading_day,
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
    structure_low = min(lows) if lows else close - atr * 1.5
    stop_distance = structural_stop_distance_pct(close, structure_low)
    stop_atr = (close - structure_low) / atr
    risk_ok = 0 < stop_distance <= 6 and stop_atr <= 1.5
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
        "atr20": atr,
        "distance_pct": (close / ema20 - 1) * 100,
        "extension_atr": extension_atr,
        "volume_ratio": volume_ratio,
        "change_pct": change_pct,
        "breakout": breakout,
        "pullback": pullback,
        "reclaim": reclaim,
        "trend_aligned": trend_aligned,
        "stop_price": structure_low,
        "stop_distance_pct": stop_distance,
        "stop_atr": stop_atr,
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
    budget = niuone_risk_budget(str(market.get("risk_state") or market.get("state") or ""))
    absolute_cap = NIUONE_ABSOLUTE_POSITION_CAP_PCT[strategy_name]
    dynamic_cap = risk_sized_position_cap_pct(
        per_trade_risk_pct=budget["per_trade_risk_pct"],
        effective_loss_distance_pct_value=metrics["effective_loss_distance_pct"],
        absolute_cap_pct=absolute_cap,
    )
    news_precheck = stock.get("news_precheck") if isinstance(stock.get("news_precheck"), dict) else {}
    return {
        "score": metrics["composite_score"],
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
        "market_regime": market.get("state"),
        "market_score": market.get("score"),
        "market_hard_stop": bool(market.get("hard_stop")),
        "market_allows_buys": bool(market.get("allow_new_buys")),
        "stock_role": stock.get("role"),
        "stock_strong": bool(stock.get("strong")),
        "stock_strong_score": stock.get("strong_score"),
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
        "atr20": safe_round(metrics["atr20"], 3),
        "distance_pct": safe_round(metrics["distance_pct"], 2),
        "extension_atr": safe_round(metrics["extension_atr"], 2),
        "volume_ratio": safe_round(metrics["volume_ratio"], 2),
        "change_pct": safe_round(metrics["change_pct"], 2),
        "trend_aligned": metrics["trend_aligned"],
        "breakout": metrics["breakout"],
        "pullback": metrics["pullback"],
        "reclaim": metrics["reclaim"],
        "stop_price": safe_round(metrics["stop_price"], 3),
        "stop_source": "niu_structure_low",
        "stop_distance_pct": safe_round(metrics["stop_distance_pct"], 2),
        "stop_atr": safe_round(metrics["stop_atr"], 2),
        "gap_buffer_pct": safe_round(metrics["gap_buffer_pct"], 3),
        "execution_buffer_pct": SECTOR_TIDE_EXECUTION_BUFFER_PCT,
        "effective_loss_distance_pct": safe_round(metrics["effective_loss_distance_pct"], 3),
        "per_trade_risk_budget_pct": budget["per_trade_risk_pct"],
        "max_open_risk_pct": budget["max_open_risk_pct"],
        "max_sector_risk_pct": budget["max_sector_risk_pct"],
        "max_total_position_pct": budget["max_total_position_pct"],
        "max_sector_position_pct": budget["max_sector_position_pct"],
        "absolute_position_cap_pct": absolute_cap,
        "max_position_pct_by_risk": dynamic_cap,
        "risk_ok": metrics["risk_ok"],
        "risk_flags": risk_flags,
        "recent_close": safe_round(metrics["close"], 3),
    }


def _common_risks(metrics: dict[str, Any]) -> list[str]:
    risks: list[str] = []
    if not metrics["risk_ok"]:
        risks.append("结构止损超过1.5ATR或6%")
    if metrics["theme"].get("single_stock_dominated"):
        risks.append("主题由单只强股主导")
    news = metrics["stock"].get("news_precheck") or {}
    if news.get("available") and news.get("tone") == "negative":
        risks.append("近3日个股消息面偏利空")
    return risks


def score_niu_leader(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context)
    if metrics is None:
        return None
    risks = _common_risks(metrics)
    if metrics["theme"].get("state") != "mainline":
        risks.append("主题尚未确认为市场主线")
    if not metrics["theme"].get("cross_day_confirmed"):
        risks.append("主线未完成跨交易日核心股延续确认")
    if not metrics["stock"].get("strong") or float(metrics["stock"].get("theme_rank") or 0) < 80:
        risks.append("个股不是主线核心强股")
    if not (metrics["breakout"] or metrics["pullback"]):
        risks.append("未形成突破或首次缩量回踩")
    if metrics["change_pct"] > 4 or metrics["extension_atr"] > 1.0:
        risks.append("领航战法拒绝追高")
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
    if float(metrics["stock"].get("theme_rank") or 0) < 70:
        risks.append("个股不是主线第一梯队")
    if not (metrics["pullback"] or metrics["reclaim"]):
        risks.append("未出现EMA20承接或收复买点")
    if metrics["change_pct"] > 4 or metrics["extension_atr"] > 1.0:
        risks.append("回踩战法拒绝追高")
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
    if not metrics["stock"].get("strong") or float(metrics["stock"].get("theme_rank") or 0) < 80:
        risks.append("个股不是新主线核心强股")
    if not (metrics["breakout"] or metrics["reclaim"]):
        risks.append("启动买点尚未确认")
    if metrics["change_pct"] > 7 or metrics["extension_atr"] > 1.5:
        risks.append("启动战法拒绝追高")
    verdict = "高匹配牛牛启动" if metrics["composite_score"] >= 8.4 else ("观察牛牛启动" if metrics["composite_score"] >= 6.5 else "不匹配")
    return with_strategy_profile("niu_emerging", _payload("niu_emerging", metrics, verdict=verdict, risk_flags=risks))


score_niu_leader.requires_context = True  # type: ignore[attr-defined]
score_niu_pullback.requires_context = True  # type: ignore[attr-defined]
score_niu_emerging.requires_context = True  # type: ignore[attr-defined]
