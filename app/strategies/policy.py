"""Pure strategy-level position and candidate eligibility policies."""
from __future__ import annotations

from typing import Any

from .registry import STRATEGY_DEFINITIONS, STRATEGY_POSITION_LIMIT_PCT


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def strategy_position_limit_pct(strategy: str, max_single_position_pct: float) -> float:
    return min(
        max_single_position_pct,
        float(STRATEGY_POSITION_LIMIT_PCT.get(strategy or "", max_single_position_pct)),
    )


def candidate_buy_blockers(
    candidate: dict[str, Any] | None,
    *,
    max_bbi_distance_pct: float = 6.5,
) -> list[str]:
    if not candidate:
        return ["买入标的不在本轮交易候选池"]
    blockers = [str(item) for item in (candidate.get("hard_blockers") or []) if str(item).strip()]
    raw_score = candidate.get("best_score")
    if raw_score is None:
        raw_score = candidate.get("score")
    score = _safe_float(raw_score, 0.0)
    threshold = _safe_float(candidate.get("entry_threshold"), 8.0)
    if score < threshold:
        blockers.append(f"评分{score:g}<基准{threshold:g}")
    if candidate.get("actionable") is False:
        blockers.append("候选未通过战法硬过滤")
    strategy_id = str(
        candidate.get("best_strategy")
        or candidate.get("buy_strategy")
        or candidate.get("strategy_id")
        or ""
    )
    persona = STRATEGY_DEFINITIONS.get(strategy_id, {}).get("persona")
    reversal_probe = strategy_id == "niu_reversal_probe"
    if persona == "niuone" and (
        (
            candidate.get("stock_reversal_leader_tier") is not True
            or candidate.get("stock_reversal_strong") is not True
        )
        if reversal_probe
        else (
            candidate.get("stock_leader_tier") is not True
            or candidate.get("stock_strong") is not True
        )
    ):
        leader_blocker = (
            "个股未进入反转领涨前三"
            if reversal_probe
            else "个股未进入强势行业龙头梯队"
        )
        if leader_blocker not in blockers:
            blockers.append(leader_blocker)
    is_ema_strategy = persona in {"sector_tide", "niuone"}
    distance = candidate.get("distance_pct")
    if not is_ema_strategy and distance is not None and _safe_float(distance, 99.0) > max_bbi_distance_pct:
        blockers.append(f"距BBI>{max_bbi_distance_pct}%")
    return blockers


def candidate_is_buyable(
    candidate: dict[str, Any] | None,
    *,
    max_bbi_distance_pct: float = 6.5,
) -> bool:
    return not candidate_buy_blockers(candidate, max_bbi_distance_pct=max_bbi_distance_pct)
