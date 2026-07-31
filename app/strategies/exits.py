"""Strategy-specific exit rules without market-data or execution side effects."""
from __future__ import annotations

from typing import Any


SHAOFU_MIN_HOLD_TRADING_DAYS = 3
SHAOFU_SOFT_EXIT_CONFIRMATIONS = 2


def _sell_signal(reason: str, signal: str, sell_ratio: float = 1.0) -> dict[str, Any]:
    return {"reason": reason, "signal": signal, "sell_ratio": sell_ratio}


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
                f"牛牛领航5日未创新高 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "niu_leader_no_progress",
            )
        if entry_strategy == "niu_pullback" and hold_days >= 3 and max_pnl_pct < 2.0:
            return _sell_signal(
                f"牛牛回踩3日未恢复强势 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "niu_pullback_no_follow_through",
            )
        if entry_strategy == "niu_reversal_probe" and hold_days >= 2:
            return _sell_signal(
                f"牛牛反转T+2仍未升级 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "niu_reversal_not_upgraded",
            )
        if entry_strategy == "niu_reversal_probe" and hold_days >= 1 and not strategy_confirmation_met:
            return _sell_signal(
                f"牛牛反转T+1未形成跨日延续 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
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
