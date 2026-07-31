"""Dynamic risk budgets for the independent 牛牛战法 strategy suite."""
from __future__ import annotations


NIUONE_ABSOLUTE_POSITION_CAP_PCT = {
    "niu_leader": 30.0,
    "niu_pullback": 25.0,
    "niu_emerging": 15.0,
}

NIUONE_MAX_OPEN_POSITIONS = 5

# Entry limits become gradually more permissive as the market risk state
# improves.  The execution layer still sizes every order from the effective
# loss distance, so widening a structural stop does not widen the account-risk
# budget for that trade.
NIUONE_STRUCTURAL_STOP_LIMITS = {
    "offensive": {"max_stop_distance_pct": 10.0, "max_stop_atr": 2.5},
    "rotation": {"max_stop_distance_pct": 8.0, "max_stop_atr": 2.0},
    "recovery": {"max_stop_distance_pct": 6.0, "max_stop_atr": 1.5},
    "defensive": {"max_stop_distance_pct": 6.0, "max_stop_atr": 1.5},
}

NIUONE_CHASE_LIMITS = {
    "niu_leader": {
        "offensive": {"max_entry_change_pct": 7.0, "max_entry_extension_atr": 1.5},
        "rotation": {"max_entry_change_pct": 5.0, "max_entry_extension_atr": 1.25},
    },
    "niu_pullback": {
        "offensive": {"max_entry_change_pct": 5.0, "max_entry_extension_atr": 1.25},
        "rotation": {"max_entry_change_pct": 4.0, "max_entry_extension_atr": 1.0},
        "recovery": {"max_entry_change_pct": 4.0, "max_entry_extension_atr": 1.0},
    },
    "niu_emerging": {
        "offensive": {"max_entry_change_pct": 7.0, "max_entry_extension_atr": 1.5},
        "rotation": {"max_entry_change_pct": 7.0, "max_entry_extension_atr": 1.5},
        "recovery": {"max_entry_change_pct": 7.0, "max_entry_extension_atr": 1.5},
    },
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
        "per_trade_risk_pct": 0.0,
        "max_open_risk_pct": 0.0,
        "max_sector_risk_pct": 0.0,
        "max_total_position_pct": 0.0,
        "max_sector_position_pct": 0.0,
    },
}


def niuone_risk_budget(regime: str | None) -> dict[str, float]:
    """Return an isolated budget mapping for one market regime."""
    key = str(regime or "defensive").strip().lower()
    return dict(NIUONE_REGIME_RISK_BUDGETS.get(key, NIUONE_REGIME_RISK_BUDGETS["defensive"]))


def niuone_structural_stop_limits(regime: str | None) -> dict[str, float]:
    """Return the hard structural-stop limits for one market regime."""
    key = str(regime or "defensive").strip().lower()
    return dict(NIUONE_STRUCTURAL_STOP_LIMITS.get(key, NIUONE_STRUCTURAL_STOP_LIMITS["defensive"]))


def niuone_structure_risk_ok(
    stop_distance_pct: float,
    stop_atr: float,
    regime: str | None,
) -> bool:
    """Check a proposed structural stop against the regime-aware hard limits."""
    limits = niuone_structural_stop_limits(regime)
    return bool(
        0 < stop_distance_pct <= limits["max_stop_distance_pct"]
        and 0 < stop_atr <= limits["max_stop_atr"]
    )


def niuone_chase_limits(strategy_name: str, regime: str | None) -> dict[str, float]:
    """Return the price-expansion hard limits for one NiuOne entry path."""
    key = str(regime or "defensive").strip().lower()
    by_regime = NIUONE_CHASE_LIMITS.get(strategy_name, {})
    fallback = (
        NIUONE_CHASE_LIMITS["niu_emerging"]["recovery"]
        if strategy_name == "niu_emerging"
        else NIUONE_CHASE_LIMITS["niu_pullback"]["recovery"]
    )
    return dict(by_regime.get(key, fallback))
