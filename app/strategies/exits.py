"""Strategy-specific exit rules without market-data or execution side effects."""
from __future__ import annotations

from collections.abc import Collection
from typing import Any


SHAOFU_MIN_HOLD_TRADING_DAYS = 3
SHAOFU_SOFT_EXIT_CONFIRMATIONS = 2
NIUONE_MAX_HOLD_CALENDAR_DAYS = 25
NIUONE_LEADER_LOSS_CONFIRMATIONS = 2
NIUONE_MAINLINE_WEAK_CONFIRMATIONS = 2
NIUONE_CLIMAX_RUNNER_ENABLED = True
NIUONE_CLIMAX_RUNNER_LEADER_LOSS_CONFIRMATIONS = 3
NIUONE_CLIMAX_RUNNER_TRAILING_ATR = 3.0
# Frozen on disjoint development windows on 2026-08-01.  NiuOne realizes a
# smaller first leg once the initial structural risk has been earned, then
# protects the runner at cost while the mainline remains healthy.
NIUONE_PARTIAL_TAKE_PROFIT_R = 1.0
NIUONE_PARTIAL_TAKE_PROFIT_RATIO = 0.45
# Reversal probes entered while the broad tape is advancing, repairing, or
# defensive use earlier protection than rotation-regime probes. Realize a
# slightly larger first leg sooner, while leaving mature NiuOne paths and
# rotation-regime reversals on the common 1R/45% policy.
NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES = frozenset({
    "offensive",
    "recovery",
    "defensive",
})
NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_R = 0.75
NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_RATIO = 0.50


def niuone_climax_runner_active(
    *,
    enabled: bool,
    climax_partial_done: bool,
    partial_tp_done: bool,
    stock_strong: bool,
    theme_score: float,
    theme_state: str,
) -> bool:
    """Keep only a de-risked winner's remainder on a healthy mainline."""
    return bool(
        enabled
        and climax_partial_done
        and partial_tp_done
        and stock_strong
        and float(theme_score) >= 55.0
        and str(theme_state or "")
        in {"candidate", "emerging", "mainline", "diverging"}
    )
NIUONE_BREAK_EVEN_AFTER_PARTIAL = True
NIUONE_LIFECYCLE_CLIMAX_PARTIAL_RATIO = 1.0 / 3.0
NIUONE_LIFECYCLE_CLIMAX_MIN_PNL_PCT = 0.0
NIUONE_INTRADAY_PROFIT_TARGET = True
NIUONE_REVERSAL_MAINLINE_WEAK_CONFIRMATIONS = 1


def _sell_signal(reason: str, signal: str, sell_ratio: float = 1.0) -> dict[str, Any]:
    return {"reason": reason, "signal": signal, "sell_ratio": sell_ratio}


def resolve_niuone_partial_take_profit(
    *,
    strategy_id: str,
    entry_market_regime: str,
    default_r: float = NIUONE_PARTIAL_TAKE_PROFIT_R,
    default_ratio: float = NIUONE_PARTIAL_TAKE_PROFIT_RATIO,
    reversal_early_regimes: Collection[str] = NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES,
    reversal_early_r: float = NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_R,
    reversal_early_ratio: float = NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_RATIO,
) -> tuple[float, float]:
    """Resolve one NiuOne first-profit policy from immutable entry context."""
    normalized_regimes = {
        str(value or "").strip().lower() for value in reversal_early_regimes
    }
    if (
        str(strategy_id or "").strip() == "niu_reversal_probe"
        and str(entry_market_regime or "").strip().lower() in normalized_regimes
    ):
        return float(reversal_early_r), float(reversal_early_ratio)
    return float(default_r), float(default_ratio)


def evaluate_shaofu_soft_exit(
    *,
    hold_trading_days: int,
    soft_exit_allowed: bool,
    confirmation_key: str,
    previous_key: str,
    previous_count: int,
    sector_flow_direction: str,
    volume_price_signal: str,
    already_reduced: bool,
    min_hold_trading_days: int = SHAOFU_MIN_HOLD_TRADING_DAYS,
    confirmations_required: int = SHAOFU_SOFT_EXIT_CONFIRMATIONS,
) -> dict[str, Any]:
    """Arbitrate one non-structural Shaofu exit without mutating position state.

    Strong industry inflow or a constructive price/volume relationship vetoes a
    soft exit.  Industry outflow plus bearish projected volume counts as two
    independent confirmations, but can only release half of the position; the
    remaining runner still waits for a structural hard exit.
    """
    if already_reduced:
        return {"status": "runner_hold", "allow_reduce": False, "count": 0, "required": confirmations_required}
    if hold_trading_days < min_hold_trading_days:
        return {"status": "min_hold", "allow_reduce": False, "count": 0, "required": confirmations_required}
    if not soft_exit_allowed:
        return {"status": "morning_hold", "allow_reduce": False, "count": 0, "required": confirmations_required}

    direction = str(sector_flow_direction or "neutral")
    volume_signal = str(volume_price_signal or "neutral")
    if direction == "inflow" or volume_signal == "supportive":
        return {"status": "context_hold", "allow_reduce": False, "count": 0, "required": confirmations_required}

    required = max(1, int(confirmations_required))
    if direction == "outflow" and volume_signal == "bearish":
        required = 1
    count = max(0, int(previous_count))
    if confirmation_key and confirmation_key != previous_key:
        count += 1
    if count < required:
        return {"status": "pending", "allow_reduce": False, "count": count, "required": required}
    return {"status": "confirmed", "allow_reduce": True, "count": count, "required": required}


def evaluate_strategy_time_exit(
    *,
    entry_strategy: str,
    hold_days: int,
    max_pnl_pct: float,
    pnl_pct: float,
    time_exit_allowed: bool,
    b3_exit_allowed: bool,
    b3_exit_hhmm: str,
    time_exit_hhmm: str,
    no_progress_hold_days: int,
    no_progress_max_pnl_pct: float,
    strategy_confirmation_met: bool = False,
    strategy_variant: str = "",
) -> dict[str, Any] | None:
    """Evaluate strategy-specific time-boxed exits."""
    if b3_exit_allowed and entry_strategy == "b3_accelerate" and hold_days >= 1 and max_pnl_pct < 1.0 and pnl_pct <= 0:
        return _sell_signal(
            f"B3次日不涨离场 ({hold_days}d {b3_exit_hhmm}开盘检查，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
            "b3_next_day_no_progress",
        )
    if time_exit_allowed:
        if entry_strategy == "tide_leader" and hold_days >= 5 and max_pnl_pct < 3.0:
            return _sell_signal(
                f"主线领航5日未创新高 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "tide_leader_no_progress",
            )
        if entry_strategy == "tide_rotation" and hold_days >= 3 and max_pnl_pct < 2.0:
            return _sell_signal(
                f"轮动初升3日未延续 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "tide_rotation_no_follow_through",
            )
        if entry_strategy == "tide_recovery" and hold_days >= 2 and max_pnl_pct < 1.5 and pnl_pct <= 0.5:
            return _sell_signal(
                f"冰点修复T+2未确认 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "tide_recovery_unconfirmed",
            )
        if entry_strategy == "niu_leader" and hold_days >= 5 and max_pnl_pct < 3.0:
            return _sell_signal(
                f"牛牛领涨5日未创新高 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "niu_leader_no_progress",
            )
        if entry_strategy == "niu_pullback" and hold_days >= 3 and max_pnl_pct < 2.0:
            return _sell_signal(
                f"牛牛转强3日未恢复强势 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "niu_pullback_no_follow_through",
            )
        if (
            entry_strategy == "niu_reversal_probe"
            and strategy_variant == "daily_v"
            and hold_days >= 3
            and max_pnl_pct < 2.0
        ):
            return _sell_signal(
                f"牛牛试仓3日未延续 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "niu_reversal_no_progress",
            )
        if (
            entry_strategy == "niu_reversal_probe"
            and strategy_variant != "daily_v"
            and hold_days >= 2
        ):
            return _sell_signal(
                f"牛牛试仓T+2仍未升级 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "niu_reversal_not_upgraded",
            )
        if (
            entry_strategy == "niu_reversal_probe"
            and strategy_variant != "daily_v"
            and hold_days >= 1
            and not strategy_confirmation_met
        ):
            return _sell_signal(
                f"牛牛试仓T+1未形成跨日延续 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "niu_reversal_unconfirmed",
            )
        if entry_strategy == "niu_emerging" and hold_days >= 2 and max_pnl_pct < 1.5 and pnl_pct <= 0.5:
            return _sell_signal(
                f"牛牛启动T+2未升级为确认主线 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "niu_emerging_unconfirmed",
            )
        if entry_strategy == "b2_confirm" and hold_days >= 2 and max_pnl_pct < 2.0 and pnl_pct <= 0.5:
            return _sell_signal(
                f"B2确认未延续离场 ({hold_days}d {time_exit_hhmm}尾盘检查，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "b2_no_follow_through",
            )
        if entry_strategy == "super_b1" and hold_days >= no_progress_hold_days and max_pnl_pct < no_progress_max_pnl_pct:
            return _sell_signal(
                f"超级B1只赌一次未兑现离场 ({hold_days}d {time_exit_hhmm}尾盘检查，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "super_b1_no_progress",
            )
    return None
