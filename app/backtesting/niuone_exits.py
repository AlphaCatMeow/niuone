"""Daily-bar replay of the deterministic NiuOne position exit state machine."""
from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date
import math
from typing import Any

try:
    from app.strategies.lifecycle import (
        niuone_lifecycle_stage,
        niuone_lifecycle_transition,
    )
    from app.strategies.exits import (
        NIUONE_LEADER_LOSS_CONFIRMATIONS,
        NIUONE_BREAK_EVEN_AFTER_PARTIAL,
        NIUONE_CLIMAX_RUNNER_ENABLED,
        NIUONE_CLIMAX_RUNNER_LEADER_LOSS_CONFIRMATIONS,
        NIUONE_CLIMAX_RUNNER_TRAILING_ATR,
        NIUONE_INTRADAY_PROFIT_TARGET,
        NIUONE_MAINLINE_WEAK_CONFIRMATIONS,
        NIUONE_MAX_HOLD_CALENDAR_DAYS,
        NIUONE_PARTIAL_TAKE_PROFIT_R,
        NIUONE_PARTIAL_TAKE_PROFIT_RATIO,
        NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_R,
        NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_RATIO,
        NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES,
        NIUONE_REVERSAL_MAINLINE_WEAK_CONFIRMATIONS,
        evaluate_strategy_time_exit,
        niuone_climax_runner_active,
        resolve_niuone_partial_take_profit,
    )
    from app.strategies.niuone_risk import (
        NIUONE_ABSOLUTE_POSITION_CAP_PCT,
        NIUONE_ENTRY_REGIMES,
        NIUONE_MARKUP_MOMENTUM_PROBE_MAX_EXECUTION_GAP_PCT,
        NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
        NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
        NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT,
        NIUONE_MARKUP_REBALANCE_MIN_SESSIONS_AFTER_ADD,
        NIUONE_MARKUP_REBALANCE_PULLBACK_ATR,
        NIUONE_MARKUP_REBALANCE_REBOUND_ATR,
        NIUONE_MARKUP_REBALANCE_STALL_MIN_ATR,
        NIUONE_MARKUP_REBALANCE_STALL_SESSIONS,
        NIUONE_MARKUP_REBALANCE_TRIM_RATIO,
        NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT,
        NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY,
        NIUONE_MAX_OPEN_POSITIONS,
        niuone_markup_momentum_probe_eligible,
        niuone_risk_budget,
        niuone_structure_risk_ok,
    )
    from app.strategies.policy import (
        niuone_markup_rebalance_observation,
        niuone_markup_rebalance_reentry_blocker,
        niuone_markup_upgrade_blocker,
    )
    from app.strategies.sector_tide_risk import (
        SECTOR_TIDE_EXECUTION_BUFFER_PCT,
        effective_loss_distance_pct,
        risk_sized_position_cap_pct,
        stored_position_effective_loss_distance_pct,
    )
except ImportError:  # pragma: no cover - legacy top-level import path
    from strategies.lifecycle import (
        niuone_lifecycle_stage,
        niuone_lifecycle_transition,
    )
    from strategies.exits import (
        NIUONE_LEADER_LOSS_CONFIRMATIONS,
        NIUONE_BREAK_EVEN_AFTER_PARTIAL,
        NIUONE_CLIMAX_RUNNER_ENABLED,
        NIUONE_CLIMAX_RUNNER_LEADER_LOSS_CONFIRMATIONS,
        NIUONE_CLIMAX_RUNNER_TRAILING_ATR,
        NIUONE_INTRADAY_PROFIT_TARGET,
        NIUONE_MAINLINE_WEAK_CONFIRMATIONS,
        NIUONE_MAX_HOLD_CALENDAR_DAYS,
        NIUONE_PARTIAL_TAKE_PROFIT_R,
        NIUONE_PARTIAL_TAKE_PROFIT_RATIO,
        NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_R,
        NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_RATIO,
        NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES,
        NIUONE_REVERSAL_MAINLINE_WEAK_CONFIRMATIONS,
        evaluate_strategy_time_exit,
        niuone_climax_runner_active,
        resolve_niuone_partial_take_profit,
    )
    from strategies.niuone_risk import (
        NIUONE_ABSOLUTE_POSITION_CAP_PCT,
        NIUONE_ENTRY_REGIMES,
        NIUONE_MARKUP_MOMENTUM_PROBE_MAX_EXECUTION_GAP_PCT,
        NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
        NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
        NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT,
        NIUONE_MARKUP_REBALANCE_MIN_SESSIONS_AFTER_ADD,
        NIUONE_MARKUP_REBALANCE_PULLBACK_ATR,
        NIUONE_MARKUP_REBALANCE_REBOUND_ATR,
        NIUONE_MARKUP_REBALANCE_STALL_MIN_ATR,
        NIUONE_MARKUP_REBALANCE_STALL_SESSIONS,
        NIUONE_MARKUP_REBALANCE_TRIM_RATIO,
        NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT,
        NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY,
        NIUONE_MAX_OPEN_POSITIONS,
        niuone_markup_momentum_probe_eligible,
        niuone_risk_budget,
        niuone_structure_risk_ok,
    )
    from strategies.policy import (
        niuone_markup_rebalance_observation,
        niuone_markup_rebalance_reentry_blocker,
        niuone_markup_upgrade_blocker,
    )
    from strategies.sector_tide_risk import (
        SECTOR_TIDE_EXECUTION_BUFFER_PCT,
        effective_loss_distance_pct,
        risk_sized_position_cap_pct,
        stored_position_effective_loss_distance_pct,
    )

from .selection import (
    HistoricalBar,
    PortfolioEntryDecision,
    PositionExitSignal,
    SelectionCostModel,
    SelectionContext,
    SelectionFunction,
    SelectionSignal,
    SelectionStrategy,
)


NIUONE_BACKTEST_INITIAL_CASH = 1_000_000.0
# Preserve the historical backtest-facing name while sharing the production
# policy with Practice. In daily-bar replay one session is one trading day.
NIUONE_MAX_NEW_POSITIONS_PER_SESSION = (
    NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY
)
NIUONE_BOARD_LOT = 100


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _scored_from_signal(signal: SelectionSignal) -> dict[str, Any]:
    value = signal.metadata.get("scored")
    return dict(value) if isinstance(value, Mapping) else {}


def _latest_scored(
    selector: SelectionStrategy | SelectionFunction,
    symbol: str,
    strategy_id: str,
) -> dict[str, Any]:
    reader = getattr(selector, "latest_scored", None)
    if not callable(reader):
        return {}
    value = reader(symbol, strategy_id)
    return dict(value) if isinstance(value, Mapping) else {}


def _calendar_holding_days(entry_date: str, current_date: str) -> int:
    try:
        return max(
            0,
            (date.fromisoformat(current_date) - date.fromisoformat(entry_date)).days,
        )
    except ValueError:
        return 0


class NiuOneDailyExitStrategy:
    """Apply NiuOne's deterministic exits at historical daily closes.

    Daily data cannot reconstruct the production intraday trigger timestamp.
    Structural stops use the completed bar's low and intraday profit targets use
    its high. Stops fill from the stop price, or the open after a worse gap;
    targets fill from the target, or the open after a better gap. A bar touching
    both is conservatively stop-first. Other completed-bar rules fill at the
    close. T+1 is enforced by the lifecycle engine before this policy is called.
    """

    def __init__(
        self,
        *,
        partial_take_profit_r: float = NIUONE_PARTIAL_TAKE_PROFIT_R,
        partial_take_profit_ratio: float = NIUONE_PARTIAL_TAKE_PROFIT_RATIO,
        intraday_profit_target: bool = NIUONE_INTRADAY_PROFIT_TARGET,
        break_even_after_partial: bool = NIUONE_BREAK_EVEN_AFTER_PARTIAL,
        reversal_early_profit_regimes: Collection[str] | None = (
            NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
        ),
        reversal_early_partial_take_profit_r: float = (
            NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_R
        ),
        reversal_early_partial_take_profit_ratio: float = (
            NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_RATIO
        ),
        reversal_mainline_weak_confirmations: int | None = (
            NIUONE_REVERSAL_MAINLINE_WEAK_CONFIRMATIONS
        ),
        daily_v_no_progress_requires_unconfirmed: bool = False,
        holding_upgrade_preserves_strategy: bool = False,
        holding_upgrade_position_cap_pct: float | None = None,
        reversal_max_execution_gap_pct: float | None = None,
        reversal_mainline_peak_drawdown_points: float | None = None,
        reversal_strong_leader_exit_promotion: bool = False,
        reversal_strong_leader_mainline_exit: bool = False,
        daily_v_unconfirmed_failure_hold_days: int | None = None,
        lifecycle_climax_partial_ratio: float | None = None,
        lifecycle_climax_min_pnl_pct: float = 0.0,
        lifecycle_fade_exit: bool = False,
        markup_rebalance_enabled: bool = False,
        markup_rebalance_pullback_atr: float = (
            NIUONE_MARKUP_REBALANCE_PULLBACK_ATR
        ),
        markup_rebalance_stall_sessions: int = (
            NIUONE_MARKUP_REBALANCE_STALL_SESSIONS
        ),
        markup_rebalance_stall_min_atr: float = (
            NIUONE_MARKUP_REBALANCE_STALL_MIN_ATR
        ),
        markup_rebalance_rebound_atr: float = (
            NIUONE_MARKUP_REBALANCE_REBOUND_ATR
        ),
        markup_rebalance_min_sessions_after_add: int = (
            NIUONE_MARKUP_REBALANCE_MIN_SESSIONS_AFTER_ADD
        ),
        markup_rebalance_trim_ratio: float = (
            NIUONE_MARKUP_REBALANCE_TRIM_RATIO
        ),
        climax_runner_enabled: bool = NIUONE_CLIMAX_RUNNER_ENABLED,
        climax_runner_leader_loss_confirmations: int = (
            NIUONE_CLIMAX_RUNNER_LEADER_LOSS_CONFIRMATIONS
        ),
        climax_runner_trailing_atr: float = (
            NIUONE_CLIMAX_RUNNER_TRAILING_ATR
        ),
    ) -> None:
        resolved_r = float(partial_take_profit_r)
        if not 0 < resolved_r <= 10:
            raise ValueError("partial_take_profit_r must be between 0 and 10")
        resolved_ratio = float(partial_take_profit_ratio)
        if not 0 < resolved_ratio < 1:
            raise ValueError("partial_take_profit_ratio must be between 0 and 1")
        resolved_early_r = float(reversal_early_partial_take_profit_r)
        if not 0 < resolved_early_r <= 10:
            raise ValueError(
                "reversal_early_partial_take_profit_r must be between 0 and 10"
            )
        resolved_early_ratio = float(reversal_early_partial_take_profit_ratio)
        if not 0 < resolved_early_ratio < 1:
            raise ValueError(
                "reversal_early_partial_take_profit_ratio must be between 0 and 1"
            )
        resolved_early_regimes = frozenset(
            str(value or "").strip().lower()
            for value in (reversal_early_profit_regimes or ())
            if str(value or "").strip()
        )
        unknown_regimes = resolved_early_regimes - {
            "offensive", "rotation", "recovery", "defensive"
        }
        if unknown_regimes:
            raise ValueError(
                "reversal_early_profit_regimes contains unknown market regimes: "
                + ", ".join(sorted(unknown_regimes))
            )
        if (
            reversal_mainline_weak_confirmations is not None
            and int(reversal_mainline_weak_confirmations) <= 0
        ):
            raise ValueError(
                "reversal_mainline_weak_confirmations must be positive"
            )
        self.partial_take_profit_r = resolved_r
        self.partial_take_profit_ratio = resolved_ratio
        self.intraday_profit_target = bool(intraday_profit_target)
        self.break_even_after_partial = bool(break_even_after_partial)
        self.reversal_early_profit_regimes = resolved_early_regimes
        self.reversal_early_partial_take_profit_r = resolved_early_r
        self.reversal_early_partial_take_profit_ratio = resolved_early_ratio
        self.reversal_mainline_weak_confirmations = (
            int(reversal_mainline_weak_confirmations)
            if reversal_mainline_weak_confirmations is not None else None
        )
        self.daily_v_no_progress_requires_unconfirmed = bool(
            daily_v_no_progress_requires_unconfirmed
        )
        self.holding_upgrade_preserves_strategy = bool(
            holding_upgrade_preserves_strategy
        )
        if (
            holding_upgrade_position_cap_pct is not None
            and not 0 < float(holding_upgrade_position_cap_pct) <= 100
        ):
            raise ValueError(
                "holding_upgrade_position_cap_pct must be between 0 and 100"
            )
        self.holding_upgrade_position_cap_pct = (
            float(holding_upgrade_position_cap_pct)
            if holding_upgrade_position_cap_pct is not None else None
        )
        if reversal_max_execution_gap_pct is not None:
            resolved_execution_gap = float(reversal_max_execution_gap_pct)
            if (
                not math.isfinite(resolved_execution_gap)
                or not -10.0 <= resolved_execution_gap <= 10.0
            ):
                raise ValueError(
                    "reversal_max_execution_gap_pct must be between -10 and 10"
                )
            self.reversal_max_execution_gap_pct = resolved_execution_gap
        else:
            self.reversal_max_execution_gap_pct = None
        if reversal_mainline_peak_drawdown_points is not None:
            resolved_peak_drawdown = float(
                reversal_mainline_peak_drawdown_points
            )
            if (
                not math.isfinite(resolved_peak_drawdown)
                or not 0.0 < resolved_peak_drawdown <= 100.0
            ):
                raise ValueError(
                    "reversal_mainline_peak_drawdown_points must be "
                    "between 0 and 100"
                )
            self.reversal_mainline_peak_drawdown_points = (
                resolved_peak_drawdown
            )
        else:
            self.reversal_mainline_peak_drawdown_points = None
        self.reversal_strong_leader_exit_promotion = bool(
            reversal_strong_leader_exit_promotion
        )
        self.reversal_strong_leader_mainline_exit = bool(
            reversal_strong_leader_mainline_exit
        )
        if (
            daily_v_unconfirmed_failure_hold_days is not None
            and int(daily_v_unconfirmed_failure_hold_days) <= 0
        ):
            raise ValueError(
                "daily_v_unconfirmed_failure_hold_days must be positive"
            )
        self.daily_v_unconfirmed_failure_hold_days = (
            int(daily_v_unconfirmed_failure_hold_days)
            if daily_v_unconfirmed_failure_hold_days is not None else None
        )
        if lifecycle_climax_partial_ratio is not None:
            resolved_climax_ratio = float(lifecycle_climax_partial_ratio)
            if (
                not math.isfinite(resolved_climax_ratio)
                or not 0.0 < resolved_climax_ratio < 1.0
            ):
                raise ValueError(
                    "lifecycle_climax_partial_ratio must be between 0 and 1"
                )
            self.lifecycle_climax_partial_ratio = resolved_climax_ratio
        else:
            self.lifecycle_climax_partial_ratio = None
        resolved_climax_min_pnl = float(lifecycle_climax_min_pnl_pct)
        if (
            not math.isfinite(resolved_climax_min_pnl)
            or not 0.0 <= resolved_climax_min_pnl <= 100.0
        ):
            raise ValueError(
                "lifecycle_climax_min_pnl_pct must be between 0 and 100"
            )
        self.lifecycle_climax_min_pnl_pct = resolved_climax_min_pnl
        self.lifecycle_fade_exit = bool(lifecycle_fade_exit)
        self.markup_rebalance_enabled = bool(markup_rebalance_enabled)
        resolved_pullback_atr = float(markup_rebalance_pullback_atr)
        resolved_stall_sessions = int(markup_rebalance_stall_sessions)
        resolved_stall_min_atr = float(markup_rebalance_stall_min_atr)
        resolved_rebound_atr = float(markup_rebalance_rebound_atr)
        resolved_min_sessions = int(markup_rebalance_min_sessions_after_add)
        resolved_trim_ratio = float(markup_rebalance_trim_ratio)
        if not math.isfinite(resolved_pullback_atr) or not 0 < resolved_pullback_atr <= 5:
            raise ValueError("markup_rebalance_pullback_atr must be between 0 and 5")
        if not 1 <= resolved_stall_sessions <= 20:
            raise ValueError("markup_rebalance_stall_sessions must be between 1 and 20")
        if not math.isfinite(resolved_stall_min_atr) or not 0 <= resolved_stall_min_atr <= 5:
            raise ValueError("markup_rebalance_stall_min_atr must be between 0 and 5")
        if not math.isfinite(resolved_rebound_atr) or not 0 < resolved_rebound_atr <= 5:
            raise ValueError("markup_rebalance_rebound_atr must be between 0 and 5")
        if not 1 <= resolved_min_sessions <= 20:
            raise ValueError(
                "markup_rebalance_min_sessions_after_add must be between 1 and 20"
            )
        if not math.isfinite(resolved_trim_ratio) or not 0 < resolved_trim_ratio < 1:
            raise ValueError("markup_rebalance_trim_ratio must be between 0 and 1")
        self.markup_rebalance_pullback_atr = resolved_pullback_atr
        self.markup_rebalance_stall_sessions = resolved_stall_sessions
        self.markup_rebalance_stall_min_atr = resolved_stall_min_atr
        self.markup_rebalance_rebound_atr = resolved_rebound_atr
        self.markup_rebalance_min_sessions_after_add = resolved_min_sessions
        self.markup_rebalance_trim_ratio = resolved_trim_ratio
        self.climax_runner_enabled = bool(climax_runner_enabled)
        resolved_runner_confirmations = int(
            climax_runner_leader_loss_confirmations
        )
        if not 2 <= resolved_runner_confirmations <= 10:
            raise ValueError(
                "climax_runner_leader_loss_confirmations must be between 2 and 10"
            )
        self.climax_runner_leader_loss_confirmations = (
            resolved_runner_confirmations
        )
        resolved_runner_trailing_atr = float(climax_runner_trailing_atr)
        if (
            not math.isfinite(resolved_runner_trailing_atr)
            or not 2.0 <= resolved_runner_trailing_atr <= 5.0
        ):
            raise ValueError(
                "climax_runner_trailing_atr must be between 2 and 5"
            )
        self.climax_runner_trailing_atr = resolved_runner_trailing_atr

    @staticmethod
    def _climax_partial_done(position: Mapping[str, Any]) -> bool:
        if position.get("niuone_lifecycle_climax_partial_done") is True:
            return True
        trade = position.get("trade")
        exit_legs = (
            trade.get("exit_legs")
            if isinstance(trade, Mapping)
            and isinstance(trade.get("exit_legs"), list)
            else ()
        )
        return any(
            isinstance(leg, Mapping)
            and leg.get("signal") == "niu_lifecycle_climax_partial"
            for leg in exit_legs
        )

    def _partial_take_profit_policy(
        self,
        position: Mapping[str, Any],
    ) -> tuple[float, float]:
        return resolve_niuone_partial_take_profit(
            strategy_id=str(position.get("strategy_id") or ""),
            entry_market_regime=str(
                position.get("entry_market_regime")
                or position.get("market_regime")
                or ""
            ),
            default_r=self.partial_take_profit_r,
            default_ratio=self.partial_take_profit_ratio,
            reversal_early_regimes=self.reversal_early_profit_regimes,
            reversal_early_r=self.reversal_early_partial_take_profit_r,
            reversal_early_ratio=self.reversal_early_partial_take_profit_ratio,
        )

    def _partial_take_profit(
        self,
        position: dict[str, Any],
        bar: HistoricalBar,
        entry_price: float,
        stop_price: float,
    ) -> PositionExitSignal | None:
        initial_risk = (
            entry_price - stop_price if 0 < stop_price < entry_price else 0.0
        )
        target_r, target_ratio = self._partial_take_profit_policy(position)
        target_price = (
            entry_price + target_r * initial_risk
            if initial_risk > 0 else 0.0
        )
        if target_price <= 0:
            return None
        position["partial_target_price"] = target_price
        if abs(target_r - 2.0) <= 1e-12:
            position["two_r_price"] = target_price
        observed_price = (
            float(bar.high) if self.intraday_profit_target else float(bar.close)
        )
        if observed_price < target_price or position.get("partial_tp_done"):
            return None
        fill_reference = (
            max(float(bar.open), target_price)
            if self.intraday_profit_target else None
        )
        r_label = f"{target_r:g}R"
        return PositionExitSignal(
            signal=(
                "niu_2r_partial"
                if abs(target_r - 2.0) <= 1e-12
                else "niu_r_partial"
            ),
            reason=(
                f"牛牛战法达到{r_label}先减仓"
                f"{target_ratio * 100:g}%（"
                f"{'最高' if self.intraday_profit_target else '收盘'}"
                f"{observed_price:.2f} ≥ {r_label}目标{target_price:.2f}）"
            ),
            sell_ratio=target_ratio,
            fill_reference_price=fill_reference,
        )

    def on_entry(
        self,
        signal: SelectionSignal,
        entry_bar: HistoricalBar,
        entry_price: float,
    ) -> Mapping[str, Any]:
        scored = _scored_from_signal(signal)
        stop_price = _number(scored.get("stop_price"), 0.0)
        atr20 = _number(scored.get("atr20") or scored.get("atr"), 0.0)
        state = {
            "entry_stop_price": stop_price,
            "entry_stop_source": str(scored.get("stop_source") or "niu_structure_low"),
            "entry_atr20": atr20,
            "atr20": atr20,
            "reversal_basis": str(scored.get("reversal_basis") or ""),
            "entry_market_regime": str(scored.get("market_regime") or ""),
            "industry": str(scored.get("industry") or entry_bar.industry or ""),
            "highest_price": entry_price,
            "max_pnl_pct": 0.0,
            "mainline_weak_count": 0,
            "mainline_weak_last_session": None,
            "niu_leader_lost_count": 0,
            "niu_leader_lost_last_session": None,
            "niuone_lifecycle_stage": (
                str(scored.get("niuone_lifecycle_stage") or "")
                or niuone_lifecycle_stage(scored)
            ),
            "niuone_entry_subroute": str(
                scored.get("niuone_entry_subroute") or ""
            ),
        }
        if self.reversal_mainline_peak_drawdown_points is not None:
            entry_mainline_score = _number(
                scored.get("mainline_score"),
                math.nan,
            )
            if math.isfinite(entry_mainline_score):
                state["mainline_peak_score"] = entry_mainline_score
                state["mainline_peak_drawdown_points"] = 0.0
        if (
            self.markup_rebalance_enabled
            and signal.strategy_id == "niu_leader"
            and state["niuone_lifecycle_stage"] == "markup"
        ):
            state.update({
                "niuone_markup_rebalance_cycle_peak_price": entry_price,
                "niuone_markup_rebalance_stall_count": 0,
                "niuone_markup_rebalance_observation_count": 0,
                "niuone_markup_rebalance_last_add_date": entry_bar.date,
                "niuone_markup_rebalance_armed": False,
            })
        return state

    def on_close(
        self,
        position: dict[str, Any],
        context: SelectionContext,
        selector: SelectionStrategy | SelectionFunction,
    ) -> PositionExitSignal | None:
        symbol = str(position.get("symbol") or "")
        strategy_id = str(position.get("strategy_id") or "")
        bar = context.bars.get(symbol)
        if bar is None:
            return None
        price = float(bar.close)
        entry_price = _number(
            position.get("avg_cost") or position.get("entry_price"),
            0.0,
        )
        if entry_price <= 0:
            return None
        hold_sessions = max(
            0,
            context.session_index - int(position.get("entry_session_index") or 0),
        )
        pnl_pct = (price / entry_price - 1.0) * 100.0
        observed_high = float(bar.high) if self.intraday_profit_target else price
        highest_price = max(
            _number(position.get("highest_price"), observed_high),
            observed_high,
        )
        position["highest_price"] = highest_price
        observed_pnl_pct = (observed_high / entry_price - 1.0) * 100.0
        max_pnl_pct = max(
            _number(position.get("max_pnl_pct"), observed_pnl_pct),
            observed_pnl_pct,
        )
        position["max_pnl_pct"] = max_pnl_pct

        previous_lifecycle = {
            "mainline_state": position.get("mainline_state"),
            "mainline_cross_day_persistent": position.get(
                "mainline_cross_day_persistent"
            ),
            "mainline_confirmed": position.get("mainline_confirmed"),
            "niuone_lifecycle_stage": position.get(
                "niuone_lifecycle_stage"
            ),
        }
        scored = _latest_scored(selector, symbol, strategy_id)
        if scored:
            for key in (
                "mainline_score", "mainline_state", "mainline_cross_day_persistent",
                "mainline_confirmed", "market_hard_stop", "stock_leader_rank",
                "stock_leader_tier", "stock_strong", "atr20", "atr",
            ):
                if key in scored:
                    position[key] = scored[key]
            if scored.get("industry"):
                position["industry"] = str(scored["industry"])
            position["niuone_lifecycle_stage"] = (
                niuone_lifecycle_transition(previous_lifecycle, scored)
            )

            if (
                (
                    self.reversal_strong_leader_exit_promotion
                    or self.reversal_strong_leader_mainline_exit
                )
                and strategy_id == "niu_reversal_probe"
                and scored.get("stock_leader_tier") is True
                and scored.get("stock_strong") is True
            ):
                position["reversal_strong_leader_exit_promoted"] = True
                position.setdefault(
                    "reversal_strong_leader_exit_promotion_date",
                    context.date,
                )

            mature_leader_exit_identity = bool(
                strategy_id != "niu_reversal_probe"
                or (
                    self.reversal_strong_leader_exit_promotion
                    and position.get("reversal_strong_leader_exit_promoted")
                )
            )

            theme_score = _number(scored.get("mainline_score"), 100.0)
            theme_state = str(scored.get("mainline_state") or "")
            weak = theme_score < 55 or theme_state in {"fading", "inactive"}
            if weak:
                if position.get("mainline_weak_last_session") != context.session_index:
                    position["mainline_weak_count"] = (
                        int(position.get("mainline_weak_count") or 0) + 1
                    )
                    position["mainline_weak_last_session"] = context.session_index
            else:
                position["mainline_weak_count"] = 0
                position["mainline_weak_last_session"] = None

            if self.reversal_mainline_peak_drawdown_points is not None:
                current_mainline_score = _number(
                    scored.get("mainline_score"),
                    math.nan,
                )
                if math.isfinite(current_mainline_score):
                    previous_peak = _number(
                        position.get("mainline_peak_score"),
                        current_mainline_score,
                    )
                    peak_score = max(previous_peak, current_mainline_score)
                    position["mainline_peak_score"] = peak_score
                    position["mainline_peak_drawdown_points"] = max(
                        0.0,
                        peak_score - current_mainline_score,
                    )

            if mature_leader_exit_identity:
                is_current_leader = (
                    scored.get("stock_leader_tier") is True
                    and scored.get("stock_strong") is not False
                )
                if is_current_leader:
                    position["niu_leader_lost_count"] = 0
                    position["niu_leader_lost_last_session"] = None
                elif position.get("niu_leader_lost_last_session") != context.session_index:
                    previous = position.get("niu_leader_lost_last_session")
                    position["niu_leader_lost_count"] = (
                        int(position.get("niu_leader_lost_count") or 0) + 1
                        if previous == context.session_index - 1 else 1
                    )
                    position["niu_leader_lost_last_session"] = context.session_index

        stop_price = _number(position.get("entry_stop_price"), 0.0)
        if self.break_even_after_partial and position.get("partial_tp_done"):
            stop_price = max(stop_price, entry_price)
            position["entry_stop_price"] = stop_price
            position["entry_stop_source"] = "niu_breakeven"
        if stop_price > 0 and float(bar.low) < stop_price:
            stop_labels = {
                "niu_structure_low": "牛牛战法结构低点",
                "niu_breakout_pivot": "牛牛突破位",
                "niu_reversal_low": "牛牛试仓V型低点",
                "niu_reversal_right_low": "牛牛试仓右侧确认低点",
                "niu_breakeven": "牛牛分批止盈后成本保护线",
            }
            stop_source = str(position.get("entry_stop_source") or "")
            stop_label = stop_labels.get(stop_source, "入场止损")
            fill_reference = min(float(bar.open), stop_price)
            return PositionExitSignal(
                signal="niu_structure_stop",
                reason=(
                    f"日内跌破{stop_label}（最低{bar.low:.2f} < 止损{stop_price:.2f}，"
                    f"成交基准{fill_reference:.2f}）"
                ),
                fill_reference_price=fill_reference,
            )

        theme_score = _number(position.get("mainline_score"), 100.0)
        theme_state = str(position.get("mainline_state") or "")
        industry = str(position.get("industry") or "-")
        reversal_exit_promoted = bool(
            strategy_id == "niu_reversal_probe"
            and position.get("reversal_strong_leader_exit_promoted")
        )
        mature_leader_exit_identity = bool(
            strategy_id != "niu_reversal_probe"
            or (
                reversal_exit_promoted
                and self.reversal_strong_leader_exit_promotion
            )
        )
        mature_mainline_exit_identity = bool(
            mature_leader_exit_identity
            or (
                reversal_exit_promoted
                and self.reversal_strong_leader_mainline_exit
            )
        )
        climax_partial_done = self._climax_partial_done(position)
        climax_runner_active = niuone_climax_runner_active(
            enabled=self.climax_runner_enabled,
            climax_partial_done=climax_partial_done,
            partial_tp_done=bool(position.get("partial_tp_done")),
            stock_strong=position.get("stock_strong") is True,
            theme_score=theme_score,
            theme_state=theme_state,
        )
        leader_loss_confirmations = (
            self.climax_runner_leader_loss_confirmations
            if climax_runner_active
            else NIUONE_LEADER_LOSS_CONFIRMATIONS
        )
        if position.get("market_hard_stop") and (
            theme_score < 55 or theme_state in {"fading", "inactive"}
        ):
            return PositionExitSignal(
                signal="niu_market_hard_stop",
                reason=(
                    f"市场硬停止且主线转弱（{industry}分数{theme_score:.1f}，"
                    f"状态{theme_state or '-'}）"
                ),
            )
        lifecycle_stage = str(position.get("niuone_lifecycle_stage") or "")
        if self.lifecycle_fade_exit and lifecycle_stage == "fade":
            return PositionExitSignal(
                signal="niu_lifecycle_fade_exit",
                reason=(
                    "牛牛主线进入退幕阶段，只执行退出"
                    f"（{industry}分数{theme_score:.1f}，"
                    f"状态{theme_state or '-'}）"
                ),
            )
        if mature_leader_exit_identity and int(
            position.get("niu_leader_lost_count") or 0
        ) >= leader_loss_confirmations:
            return PositionExitSignal(
                signal="niu_leader_lost",
                reason=(
                    f"连续{leader_loss_confirmations}个交易日跌出强势行业"
                    f"龙头梯队（{industry}，当前排名"
                    f"{position.get('stock_leader_rank') or '-'}"
                    f"{'，高潮减仓后余仓' if climax_runner_active else ''}）"
                ),
            )
        if mature_mainline_exit_identity and (
            int(position.get("mainline_weak_count") or 0)
            >= NIUONE_MAINLINE_WEAK_CONFIRMATIONS
            or theme_state == "inactive"
        ):
            return PositionExitSignal(
                signal="niu_mainline_faded",
                reason=(
                    f"主线连续转弱（{industry}分数{theme_score:.1f}，"
                    f"状态{theme_state or '-'}）"
                ),
            )

        reversal_weak_required = self.reversal_mainline_weak_confirmations
        if (
            strategy_id == "niu_reversal_probe"
            and not reversal_exit_promoted
            and reversal_weak_required is not None
            and (
                int(position.get("mainline_weak_count") or 0)
                >= reversal_weak_required
                or theme_state == "inactive"
            )
        ):
            return PositionExitSignal(
                signal="niu_reversal_theme_failed",
                reason=(
                    "牛牛试仓所属题材未能维持主线酝酿强度"
                    f"（{industry}分数{theme_score:.1f}，"
                    f"状态{theme_state or '-'}）"
                ),
            )

        if (
            self.markup_rebalance_enabled
            and (
                strategy_id == "niu_leader"
                or position.get("niuone_markup_rebalance_reduced") is True
            )
        ):
            atr20 = _number(
                position.get("atr20")
                or position.get("atr")
                or position.get("entry_atr20"),
                0.0,
            )
            rebalance = niuone_markup_rebalance_observation(
                position,
                current_price=price,
                atr=atr20,
                session_key=context.date,
                lifecycle_stage=lifecycle_stage,
                current_pnl_pct=pnl_pct,
                strong_leader=bool(
                    position.get("stock_leader_tier") is True
                    and position.get("stock_strong") is True
                ),
                pullback_atr=self.markup_rebalance_pullback_atr,
                stall_sessions=self.markup_rebalance_stall_sessions,
                stall_min_atr=self.markup_rebalance_stall_min_atr,
                minimum_sessions_after_add=(
                    self.markup_rebalance_min_sessions_after_add
                ),
            )
            position.update(dict(rebalance.get("state") or {}))
            if rebalance.get("arm_existing_reduction") is True:
                position.update({
                    "niuone_markup_rebalance_armed": True,
                    "niuone_markup_rebalance_armed_date": context.date,
                    "niuone_markup_rebalance_reentry_price": round(
                        price + self.markup_rebalance_rebound_atr * atr20,
                        4,
                    ),
                    "niuone_markup_rebalance_last_trigger": str(
                        rebalance.get("trigger") or ""
                    ),
                    "niuone_markup_rebalance_arm_count": (
                        int(
                            position.get(
                                "niuone_markup_rebalance_arm_count"
                            ) or 0
                        ) + 1
                    ),
                })
            if rebalance.get("trim") is True:
                trigger_label = (
                    "回落"
                    if rebalance.get("trigger") == "pullback"
                    else "横盘"
                )
                return PositionExitSignal(
                    signal="niu_markup_rebalance_partial",
                    reason=(
                        f"牛牛主升{trigger_label}释放波段仓位"
                        f"（距周期高点{rebalance.get('drawdown_atr', 0):g}ATR，"
                        f"横盘计数{rebalance.get('stall_count', 0)}）"
                    ),
                    sell_ratio=self.markup_rebalance_trim_ratio,
                )

        climax_ratio = self.lifecycle_climax_partial_ratio
        climax_partial_deferred = (
            position.get("deferred_exit_signal")
            == "niu_lifecycle_climax_partial"
        )
        if (
            climax_ratio is not None
            and not climax_partial_done
            and (lifecycle_stage == "climax" or climax_partial_deferred)
            and pnl_pct >= self.lifecycle_climax_min_pnl_pct - 1e-9
        ):
            return PositionExitSignal(
                signal="niu_lifecycle_climax_partial",
                reason=(
                    "牛牛主线进入高潮阶段，按收盘锁定部分利润"
                    f"（{industry}分数{theme_score:.1f}，"
                    f"现盈亏{pnl_pct:.1f}%）"
                ),
                sell_ratio=climax_ratio,
            )

        peak_drawdown_limit = self.reversal_mainline_peak_drawdown_points
        peak_drawdown = _number(
            position.get("mainline_peak_drawdown_points"),
            0.0,
        )
        if (
            strategy_id == "niu_reversal_probe"
            and peak_drawdown_limit is not None
            and hold_sessions >= 1
            and peak_drawdown >= peak_drawdown_limit - 1e-9
        ):
            return PositionExitSignal(
                signal="niu_reversal_mainline_peak_decay",
                reason=(
                    "牛牛试仓所属主线从持仓期峰值回落"
                    f"{peak_drawdown:.2f}分（研究上限"
                    f"{peak_drawdown_limit:g}分）"
                ),
            )

        partial_take_profit = self._partial_take_profit(
            position,
            bar,
            entry_price,
            stop_price,
        )
        if self.intraday_profit_target and partial_take_profit is not None:
            return partial_take_profit

        strategy_confirmation_met = bool(
            position.get("mainline_cross_day_persistent")
            or position.get("mainline_confirmed")
        )
        early_failure_days = self.daily_v_unconfirmed_failure_hold_days
        if (
            strategy_id == "niu_reversal_probe"
            and str(position.get("reversal_basis") or "") == "daily_v"
            and early_failure_days is not None
            and hold_sessions >= early_failure_days
            and not strategy_confirmation_met
            and max_pnl_pct < 2.0
            and pnl_pct <= 0.0
        ):
            return PositionExitSignal(
                signal="niu_reversal_unconfirmed_failure",
                reason=(
                    "牛牛试仓未形成跨日主线且价格仍未延续"
                    f"（{hold_sessions}d，最高盈利{max_pnl_pct:.1f}%，"
                    f"现盈亏{pnl_pct:.1f}%）"
                ),
            )
        time_exit = evaluate_strategy_time_exit(
            entry_strategy=(
                "niu_leader"
                if reversal_exit_promoted
                and self.reversal_strong_leader_exit_promotion
                else strategy_id
            ),
            hold_days=hold_sessions,
            max_pnl_pct=max_pnl_pct,
            pnl_pct=pnl_pct,
            time_exit_allowed=True,
            b3_exit_allowed=False,
            b3_exit_hhmm="09:37",
            time_exit_hhmm="14:45",
            no_progress_hold_days=3,
            no_progress_max_pnl_pct=1.0,
            strategy_confirmation_met=strategy_confirmation_met,
            strategy_variant=str(position.get("reversal_basis") or ""),
        )
        if (
            reversal_exit_promoted
            and self.reversal_strong_leader_mainline_exit
            and time_exit
            and time_exit.get("signal") in {
                "niu_reversal_no_progress",
                "niu_reversal_not_upgraded",
                "niu_reversal_unconfirmed",
            }
        ):
            time_exit = None
        if (
            time_exit
            and self.daily_v_no_progress_requires_unconfirmed
            and strategy_confirmation_met
            and time_exit.get("signal") == "niu_reversal_no_progress"
        ):
            time_exit = None
        if time_exit:
            return PositionExitSignal(
                signal=str(time_exit["signal"]),
                reason=str(time_exit["reason"]),
                sell_ratio=float(time_exit.get("sell_ratio") or 1.0),
            )

        if partial_take_profit is not None:
            return partial_take_profit

        initial_risk = (
            entry_price - stop_price if 0 < stop_price < entry_price else 0.0
        )
        target_r, _target_ratio = self._partial_take_profit_policy(position)
        target_price = (
            entry_price + target_r * initial_risk
            if initial_risk > 0 else 0.0
        )

        atr20 = _number(
            position.get("atr20") or position.get("atr")
            or position.get("entry_atr20"),
            0.0,
        )
        if atr20 > 0 and (
            position.get("partial_tp_done")
            or (target_price > 0 and highest_price >= target_price)
        ):
            trailing_atr = (
                self.climax_runner_trailing_atr
                if climax_runner_active
                else 2.0
            )
            trailing_stop = highest_price - trailing_atr * atr20
            position["niu_trailing_stop"] = trailing_stop
            if trailing_stop > entry_price and price <= trailing_stop:
                return PositionExitSignal(
                    signal="niu_atr_trail",
                    reason=(
                        f"牛牛战法{trailing_atr:g}ATR跟踪退出（收盘{price:.2f} ≤ "
                        f"跟踪线{trailing_stop:.2f}）"
                    ),
                )

        calendar_days = _calendar_holding_days(
            str(position.get("entry_date") or ""),
            context.date,
        )
        if calendar_days >= NIUONE_MAX_HOLD_CALENDAR_DAYS:
            return PositionExitSignal(
                signal="max_hold_days",
                reason=(
                    f"持仓到期（{calendar_days}天 ≥ "
                    f"{NIUONE_MAX_HOLD_CALENDAR_DAYS}天）"
                ),
            )
        return None

    def on_exit_filled(
        self,
        position: dict[str, Any],
        decision: PositionExitSignal,
        leg: Mapping[str, Any],
        context: SelectionContext,
    ) -> None:
        """Arm exactly one re-entry only after a wave trim actually fills."""
        if decision.signal in {"niu_r_partial", "niu_2r_partial"}:
            fill_price = _number(leg.get("price"), 0.0)
            if fill_price > 0:
                position.update({
                    "niuone_markup_rebalance_reduced": True,
                    "niuone_markup_rebalance_cycle_peak_price": round(
                        fill_price,
                        4,
                    ),
                    "niuone_markup_rebalance_stall_count": 0,
                    "niuone_markup_rebalance_observation_count": 0,
                    "niuone_markup_rebalance_last_observation": context.date,
                    "niuone_markup_rebalance_reduction_source": (
                        decision.signal
                    ),
                })
            return
        if decision.signal != "niu_markup_rebalance_partial":
            return
        fill_price = _number(leg.get("price"), 0.0)
        atr20 = _number(
            position.get("atr20")
            or position.get("atr")
            or position.get("entry_atr20"),
            0.0,
        )
        if fill_price <= 0 or atr20 <= 0:
            return
        position.update({
            "niuone_markup_rebalance_armed": True,
            "niuone_markup_rebalance_reduced": True,
            "niuone_markup_rebalance_armed_date": context.date,
            "niuone_markup_rebalance_reentry_price": round(
                fill_price + self.markup_rebalance_rebound_atr * atr20,
                4,
            ),
            "niuone_markup_rebalance_trim_count": (
                int(position.get("niuone_markup_rebalance_trim_count") or 0)
                + 1
            ),
            "niuone_markup_rebalance_last_trim_price": round(fill_price, 4),
            "niuone_markup_rebalance_last_trigger": (
                "pullback"
                if _number(
                    position.get("niuone_markup_rebalance_drawdown_atr"),
                    0.0,
                ) + 1e-9 >= self.markup_rebalance_pullback_atr
                else "sideways"
            ),
        })


class NiuOneStrategyBacktestPolicy(NiuOneDailyExitStrategy):
    """Replay NiuOne entries, upgrades, portfolio risk and exits as one book."""

    portfolio_mode = True
    initial_cash = NIUONE_BACKTEST_INITIAL_CASH
    max_new_positions_per_session = NIUONE_MAX_NEW_POSITIONS_PER_SESSION
    board_lot = NIUONE_BOARD_LOT

    def __init__(
        self,
        *,
        max_new_positions_per_session: int = (
            NIUONE_MAX_NEW_POSITIONS_PER_SESSION
        ),
        max_open_positions: int = NIUONE_MAX_OPEN_POSITIONS,
        max_industry_positions: int = 2,
        entry_order_scale: float = 1.0,
        risk_budget_scale: float = 1.0,
        position_budget_scale: float = 1.0,
        reversal_entry_position_cap_pct: float | None = None,
        holding_upgrade_early_position_cap_pct: float | None = None,
        markup_upgrade_only: bool = False,
        **exit_options: Any,
    ) -> None:
        super().__init__(**exit_options)
        resolved_limit = int(max_new_positions_per_session)
        if resolved_limit <= 0:
            raise ValueError("max_new_positions_per_session must be positive")
        self.max_new_positions_per_session = resolved_limit
        resolved_open_limit = int(max_open_positions)
        if resolved_open_limit <= 0:
            raise ValueError("max_open_positions must be positive")
        self.max_open_positions = resolved_open_limit
        resolved_industry_limit = int(max_industry_positions)
        if resolved_industry_limit <= 0:
            raise ValueError("max_industry_positions must be positive")
        self.max_industry_positions = resolved_industry_limit
        resolved_scale = float(entry_order_scale)
        if not math.isfinite(resolved_scale) or not 0 < resolved_scale <= 1:
            raise ValueError("entry_order_scale must be within (0, 1]")
        self.entry_order_scale = resolved_scale
        resolved_risk_scale = float(risk_budget_scale)
        if (
            not math.isfinite(resolved_risk_scale)
            or not 0 < resolved_risk_scale <= 2.0
        ):
            raise ValueError("risk_budget_scale must be within (0, 2]")
        self.risk_budget_scale = resolved_risk_scale
        resolved_position_scale = float(position_budget_scale)
        if (
            not math.isfinite(resolved_position_scale)
            or not 0 < resolved_position_scale <= 2.0
        ):
            raise ValueError("position_budget_scale must be within (0, 2]")
        self.position_budget_scale = resolved_position_scale
        if reversal_entry_position_cap_pct is not None:
            resolved_reversal_cap = float(reversal_entry_position_cap_pct)
            if (
                not math.isfinite(resolved_reversal_cap)
                or not 0 < resolved_reversal_cap <= 30
            ):
                raise ValueError(
                    "reversal_entry_position_cap_pct must be within (0, 30]"
                )
            self.reversal_entry_position_cap_pct = resolved_reversal_cap
        else:
            self.reversal_entry_position_cap_pct = None
        if holding_upgrade_early_position_cap_pct is not None:
            resolved_early_cap = float(
                holding_upgrade_early_position_cap_pct
            )
            if (
                not math.isfinite(resolved_early_cap)
                or not 0 < resolved_early_cap <= 100
            ):
                raise ValueError(
                    "holding_upgrade_early_position_cap_pct must be "
                    "between 0 and 100"
                )
            self.holding_upgrade_early_position_cap_pct = resolved_early_cap
        else:
            self.holding_upgrade_early_position_cap_pct = None
        self.markup_upgrade_only = bool(markup_upgrade_only)

    def _risk_budget(
        self,
        regime: str,
        strategy_id: str,
    ) -> dict[str, float]:
        """Apply a request-scoped backtest risk profile to production budgets.

        Single-name absolute caps and structural-stop validity remain unchanged.
        The scale only changes account-risk and portfolio/theme exposure budgets,
        so an aggressive replay can accept more drawdown without manufacturing
        price setups or weakening live-trading controls.
        """
        budget = niuone_risk_budget(regime, strategy_id)
        for key in (
            "per_trade_risk_pct",
            "max_open_risk_pct",
            "max_sector_risk_pct",
        ):
            budget[key] *= self.risk_budget_scale
        budget["max_total_position_pct"] = min(
            95.0,
            budget["max_total_position_pct"] * self.position_budget_scale,
        )
        budget["max_sector_position_pct"] = min(
            80.0,
            budget["max_sector_position_pct"] * self.position_budget_scale,
        )
        return budget

    @staticmethod
    def _entry_scored(signal: SelectionSignal) -> dict[str, Any]:
        return _scored_from_signal(signal)

    def schedule_block_reason(
        self,
        position: Mapping[str, Any] | None,
        signal: SelectionSignal,
        signal_date: str,
    ) -> str:
        """Return why an existing holding cannot accept this strategy signal."""
        if not position:
            if signal.metadata.get("holding_upgrade") is True:
                return "holding_upgrade_missing_position"
            return ""
        existing = str(position.get("strategy_id") or "")
        incoming = str(signal.strategy_id or "")
        scored = self._entry_scored(signal)
        lots = position.get("lots") or ()
        bought_today = any(
            str(lot.get("date") or "") == signal_date
            for lot in lots
            if isinstance(lot, Mapping)
        )
        upgrade_source = str(
            position.get("niuone_markup_upgrade_source_strategy_id")
            or existing
        )
        rebalance_reentry = bool(
            signal.metadata.get("niuone_markup_rebalance_reentry") is True
            or (
                self.markup_rebalance_enabled
                and incoming == "niu_leader"
                and position.get("niuone_markup_rebalance_armed") is True
            )
        )
        if (
            self.markup_upgrade_only
            and (
                upgrade_source in {"niu_reversal_probe", "niu_emerging"}
                or rebalance_reentry
            )
        ):
            if bought_today:
                return "markup_upgrade_same_day_add"
            current_price = _number(position.get("last_price"), 0.0)
            avg_cost = _number(position.get("avg_cost"), 0.0)
            current_pnl_pct = (
                (current_price / avg_cost - 1.0) * 100.0
                if current_price > 0 and avg_cost > 0
                else 0.0
            )
            candidate = {**scored, "strategy_id": incoming}
            if rebalance_reentry:
                blocker = niuone_markup_rebalance_reentry_blocker(
                    upgrade_source,
                    position,
                    candidate,
                    current_price=current_price,
                    current_pnl_pct=current_pnl_pct,
                )
            else:
                if (
                    incoming == "niu_emerging"
                    and position.get("niuone_markup_early_scale_in_done") is True
                ):
                    return "markup_upgrade_early_done"
                if (
                    incoming == "niu_leader"
                    and position.get("niuone_markup_confirmed_scale_in_done") is True
                ):
                    return "markup_upgrade_confirmed_done"
                blocker = niuone_markup_upgrade_blocker(
                    upgrade_source,
                    candidate,
                    current_pnl_pct=current_pnl_pct,
                )
            if blocker:
                return "markup_rebalance_rule" if rebalance_reentry else "markup_upgrade_rule"
            return ""
        if (
            self.markup_rebalance_enabled
            and existing == "niu_leader"
            and incoming == "niu_leader"
        ):
            if bought_today:
                return "markup_upgrade_same_day_add"
            current_price = _number(position.get("last_price"), 0.0)
            avg_cost = _number(position.get("avg_cost"), 0.0)
            current_pnl_pct = (
                (current_price / avg_cost - 1.0) * 100.0
                if current_price > 0 and avg_cost > 0
                else 0.0
            )
            blocker = niuone_markup_rebalance_reentry_blocker(
                existing,
                position,
                {**scored, "strategy_id": incoming},
                current_price=current_price,
                current_pnl_pct=current_pnl_pct,
            )
            if blocker:
                return "markup_rebalance_rule"
            return ""
        if existing == "niu_reversal_probe":
            if bought_today:
                return "reversal_same_day_add"
            if incoming == "niu_emerging" and (
                scored.get("mainline_cross_day_persistent") is True
                and str(scored.get("mainline_state") or "") == "emerging"
            ):
                return ""
            if incoming in {"niu_leader", "niu_pullback"} and (
                scored.get("mainline_confirmed") is True
                and str(scored.get("mainline_state") or "")
                in {"mainline", "diverging"}
            ):
                return ""
            return "reversal_upgrade_unconfirmed"
        if existing == "niu_emerging":
            if incoming in {"niu_leader", "niu_pullback"} and (
                scored.get("mainline_confirmed") is True
                and str(scored.get("mainline_state") or "")
                in {"mainline", "diverging"}
            ):
                return ""
            return "emerging_upgrade_unconfirmed"
        if existing != incoming:
            return "mixed_strategy_add"
        return ""

    @staticmethod
    def _marked_value(position: Mapping[str, Any], marks: Mapping[str, float]) -> float:
        symbol = str(position.get("symbol") or "")
        price = _number(marks.get(symbol), _number(position.get("last_price"), 0.0))
        return max(0.0, _number(position.get("remaining_units"), 0.0) * price)

    def _existing_open_risk(
        self,
        positions: Mapping[str, Mapping[str, Any]],
        marks: Mapping[str, float],
        total_equity: float,
        *,
        excluding_symbol: str,
        industry: str | None = None,
    ) -> float:
        total = 0.0
        if total_equity <= 0:
            return 100.0
        for symbol, position in positions.items():
            if symbol == excluding_symbol:
                continue
            if industry is not None and str(position.get("industry") or "") != industry:
                continue
            mark = _number(marks.get(symbol), _number(position.get("last_price"), 0.0))
            value = self._marked_value(position, marks)
            distance = stored_position_effective_loss_distance_pct(
                dict(position),
                mark_price=mark,
            )
            total += value / total_equity * distance
        return total

    def size_entry(
        self,
        signal: SelectionSignal,
        entry_bar: HistoricalBar,
        entry_price: float,
        position: Mapping[str, Any] | None,
        positions: Mapping[str, Mapping[str, Any]],
        marks: Mapping[str, float],
        cash: float,
        total_equity: float,
        new_positions_today: int,
        cost_model: SelectionCostModel,
    ) -> PortfolioEntryDecision:
        """Size to the same dynamic hard limits enforced by paper trading."""
        scored = self._entry_scored(signal)
        strategy_id = str(signal.strategy_id or "")
        entry_subroute = str(scored.get("niuone_entry_subroute") or "")
        is_add = bool(position and _number(position.get("remaining_units"), 0.0) > 0)
        action = "add" if is_add else "open"
        if strategy_id not in NIUONE_ABSOLUTE_POSITION_CAP_PCT:
            return PortfolioEntryDecision(0, "reject", "unsupported_strategy")
        if (
            strategy_id == "niu_emerging"
            and entry_subroute == NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE
            and not niuone_markup_momentum_probe_eligible(scored)
        ):
            return PortfolioEntryDecision(
                0,
                "reject",
                "markup_momentum_identity_block",
            )
        if (
            not is_add
            and strategy_id == "niu_reversal_probe"
            and self.reversal_max_execution_gap_pct is not None
        ):
            signal_close = _number(scored.get("recent_close"), 0.0)
            if signal_close <= 0:
                return PortfolioEntryDecision(
                    0,
                    "reject",
                    "missing_signal_close",
                )
            execution_gap_pct = (
                entry_bar.open / signal_close - 1.0
            ) * 100.0
            if execution_gap_pct > self.reversal_max_execution_gap_pct + 1e-9:
                return PortfolioEntryDecision(
                    0,
                    "reject",
                    "reversal_execution_gap",
                )
        if (
            not is_add
            and strategy_id == "niu_emerging"
            and entry_subroute == NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE
        ):
            signal_close = _number(scored.get("recent_close"), 0.0)
            if signal_close <= 0:
                return PortfolioEntryDecision(
                    0,
                    "reject",
                    "missing_signal_close",
                )
            execution_gap_pct = (
                entry_bar.open / signal_close - 1.0
            ) * 100.0
            if (
                execution_gap_pct
                > NIUONE_MARKUP_MOMENTUM_PROBE_MAX_EXECUTION_GAP_PCT + 1e-9
            ):
                return PortfolioEntryDecision(
                    0,
                    "reject",
                    "markup_momentum_execution_gap",
                )
        if not is_add and len(positions) >= self.max_open_positions:
            return PortfolioEntryDecision(0, "reject", "max_open_positions")
        if (
            not is_add
            and new_positions_today >= self.max_new_positions_per_session
        ):
            return PortfolioEntryDecision(0, "reject", "max_new_positions")

        regime = str(scored.get("market_regime") or "")
        if (
            regime not in NIUONE_ENTRY_REGIMES
            or scored.get("market_hard_stop") is True
            or scored.get("market_allows_buys") is not True
        ):
            return PortfolioEntryDecision(0, "reject", "market_risk_block")
        industry = str(scored.get("industry") or entry_bar.industry or "").strip()
        if not industry:
            return PortfolioEntryDecision(0, "reject", "missing_industry")

        same_industry = [
            item for symbol, item in positions.items()
            if symbol != signal.symbol
            and str(item.get("industry") or "").strip() == industry
            and _number(item.get("remaining_units"), 0.0) > 0
        ]
        if (
            not is_add
            and len(same_industry) >= self.max_industry_positions
        ):
            return PortfolioEntryDecision(0, "reject", "max_industry_positions")

        candidate_stop = _number(scored.get("stop_price"), 0.0)
        existing_stop = _number((position or {}).get("entry_stop_price"), 0.0)
        stop_price = max(candidate_stop, existing_stop)
        atr = _number(scored.get("atr20") or scored.get("atr"), 0.0)
        # Structural eligibility is a strategy decision made against the
        # observable market open.  The synthetic backtest slippage belongs to
        # fill cost and position sizing; letting it move the hard-stop gate can
        # reject an otherwise valid open that sits exactly on the risk limit.
        structure_reference_price = float(entry_bar.open)
        stop_distance_pct = (
            (structure_reference_price - stop_price)
            / structure_reference_price
            * 100.0
            if 0 < stop_price < structure_reference_price else 0.0
        )
        stop_atr = (
            (structure_reference_price - stop_price) / atr
            if atr > 0 and 0 < stop_price < structure_reference_price else 0.0
        )
        if not niuone_structure_risk_ok(
            stop_distance_pct,
            stop_atr,
            regime,
            strategy_id,
            entry_subroute,
        ):
            return PortfolioEntryDecision(0, "reject", "structure_risk_block")

        gap_buffer_pct = max(
            _number(scored.get("gap_buffer_pct"), 0.0),
            _number((position or {}).get("gap_buffer_pct"), 0.0),
        )
        if gap_buffer_pct <= 0:
            return PortfolioEntryDecision(0, "reject", "missing_gap_buffer")
        execution_buffer_pct = max(
            SECTOR_TIDE_EXECUTION_BUFFER_PCT,
            _number(scored.get("execution_buffer_pct"), 0.0),
            _number((position or {}).get("execution_buffer_pct"), 0.0),
        )
        effective_distance = effective_loss_distance_pct(
            entry_price,
            stop_price,
            gap_buffer_pct=gap_buffer_pct,
            execution_buffer_pct=execution_buffer_pct,
        )
        budget = self._risk_budget(regime, strategy_id)
        absolute_cap = float(NIUONE_ABSOLUTE_POSITION_CAP_PCT[strategy_id])
        if (
            not is_add
            and strategy_id == "niu_emerging"
            and entry_subroute == NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE
        ):
            absolute_cap = min(
                absolute_cap,
                NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
            )
        if (
            not is_add
            and strategy_id == "niu_reversal_probe"
            and self.reversal_entry_position_cap_pct is not None
        ):
            absolute_cap = self.reversal_entry_position_cap_pct
        production_markup_add = bool(
            is_add
            and self.markup_upgrade_only
            and (
                str(
                (position or {}).get(
                    "niuone_markup_upgrade_source_strategy_id"
                )
                or (position or {}).get("strategy_id")
                or ""
                ) in {"niu_reversal_probe", "niu_emerging"}
                or signal.metadata.get("niuone_markup_rebalance_reentry") is True
                or (position or {}).get("niuone_markup_rebalance_armed") is True
            )
            and strategy_id in {"niu_emerging", "niu_leader"}
        )
        if production_markup_add:
            source = str(
                (position or {}).get("niuone_markup_upgrade_source_strategy_id")
                or (position or {}).get("strategy_id")
                or ""
            )
            if strategy_id == "niu_emerging":
                stage_cap = NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT
            elif source in {"niu_reversal_probe", "niu_emerging"}:
                stage_cap = NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT
            else:
                stage_cap = NIUONE_ABSOLUTE_POSITION_CAP_PCT["niu_leader"]
            absolute_cap = min(
                absolute_cap,
                stage_cap,
            )
        elif (
            is_add
            and signal.metadata.get("holding_upgrade") is True
            and strategy_id == "niu_emerging"
            and self.holding_upgrade_early_position_cap_pct is not None
        ):
            absolute_cap = min(
                absolute_cap,
                self.holding_upgrade_early_position_cap_pct,
            )
        elif (
            is_add
            and signal.metadata.get("holding_upgrade") is True
            and self.holding_upgrade_position_cap_pct is not None
        ):
            absolute_cap = min(
                absolute_cap,
                self.holding_upgrade_position_cap_pct,
            )
        dynamic_cap = risk_sized_position_cap_pct(
            per_trade_risk_pct=budget["per_trade_risk_pct"],
            effective_loss_distance_pct_value=effective_distance,
            absolute_cap_pct=absolute_cap,
        )
        if total_equity <= 0 or dynamic_cap <= 0 or effective_distance <= 0:
            return PortfolioEntryDecision(0, "reject", "risk_budget_unavailable")

        current_value = self._marked_value(position or {}, marks)
        market_value = sum(self._marked_value(item, marks) for item in positions.values())
        other_industry_value = sum(self._marked_value(item, marks) for item in same_industry)
        other_open_risk = self._existing_open_risk(
            positions,
            marks,
            total_equity,
            excluding_symbol=signal.symbol,
        )
        other_industry_risk = self._existing_open_risk(
            positions,
            marks,
            total_equity,
            excluding_symbol=signal.symbol,
            industry=industry,
        )
        risk_value_cap = (
            max(0.0, budget["max_open_risk_pct"] - other_open_risk)
            / effective_distance * total_equity
        )
        industry_risk_value_cap = (
            max(0.0, budget["max_sector_risk_pct"] - other_industry_risk)
            / effective_distance * total_equity
        )
        target_value = min(
            dynamic_cap / 100.0 * total_equity,
            risk_value_cap,
            industry_risk_value_cap,
            budget["max_sector_position_pct"] / 100.0 * total_equity
            - other_industry_value,
            budget["max_total_position_pct"] / 100.0 * total_equity
            - (market_value - current_value),
        )
        required_cash = (
            100.0 - budget["max_total_position_pct"]
        ) / 100.0 * total_equity
        spendable_cash = max(0.0, cash - required_cash)
        maximum_order_value = min(
            max(0.0, target_value - current_value),
            spendable_cash,
        )
        # Practice accepts the model's explicit board-lot order when it is no
        # larger than the deterministic risk ceiling.  Scale that allowable
        # incremental order—not the strategy ceiling itself—so research can
        # measure how much portfolio performance assumes every order is filled
        # at the maximum permitted size.  Production remains 1.0.
        order_value = maximum_order_value * self.entry_order_scale
        units = int(math.floor(order_value / entry_price / self.board_lot)) * self.board_lot
        while units > 0:
            gross = units * entry_price
            if gross + cost_model.entry_fee(gross) <= spendable_cash + 1e-9:
                break
            units -= self.board_lot
        if units <= 0:
            return PortfolioEntryDecision(
                0,
                "reject",
                "target_position_reached" if is_add else "below_board_lot",
            )

        return PortfolioEntryDecision(
            units,
            action,
            state={
                "entry_stop_price": stop_price,
                "entry_stop_source": str(
                    scored.get("stop_source") or (position or {}).get("entry_stop_source")
                    or "niu_structure_low"
                ),
                "entry_atr20": atr,
                "atr20": atr,
                "industry": industry,
                "gap_buffer_pct": gap_buffer_pct,
                "execution_buffer_pct": execution_buffer_pct,
                "effective_loss_distance_pct": effective_distance,
                "market_regime": regime,
                "niuone_entry_subroute": entry_subroute,
                "target_position_pct": dynamic_cap,
                "position_before_trade_pct": (
                    current_value / total_equity * 100.0
                ),
                "order_position_pct": (
                    units * entry_price / total_equity * 100.0
                ),
                "position_after_trade_pct": (
                    (current_value + units * entry_price)
                    / total_equity * 100.0
                ),
            },
        )

    def on_add(
        self,
        position: Mapping[str, Any],
        signal: SelectionSignal,
        entry_bar: HistoricalBar,
        entry_price: float,
    ) -> Mapping[str, Any]:
        """Update only strategy state that legitimately changes on an upgrade."""
        scored = self._entry_scored(signal)
        state = {
            "reversal_basis": str(
                scored.get("reversal_basis") or position.get("reversal_basis") or ""
            ),
            "industry": str(
                scored.get("industry") or position.get("industry")
                or entry_bar.industry or ""
            ),
            "highest_price": max(
                _number(position.get("highest_price"), entry_price),
                entry_price,
            ),
        }
        source_strategy_id = str(position.get("strategy_id") or "")
        if (
            signal.strategy_id in {"niu_emerging", "niu_leader"}
            and (
                source_strategy_id in {
                    "niu_reversal_probe", "niu_emerging", "niu_leader"
                }
                or signal.metadata.get("niuone_markup_rebalance_reentry") is True
            )
            and (
                self.markup_upgrade_only
                or signal.metadata.get("holding_upgrade") is True
            )
        ):
            early_scale_in = signal.strategy_id == "niu_emerging"
            markup_source_strategy_id = str(
                position.get("niuone_markup_upgrade_source_strategy_id")
                or source_strategy_id
            )
            scale_in_cap = (
                NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT
                if self.markup_upgrade_only and early_scale_in
                else (
                    (
                        NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT
                        if markup_source_strategy_id
                        in {"niu_reversal_probe", "niu_emerging"}
                        else NIUONE_ABSOLUTE_POSITION_CAP_PCT["niu_leader"]
                    )
                    if self.markup_upgrade_only
                    else (
                        self.holding_upgrade_early_position_cap_pct
                        if early_scale_in
                        else self.holding_upgrade_position_cap_pct
                    )
                )
            )
            state.update({
                "niuone_markup_scale_in": True,
                "niuone_markup_upgrade_source_strategy_id": (
                    markup_source_strategy_id
                ),
                "niuone_markup_scale_in_cap_pct": scale_in_cap,
                "niuone_markup_scale_in_tier": (
                    "early" if early_scale_in else "confirmed"
                ),
                (
                    "niuone_markup_early_scale_in_done"
                    if early_scale_in
                    else "niuone_markup_confirmed_scale_in_done"
                ): True,
            })
            if (
                self.markup_rebalance_enabled
                and signal.strategy_id == "niu_leader"
            ):
                rebalance_reentry = bool(
                    signal.metadata.get("niuone_markup_rebalance_reentry")
                    is True
                    or position.get("niuone_markup_rebalance_armed") is True
                )
                state.update({
                    "niuone_markup_rebalance_cycle_peak_price": entry_price,
                    "niuone_markup_rebalance_stall_count": 0,
                    "niuone_markup_rebalance_observation_count": 0,
                    "niuone_markup_rebalance_last_observation": entry_bar.date,
                    "niuone_markup_rebalance_last_add_date": entry_bar.date,
                    "niuone_markup_rebalance_armed": False,
                    "niuone_markup_rebalance_reduced": False,
                    "niuone_markup_rebalance_reentry_price": None,
                })
                if rebalance_reentry:
                    state["niuone_markup_rebalance_reentry_count"] = (
                        int(
                            position.get(
                                "niuone_markup_rebalance_reentry_count"
                            ) or 0
                        ) + 1
                    )
        return state

    def strategy_id_after_add(
        self,
        position: Mapping[str, Any],
        signal: SelectionSignal,
    ) -> str:
        """Optionally isolate upgrade sizing from exit-state identity in research."""
        if (
            self.holding_upgrade_preserves_strategy
            and signal.metadata.get("holding_upgrade") is True
        ):
            return str(position.get("strategy_id") or signal.strategy_id)
        return str(signal.strategy_id or position.get("strategy_id") or "")


__all__ = ["NiuOneDailyExitStrategy", "NiuOneStrategyBacktestPolicy"]
