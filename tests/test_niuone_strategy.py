#!/usr/bin/env python3
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
COMPAT = APP / "compat"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(COMPAT))

_TEST_HOME = tempfile.TemporaryDirectory(prefix="niuone-strategy-")
os.environ["DASHBOARD_HOME"] = _TEST_HOME.name

import niuniu_practice_trader as trader  # noqa: E402
from strategies.niuone_risk import niuone_risk_budget  # noqa: E402
from strategies.scoring import (  # noqa: E402
    analyze_enriched_rows,
    build_niuone_context,
    enrich_rows,
    score_niu_leader,
)
from strategies.scoring.common import with_strategy_profile  # noqa: E402
from strategies.selection import candidate_is_trade_ready, select_trade_candidates  # noqa: E402


def make_rows(code: str, industry: str, daily_step: float = 0.04) -> list[dict]:
    rows = []
    for index in range(65):
        close = 10.0 + index * daily_step
        rows.append({
            "date": f"2026-{index // 28 + 5:02d}-{index % 28 + 1:02d}",
            "open": close * 0.997,
            "close": close,
            "high": close * 1.008,
            "low": close * 0.992,
            "volume": 1000.0,
        })
    enrich_rows(rows)
    rows[-1].update({
        "symbol_code": code,
        "stock_name": f"测试{code}",
        "industry": industry,
        "quote_amount": 1.5e9,
    })
    return rows


def niu_candidate(**updates) -> dict:
    candidate = {
        "code": "600000",
        "name": "牛牛测试",
        "best_strategy": "niu_leader",
        "best_score": 9.0,
        "entry_threshold": 8.0,
        "actionable": True,
        "hard_blockers": [],
        "industry": "半导体",
        "sector": "半导体",
        "market_regime": "offensive",
        "market_score": 78.0,
        "market_hard_stop": False,
        "market_allows_buys": True,
        "mainline_state": "mainline",
        "mainline_score": 86.0,
        "sector_status": "mainline",
        "sector_score": 86.0,
        "strong_stock_count": 4,
        "effective_strong_count": 3.6,
        "leader_concentration": 0.3,
        "single_stock_dominated": False,
        "stock_strong": True,
        "stock_strong_score": 92.0,
        "stock_sector_rank": 95.0,
        "distance_pct": 1.0,
        "stop_price": 9.5,
        "stop_source": "niu_structure_low",
        "stop_distance_pct": 5.0,
        "atr20": 0.3,
        "gap_buffer_pct": 1.0,
        "execution_buffer_pct": 0.2,
        "effective_loss_distance_pct": 6.2,
        "per_trade_risk_budget_pct": 1.5,
        "max_position_pct_by_risk": 24.1935,
    }
    candidate.update(updates)
    return candidate


class NiuOneStrategyTests(unittest.TestCase):
    def _prepared_market(self) -> list[dict]:
        prepared = []
        for theme_index, industry in enumerate(("半导体", "银行", "汽车", "医药")):
            for member_index in range(4):
                code = f"{600000 + theme_index * 10 + member_index:06d}"
                step = 0.09 if theme_index == 0 else 0.025 - theme_index * 0.008
                rows = make_rows(code, industry, step)
                if theme_index == 0:
                    for row in rows[-5:]:
                        row["volume"] = 1800.0
                    enrich_rows(rows)
                    rows[-1].update({
                        "symbol_code": code,
                        "stock_name": f"测试{code}",
                        "industry": industry,
                        "quote_amount": 2.5e9,
                    })
                prepared.append({
                    "code": code,
                    "name": f"测试{code}",
                    "industry": industry,
                    "quote": {"amount": 2.5e9 if theme_index == 0 else 1.2e9},
                    "rows": rows,
                })
        return prepared

    def test_context_confirms_mainline_from_multiple_strong_stocks(self):
        prepared = self._prepared_market()
        market_snapshot = {
                "up": 120,
                "down": 30,
                "median_change_pct": 0.8,
                "limit_up": 12,
                "limit_down": 1,
                "core_index_count": 3,
                "index_below_ma20_count": 0,
            }
        context = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            flow_rows={"inflow": [{"name": "半导体", "net_flow_yi": 30}], "outflow": []},
        )
        confirmed = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            flow_rows={"inflow": [{"name": "半导体", "net_flow_yi": 30}], "outflow": []},
            previous_context=context,
        )

        theme = confirmed["themes"]["半导体"]
        self.assertEqual(context["theme_basis"], "industry_proxy")
        self.assertEqual(confirmed["market"]["state"], "offensive")
        self.assertEqual(confirmed["market"]["per_trade_risk_pct"], 1.5)
        self.assertGreaterEqual(theme["strong_stock_count"], 3)
        self.assertGreaterEqual(theme["effective_strong_count"], 2.4)
        self.assertFalse(theme["single_stock_dominated"])
        self.assertEqual(theme["state"], "mainline")
        self.assertEqual(confirmed["mainline"]["mode"], "single")
        self.assertEqual(confirmed["mainline"]["primary"], "半导体")

    def test_single_strong_stock_does_not_force_a_mainline(self):
        prepared = self._prepared_market()
        for item in prepared:
            if item["industry"] == "半导体" and item["code"] != "600000":
                item["rows"] = make_rows(item["code"], "半导体", -0.005)
                item["quote"] = {"amount": 8e8}

        context = build_niuone_context(prepared)
        theme = context["themes"]["半导体"]

        self.assertTrue(theme["single_stock_dominated"])
        self.assertNotEqual(theme["state"], "mainline")
        self.assertEqual(context["mainline"]["mode"], "none")

    def test_prior_scan_confirmation_promotes_an_emerging_theme(self):
        first = build_niuone_context(self._prepared_market())
        first["themes"]["半导体"].update({
            "state": "emerging",
            "raw_state": "mainline",
            "confirmation_count": 1,
            "state_streak": 1,
        })
        second = build_niuone_context(self._prepared_market(), previous_context=first)

        self.assertEqual(second["themes"]["半导体"]["state"], "mainline")
        self.assertGreaterEqual(second["themes"]["半导体"]["confirmation_count"], 2)

    def test_scorer_uses_mainline_context_and_ema_hard_gates(self):
        rows = make_rows("600000", "半导体", 0.02)
        context = {
            "market": {"state": "offensive", "score": 78, "hard_stop": False, "allow_new_buys": True},
            "mainline": {"mode": "single", "primary": "半导体"},
            "dragon_tiger": {"available": False},
            "news": {"configured": False},
            "themes": {
                "半导体": {
                    "state": "mainline", "raw_state": "mainline", "score": 88,
                    "member_count": 8, "eligible_data": True, "strong_stock_count": 4,
                    "effective_strong_count": 3.5, "leader_concentration": 0.3,
                    "single_stock_dominated": False, "confirmation_count": 2, "state_streak": 2,
                }
            },
            "stocks": {
                "600000": {
                    "theme_rank": 95, "market_rank": 92, "strong_score": 92,
                    "strong": True, "role": "leader", "news_precheck": {},
                }
            },
        }

        result = score_niu_leader(rows, context)

        self.assertIsNotNone(result)
        self.assertEqual(result["mainline_state"], "mainline")
        self.assertEqual(result["stock_role"], "leader")
        self.assertEqual(result["stop_source"], "niu_structure_low")
        self.assertEqual(result["per_trade_risk_budget_pct"], 1.5)
        self.assertFalse(any("BBI" in blocker for blocker in result["hard_blockers"]))

        payload = with_strategy_profile("niu_leader", {
            "score": 9.0,
            "distance_pct": 10.0,
            "extension_atr": 1.0,
            "market_allows_buys": True,
            "market_hard_stop": False,
            "market_regime": "offensive",
            "sector_data_eligible": True,
            "sector_status": "mainline",
            "mainline_score": 85,
            "mainline_selected": True,
            "single_stock_dominated": False,
            "stock_strong": True,
            "stock_sector_rank": 90,
            "breakout": True,
            "pullback": False,
            "risk_ok": True,
            "effective_loss_distance_pct": 5.0,
            "max_position_pct_by_risk": 5.0,
            "risk_flags": [],
        })
        self.assertTrue(payload["actionable"])
        self.assertEqual(payload["strategy_id"], "niu_leader")
        self.assertTrue(candidate_is_trade_ready(payload))
        self.assertTrue(candidate_is_trade_ready({**payload, "best_strategy": "niu_leader", "best_score": 9.0}))

    def test_execution_enforces_niuone_budget_and_persists_mainline_marks(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "牛牛测试", "source": "test"}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 2400, "reason": "牛牛领航确认"}]}

            executed = trader.execute_actions(state, decision, [niu_candidate()], True, "连续竞价交易时段", market)

            self.assertEqual(len(executed), 1)
            pos = state["positions"]["600000"]
            self.assertEqual(pos["entry_stop_source"], "niu_structure_low")
            self.assertEqual(pos["mainline_state"], "mainline")
            self.assertEqual(pos["risk_budget_regime"], "offensive")
            self.assertGreater(pos["position_open_risk_pct"], 1.4)
            self.assertLessEqual(pos["position_open_risk_pct"], 1.5)

            blocked_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            blocked_decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 2500, "reason": "牛牛领航确认"}]}
            blocked = trader.execute_actions(blocked_state, blocked_decision, [niu_candidate()], True, "连续竞价交易时段", market)
            self.assertEqual(blocked, [])
            self.assertIn("风险预算动态上限", blocked_decision["execution_blocked_reason"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_niuone_execution_blocks_chinext_when_configured_universe_is_main_board(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        saved_active = os.environ.get(trader.ACTIVE_STRATEGY_ENV)
        saved_universe = os.environ.get(trader.STOCK_UNIVERSE_ENV)
        try:
            os.environ[trader.ACTIVE_STRATEGY_ENV] = "niuone"
            os.environ[trader.STOCK_UNIVERSE_ENV] = "main_board"
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "创业板牛牛", "source": "test"}
            candidate = niu_candidate(code="300001", name="创业板牛牛")
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "300001", "shares": 100, "reason": "牛牛领航确认"}]
            }
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            executed = trader.execute_actions(state, decision, [candidate], True, "连续竞价交易时段", market)

            self.assertEqual(executed, [])
            self.assertEqual(state["positions"], {})
            self.assertIn("不在当前选股范围", decision["execution_blocked_reason"])
            self.assertEqual(trader.current_stock_universe(), ("main_board",))
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote
            if saved_active is None:
                os.environ.pop(trader.ACTIVE_STRATEGY_ENV, None)
            else:
                os.environ[trader.ACTIVE_STRATEGY_ENV] = saved_active
            if saved_universe is None:
                os.environ.pop(trader.STOCK_UNIVERSE_ENV, None)
            else:
                os.environ[trader.STOCK_UNIVERSE_ENV] = saved_universe

    def test_mainline_weakness_counts_once_per_day_and_exits(self):
        state = {
            "positions": {
                "600000": {
                    "code": "600000", "name": "牛牛测试", "qty": 400,
                    "avg_cost": 10.0, "last_price": 10.2, "close": 10.2,
                    "buy_strategy": "niu_leader", "industry": "半导体",
                    "entry_stop_price": 9.5, "entry_stop_source": "niu_structure_low",
                    "buy_date_lots": {"2026-07-10": 400},
                }
            }
        }

        def payload(day: str) -> dict:
            return {
                "generated_at": f"{day} 14:30:00",
                "niuone_context": {
                    "market": {"state": "rotation", "score": 55, "hard_stop": False, "allow_new_buys": True},
                    "themes": {"半导体": {"score": 50, "state": "fading", "raw_state": "fading"}},
                    "stocks": {"600000": {"industry": "半导体", "theme_rank": 20}},
                },
            }

        trader.sync_niuone_position_context(state, payload("2026-07-15"))
        trader.sync_niuone_position_context(state, payload("2026-07-15"))
        self.assertEqual(state["positions"]["600000"]["mainline_weak_count"], 1)
        trader.sync_niuone_position_context(state, payload("2026-07-16"))
        self.assertEqual(state["positions"]["600000"]["mainline_weak_count"], 2)

        signal = trader.evaluate_sell_signal("600000", state["positions"]["600000"], "2026-07-16", time_exit_allowed=False)
        self.assertEqual(signal["signal"], "niu_mainline_faded")

    def test_niuone_uses_two_r_and_independent_risk_budget(self):
        self.assertEqual(niuone_risk_budget("offensive")["per_trade_risk_pct"], 1.5)
        self.assertEqual(niuone_risk_budget("offensive")["max_total_position_pct"], 70.0)
        self.assertEqual(niuone_risk_budget("rotation")["max_total_position_pct"], 55.0)
        self.assertEqual(niuone_risk_budget("defensive")["max_open_risk_pct"], 0.0)
        pos = {
            "qty": 400,
            "avg_cost": 10.0,
            "last_price": 12.0,
            "close": 12.0,
            "buy_strategy": "niu_leader",
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_structure_low",
            "mainline_score": 82,
            "mainline_state": "mainline",
            "mainline_weak_count": 0,
            "buy_date_lots": {"2026-07-15": 400},
        }

        signal = trader.evaluate_sell_signal("600000", pos, "2026-07-16", time_exit_allowed=False)

        self.assertEqual(signal["signal"], "niu_2r_partial")
        self.assertEqual(signal["sell_ratio"], 0.5)

    def test_niuone_hard_caps_total_open_positions_at_five(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "牛牛测试", "source": "test"}
            positions = {
                f"60001{index}": {
                    "code": f"60001{index}",
                    "name": f"已有持仓{index}",
                    "qty": 100,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                    "buy_strategy": "niu_leader",
                    "industry": f"行业{index}",
                    "entry_stop_price": 9.5,
                    "gap_buffer_pct": 1.0,
                    "execution_buffer_pct": 0.2,
                    "effective_loss_distance_pct": 6.2,
                    "buy_date_lots": {"2026-07-24": 100},
                }
                for index in range(5)
            }
            state = {"cash": 95000.0, "positions": positions, "trade_log": []}
            candidate = niu_candidate(code="600099", industry="电子", sector="电子")
            decision = {"actions": [{"action": "BUY", "code": "600099", "shares": 100, "reason": "牛牛领航确认"}]}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            executed = trader.execute_actions(state, decision, [candidate], True, "连续竞价交易时段", market)

            self.assertEqual(executed, [])
            self.assertIn("牛牛战法最多同时持有5只", decision["execution_blocked_reason"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_mainline_detection_to_selection_buy_and_automatic_sell_runs_end_to_end(self):
        prepared = self._prepared_market()
        market_snapshot = {
            "up": 120,
            "down": 30,
            "median_change_pct": 0.8,
            "limit_up": 12,
            "limit_down": 1,
            "core_index_count": 3,
            "index_below_ma20_count": 0,
        }
        flow_rows = {"inflow": [{"name": "半导体", "net_flow_yi": 30}], "outflow": []}
        dragon_tiger = {
            "available": True,
            "date": "2026-07-24",
            "items": [{"code": "600000", "net_ratio_pct": 20}],
        }
        first = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            flow_rows=flow_rows,
            dragon_tiger_snapshot=dragon_tiger,
        )
        context = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            flow_rows=flow_rows,
            previous_context=first,
            dragon_tiger_snapshot=dragon_tiger,
        )
        rows = make_rows("600000", "半导体", 0.02)

        multi = analyze_enriched_rows(rows, {"niu_leader": score_niu_leader}, context)

        self.assertIsNotNone(multi)
        self.assertEqual(multi["best_strategy"], "niu_leader")
        scored = multi["strategies"]["niu_leader"]
        self.assertTrue(scored["actionable"])
        candidate = {
            **scored,
            "code": "600000",
            "name": "牛牛全链路",
            "price": rows[-1]["close"],
            "best_strategy": multi["best_strategy"],
            "best_score": multi["best_score"],
        }
        self.assertEqual(select_trade_candidates([candidate], limit=1), [candidate])

        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {
                "price": rows[-1]["close"],
                "name": "牛牛全链路",
                "source": "test",
            }
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "牛牛领航确认"}]}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            bought = trader.execute_actions(state, decision, [candidate], True, "连续竞价交易时段", market)

            self.assertEqual(len(bought), 1)
            self.assertEqual(bought[0]["action"], "BUY")
            position = state["positions"]["600000"]
            self.assertEqual(position["buy_strategy"], "niu_leader")
            self.assertEqual(position["strategy_mark_id"], "niu_leader")
            position["buy_date_lots"] = {"2026-07-24": 100}
            position["last_price"] = position["entry_stop_price"] - 0.01
            position["close"] = position["last_price"]

            sold = trader.check_auto_exits(state, datetime(2026, 7, 27, 10, 0, 0))

            self.assertEqual(len(sold), 1)
            self.assertEqual(sold[0]["action"], "SELL")
            self.assertEqual(sold[0]["exit_signal"], "niu_structure_stop")
            self.assertEqual(sold[0]["buy_strategy"], "niu_leader")
            self.assertNotIn("600000", state["positions"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote


if __name__ == "__main__":
    unittest.main()
