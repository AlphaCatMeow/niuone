"""Dynamic risk budgets for the independent 牛牛战法 strategy suite."""
from __future__ import annotations


NIUONE_ABSOLUTE_POSITION_CAP_PCT = {
    "niu_leader": 30.0,
    "niu_pullback": 25.0,
    "niu_emerging": 15.0,
}

NIUONE_MAX_OPEN_POSITIONS = 5

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
