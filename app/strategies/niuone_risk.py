"""Dynamic risk budgets for the independent 牛牛战法 strategy suite."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


NIUONE_ABSOLUTE_POSITION_CAP_PCT = {
    "niu_leader": 30.0,
    "niu_pullback": 25.0,
    "niu_emerging": 15.0,
    "niu_reversal_probe": 6.25,
}

NIUONE_MAX_OPEN_POSITIONS = 5
NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY = 2
NIUONE_ENTRY_REGIMES = frozenset({
    "offensive",
    "rotation",
    "recovery",
    "defensive",
})

# A narrowly conditioned emerging-theme route for a first position after the
# lifecycle has already moved into markup.  The wider price geometry is paid
# for with a much smaller absolute position cap; it is not a global relaxation
# of the ordinary emerging/reversal entry rules.
NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE = "markup_momentum_probe"
NIUONE_MARKUP_MOMENTUM_PROBE_MIN_SCORE = 8.0
NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_SCORE = 8.1
NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_MAINLINE_SCORE = 70.0
NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MAX_EXTENSION_ATR = 1.0
NIUONE_MARKUP_MOMENTUM_PROBE_MIN_STRONG_SCORE = 90.0
NIUONE_MARKUP_MOMENTUM_PROBE_REQUIRED_LEADER_RANK = 1
NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MIN_EXTENSION_ATR = 2.5
NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MIN_CHANGE_PCT = 9.5
NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MAX_VOLUME_RATIO = 1.2
NIUONE_MARKUP_MOMENTUM_PROBE_MAX_ENTRY_EXTENSION_ATR = 3.2
NIUONE_MARKUP_MOMENTUM_PROBE_MAX_STOP_DISTANCE_PCT = 18.0
NIUONE_MARKUP_MOMENTUM_PROBE_MAX_STOP_ATR = 3.0
NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT = 4.0
NIUONE_MARKUP_MOMENTUM_PROBE_MAX_EXECUTION_GAP_PCT = 3.0

# A 6.25% reversal probe is only the brewing-stage starting size. During
# markup, a persistent emerging leader may first scale toward the early cap;
# once the mainline is confirmed it may continue toward the final cap. The
# structural-stop distance and portfolio risk budgets below still determine
# the smaller executable target.
NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT = 10.0
NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT = 20.0
NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT = 2.0
NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT = 12.0

# Markup holdings use event-driven rebalance cycles instead of a fixed number
# of adds.  A confirmed leader first has to give back enough of its latest
# closing-price peak, or spend several sessions below that peak, before one
# third is released.  The released risk may be added back only after a fresh
# rebound.  Completing an add starts a new cycle; there is deliberately no
# lifetime add counter limit.
NIUONE_MARKUP_REBALANCE_PULLBACK_ATR = 1.0
NIUONE_MARKUP_REBALANCE_STALL_SESSIONS = 3
NIUONE_MARKUP_REBALANCE_STALL_MIN_ATR = 0.25
NIUONE_MARKUP_REBALANCE_REBOUND_ATR = 0.5
NIUONE_MARKUP_REBALANCE_MIN_SESSIONS_AFTER_ADD = 2
NIUONE_MARKUP_REBALANCE_TRIM_RATIO = 1.0 / 3.0

# Structural-stop limits become gradually more permissive as the market risk
# state improves.  The execution layer still sizes every order from the
# effective loss distance, so widening a structural stop does not widen the
# account-risk budget for that trade.
NIUONE_STRUCTURAL_STOP_LIMITS = {
    "offensive": {"max_stop_distance_pct": 10.0, "max_stop_atr": 2.5},
    "rotation": {"max_stop_distance_pct": 8.0, "max_stop_atr": 2.0},
    "recovery": {"max_stop_distance_pct": 6.0, "max_stop_atr": 1.5},
    "defensive": {"max_stop_distance_pct": 6.0, "max_stop_atr": 1.5},
}

NIUONE_CHASE_LIMITS = {
    "niu_leader": {
        "offensive": {"max_entry_extension_atr": 1.5},
        "rotation": {"max_entry_extension_atr": 1.25},
    },
    "niu_pullback": {
        "offensive": {"max_entry_extension_atr": 1.25},
        "rotation": {"max_entry_extension_atr": 1.0},
        "recovery": {"max_entry_extension_atr": 1.0},
    },
    "niu_emerging": {
        "offensive": {"max_entry_extension_atr": 1.5},
        "rotation": {"max_entry_extension_atr": 1.5},
        "recovery": {"max_entry_extension_atr": 1.5},
    },
    "niu_reversal_probe": {
        "offensive": {
            "min_entry_extension_atr": 1.0,
            "max_entry_extension_atr": 1.5,
        },
        "rotation": {
            "min_entry_extension_atr": 1.0,
            "max_entry_extension_atr": 1.5,
        },
        "recovery": {
            "min_entry_extension_atr": 1.0,
            "max_entry_extension_atr": 1.5,
        },
    },
}

NIUONE_REVERSAL_STOP_LIMITS = {
    "max_stop_distance_pct": 6.0,
    "max_stop_atr": 2.0,
}

# Risk values are percentages of account equity. Exposure values are
# percentages of gross account equity. The suite normally concentrates in up
# to three names and hard-stops at five, so its loss budgets are paired with
# explicit single-name, theme, and total-exposure limits.
NIUONE_REGIME_RISK_BUDGETS = {
    "offensive": {
        "per_trade_risk_pct": 1.50,
        "max_open_risk_pct": 4.50,
        "max_sector_risk_pct": 3.00,
        "max_total_position_pct": 70.0,
        "max_sector_position_pct": 55.0,
    },
    "rotation": {
        "per_trade_risk_pct": 1.00,
        "max_open_risk_pct": 3.00,
        "max_sector_risk_pct": 2.00,
        "max_total_position_pct": 55.0,
        "max_sector_position_pct": 40.0,
    },
    "recovery": {
        "per_trade_risk_pct": 0.60,
        "max_open_risk_pct": 1.80,
        "max_sector_risk_pct": 1.20,
        "max_total_position_pct": 35.0,
        "max_sector_position_pct": 25.0,
    },
    "defensive": {
        "per_trade_risk_pct": 0.30,
        "max_open_risk_pct": 0.90,
        "max_sector_risk_pct": 0.60,
        "max_total_position_pct": 20.0,
        "max_sector_position_pct": 12.0,
    },
}

# The daily V-reversal path remains an early, lower-certainty entry. Keep its
# loss budget deliberately below the confirmed NiuOne paths while preserving
# the suite-wide exposure ceilings used by established positions.
NIUONE_REVERSAL_RISK_BUDGETS = {
    "offensive": {
        **NIUONE_REGIME_RISK_BUDGETS["offensive"],
        "per_trade_risk_pct": 0.35,
        "max_sector_risk_pct": 0.70,
        "max_sector_position_pct": 12.0,
    },
    "rotation": {
        **NIUONE_REGIME_RISK_BUDGETS["rotation"],
        "per_trade_risk_pct": 0.30,
        "max_sector_risk_pct": 0.60,
        "max_sector_position_pct": 10.0,
    },
    "recovery": {
        **NIUONE_REGIME_RISK_BUDGETS["recovery"],
        "per_trade_risk_pct": 0.25,
        "max_sector_risk_pct": 0.50,
        "max_sector_position_pct": 8.0,
    },
    "defensive": {
        **NIUONE_REGIME_RISK_BUDGETS["defensive"],
        "per_trade_risk_pct": 0.15,
        "max_sector_risk_pct": 0.30,
        "max_sector_position_pct": 5.0,
    },
}


def niuone_risk_budget(
    regime: str | None,
    strategy_name: str | None = None,
) -> dict[str, float]:
    """Return an isolated budget mapping for one market regime."""
    key = str(regime or "defensive").strip().lower()
    budgets = (
        NIUONE_REVERSAL_RISK_BUDGETS
        if str(strategy_name or "") == "niu_reversal_probe"
        else NIUONE_REGIME_RISK_BUDGETS
    )
    return dict(budgets.get(key, budgets["defensive"]))


def niuone_structural_stop_limits(
    regime: str | None,
    strategy_name: str | None = None,
    entry_subroute: str | None = None,
) -> dict[str, float]:
    """Return the hard structural-stop limits for one market regime."""
    if (
        str(strategy_name or "") == "niu_emerging"
        and str(entry_subroute or "")
        == NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE
    ):
        return {
            "max_stop_distance_pct": (
                NIUONE_MARKUP_MOMENTUM_PROBE_MAX_STOP_DISTANCE_PCT
            ),
            "max_stop_atr": NIUONE_MARKUP_MOMENTUM_PROBE_MAX_STOP_ATR,
        }
    if str(strategy_name or "") == "niu_reversal_probe":
        return dict(NIUONE_REVERSAL_STOP_LIMITS)
    key = str(regime or "defensive").strip().lower()
    return dict(NIUONE_STRUCTURAL_STOP_LIMITS.get(key, NIUONE_STRUCTURAL_STOP_LIMITS["defensive"]))


def niuone_structure_risk_ok(
    stop_distance_pct: float,
    stop_atr: float,
    regime: str | None,
    strategy_name: str | None = None,
    entry_subroute: str | None = None,
) -> bool:
    """Check a proposed structural stop against the regime-aware hard limits."""
    limits = niuone_structural_stop_limits(
        regime,
        strategy_name,
        entry_subroute,
    )
    return bool(
        0 < stop_distance_pct <= limits["max_stop_distance_pct"]
        and 0 < stop_atr <= limits["max_stop_atr"]
    )


def niuone_chase_limits(strategy_name: str, regime: str | None) -> dict[str, float]:
    """Return ATR expansion limits; daily gain is gated only by limit-up execution."""
    key = str(regime or "defensive").strip().lower()
    by_regime = NIUONE_CHASE_LIMITS.get(strategy_name, {})
    fallback_strategy = (
        strategy_name
        if strategy_name in {"niu_emerging", "niu_reversal_probe"}
        else "niu_pullback"
    )
    fallback = NIUONE_CHASE_LIMITS[fallback_strategy]["recovery"]
    return dict(by_regime.get(key, fallback))


def niuone_markup_momentum_probe_eligible(values: Mapping[str, Any]) -> bool:
    """Return whether a scored emerging leader has the probe's identity gates."""

    def number(key: str) -> float | None:
        try:
            value = float(values.get(key))
        except (TypeError, ValueError):
            return None
        return value

    stage = str(values.get("niuone_lifecycle_stage") or "").strip().lower()
    state = str(
        values.get("mainline_state")
        or values.get("sector_status")
        or ""
    ).strip().lower()
    regime = str(values.get("market_regime") or "").strip().lower()
    score = number("score")
    if score is None:
        score = number("best_score")
    strong_score = number("stock_strong_score")
    leader_rank = number("stock_leader_rank")
    mainline_score = number("mainline_score")
    extension_atr = number("entry_extension_atr")
    change_pct = number("change_pct")
    volume_ratio = number("volume_ratio")
    base_eligible = bool(
        stage == "markup"
        and state == "emerging"
        and values.get("mainline_cross_day_persistent") is True
        and values.get("stock_leader_tier") is True
        and values.get("stock_strong") is True
        and leader_rank == NIUONE_MARKUP_MOMENTUM_PROBE_REQUIRED_LEADER_RANK
        and strong_score is not None
        and strong_score >= NIUONE_MARKUP_MOMENTUM_PROBE_MIN_STRONG_SCORE
        and score is not None
        and round(score, 1) >= NIUONE_MARKUP_MOMENTUM_PROBE_MIN_SCORE
        and regime in NIUONE_ENTRY_REGIMES
        and values.get("market_allows_buys") is True
        and values.get("market_hard_stop") is not True
    )
    if not base_eligible:
        return False
    ordinary = bool(
        round(float(score), 1)
        >= NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_SCORE
        and mainline_score is not None
        and mainline_score
        >= NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_MAINLINE_SCORE
        and extension_atr is not None
        and extension_atr
        <= NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MAX_EXTENSION_ATR + 1e-9
    )
    return ordinary or niuone_markup_momentum_probe_is_acceleration(values)


def niuone_markup_momentum_probe_is_acceleration(
    values: Mapping[str, Any],
) -> bool:
    """Keep only the bounded near-limit-up, non-explosive-volume exception."""
    def number(key: str) -> float | None:
        try:
            return float(values.get(key))
        except (TypeError, ValueError):
            return None

    extension_atr = number("entry_extension_atr")
    change_pct = number("change_pct")
    volume_ratio = number("volume_ratio")
    return bool(
        extension_atr is not None
        and NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MIN_EXTENSION_ATR
        <= extension_atr
        <= NIUONE_MARKUP_MOMENTUM_PROBE_MAX_ENTRY_EXTENSION_ATR + 1e-9
        and change_pct is not None
        and change_pct
        >= NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MIN_CHANGE_PCT
        and volume_ratio is not None
        and volume_ratio
        <= NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MAX_VOLUME_RATIO
    )
