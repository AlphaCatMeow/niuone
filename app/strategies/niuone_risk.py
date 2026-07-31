"""Dynamic risk budgets for the independent 牛牛战法 strategy suite."""
from __future__ import annotations


NIUONE_ABSOLUTE_POSITION_CAP_PCT = {
    "niu_leader": 30.0,
    "niu_pullback": 25.0,
    "niu_emerging": 15.0,
    "niu_reversal_probe": 5.0,
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
    "niu_reversal_probe": {
        "offensive": {"max_entry_change_pct": 5.0, "max_entry_extension_atr": 1.0},
        "rotation": {"max_entry_change_pct": 5.0, "max_entry_extension_atr": 1.0},
        "recovery": {"max_entry_change_pct": 5.0, "max_entry_extension_atr": 1.0},
    },
}

NIUONE_REVERSAL_STOP_LIMITS = {
    "max_stop_distance_pct": 4.0,
    "max_stop_atr": 1.2,
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

# A same-day reversal lot cannot be sold again under A-share T+1 rules. Keep
# its per-trade loss budget deliberately below the confirmed NiuOne paths while
# preserving the suite-wide exposure ceilings used by established positions.
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
    "defensive": dict(NIUONE_REGIME_RISK_BUDGETS["defensive"]),
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
) -> dict[str, float]:
    """Return the hard structural-stop limits for one market regime."""
    if str(strategy_name or "") == "niu_reversal_probe":
        return dict(NIUONE_REVERSAL_STOP_LIMITS)
    key = str(regime or "defensive").strip().lower()
    return dict(NIUONE_STRUCTURAL_STOP_LIMITS.get(key, NIUONE_STRUCTURAL_STOP_LIMITS["defensive"]))


def niuone_structure_risk_ok(
    stop_distance_pct: float,
    stop_atr: float,
    regime: str | None,
    strategy_name: str | None = None,
) -> bool:
    """Check a proposed structural stop against the regime-aware hard limits."""
    limits = niuone_structural_stop_limits(regime, strategy_name)
    return bool(
        0 < stop_distance_pct <= limits["max_stop_distance_pct"]
        and 0 < stop_atr <= limits["max_stop_atr"]
    )


def niuone_chase_limits(strategy_name: str, regime: str | None) -> dict[str, float]:
    """Return the price-expansion hard limits for one NiuOne entry path."""
    key = str(regime or "defensive").strip().lower()
    by_regime = NIUONE_CHASE_LIMITS.get(strategy_name, {})
    fallback_strategy = (
        strategy_name
        if strategy_name in {"niu_emerging", "niu_reversal_probe"}
        else "niu_pullback"
    )
    fallback = NIUONE_CHASE_LIMITS[fallback_strategy]["recovery"]
    return dict(by_regime.get(key, fallback))
