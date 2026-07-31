"""Candidate eligibility and strategy-aware display selection."""
import os
from typing import Any

from .registry import DISPLAY_STRATEGY_ORDER
from .scoring import COMMON_MAX_BBI_DISTANCE_PCT, safe_float


DISPLAY_CANDIDATE_LIMIT_ENV = "DASHBOARD_DISPLAY_CANDIDATE_LIMIT"
TRADE_CANDIDATE_LIMIT_ENV = "DASHBOARD_TRADE_CANDIDATE_LIMIT"
DEFAULT_DISPLAY_CANDIDATE_LIMIT = 10
DEFAULT_TRADE_CANDIDATE_LIMIT = 10


def configured_candidate_limit(name: str, default: int) -> int:
    try:
        return max(1, min(100, int(os.environ.get(name, str(default)) or default)))
    except (TypeError, ValueError):
        return default


def candidate_score_sort_key(item: dict[str, Any]) -> tuple[float, float, str]:
    """Sort by the final score shown on candidate cards, then decision score."""
    raw_score = item.get("best_score")
    if raw_score is None:
        raw_score = item.get("score")
    score = safe_float(raw_score)
    decision_score = safe_float(item.get("best_decision_score"))
    return (
        -(score if score is not None else -1.0),
        -(decision_score if decision_score is not None else score if score is not None else -1.0),
        str(item.get("code") or ""),
    )


def sort_candidates_by_score(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(results, key=candidate_score_sort_key)


def candidate_is_trade_ready(item: dict[str, Any]) -> bool:
    raw_score = item.get("best_score")
    if raw_score is None:
        raw_score = item.get("score")
    score = safe_float(raw_score) or 0
    threshold = safe_float(item.get("entry_threshold")) or 8
    blockers = item.get("hard_blockers") or []
    distance = safe_float(item.get("distance_pct"))
    strategy_id = str(item.get("best_strategy") or item.get("strategy_id") or "")
    niuone_strategy = strategy_id in {
        "niu_leader", "niu_pullback", "niu_emerging", "niu_reversal_probe",
    }
    reversal_probe = strategy_id == "niu_reversal_probe"
    ema_strategy = strategy_id in {
        "tide_leader", "tide_rotation", "tide_recovery",
        "niu_leader", "niu_pullback", "niu_emerging", "niu_reversal_probe",
    }
    return (
        bool(item.get("actionable", score >= threshold))
        and score >= threshold
        and not blockers
        and (
            not niuone_strategy
            or (
                reversal_probe
                and item.get("stock_reversal_leader_tier") is True
                and item.get("stock_reversal_strong") is True
            )
            or (
                not reversal_probe
                and item.get("stock_leader_tier") is True
                and item.get("stock_strong") is True
            )
        )
        and (ema_strategy or distance is None or distance <= COMMON_MAX_BBI_DISTANCE_PCT)
    )


def select_trade_candidates(results: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    """Return candidates allowed to reach the trading decision model."""
    if limit is None:
        limit = configured_candidate_limit(TRADE_CANDIDATE_LIMIT_ENV, DEFAULT_TRADE_CANDIDATE_LIMIT)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sort_candidates_by_score(results):
        if len(selected) >= limit:
            break
        code = str(item.get("code") or "")
        if not code or code in seen or not candidate_is_trade_ready(item):
            continue
        selected.append(item)
        seen.add(code)
    return selected


def select_display_candidates(
    results: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Keep top-ranked names while reserving slots for each strategy family."""
    if limit is None:
        limit = configured_candidate_limit(DISPLAY_CANDIDATE_LIMIT_ENV, DEFAULT_DISPLAY_CANDIDATE_LIMIT)
    ranked_results = sort_candidates_by_score(results)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        if len(selected) >= limit:
            return
        code = str(item.get("code") or "")
        if not code or code in seen:
            return
        selected.append(item)
        seen.add(code)

    trade_ready = [item for item in ranked_results if candidate_is_trade_ready(item)]
    trade_head_limit = configured_candidate_limit(TRADE_CANDIDATE_LIMIT_ENV, DEFAULT_TRADE_CANDIDATE_LIMIT)
    for item in trade_ready[:min(limit, trade_head_limit)]:
        add(item)

    for strategy_id in DISPLAY_STRATEGY_ORDER:
        for item in trade_ready:
            if item.get("best_strategy") == strategy_id:
                add(item)
                break

    for item in trade_ready:
        add(item)

    for item in ranked_results:
        add(item)

    return sort_candidates_by_score(selected)
