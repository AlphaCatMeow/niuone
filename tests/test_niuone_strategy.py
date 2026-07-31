#!/usr/bin/env python3
import json
import math
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
from strategies.niuone_risk import (  # noqa: E402
    niuone_chase_limits,
    niuone_risk_budget,
    niuone_structural_stop_limits,
    niuone_structure_risk_ok,
)
from strategies.scoring import (  # noqa: E402
    analyze_enriched_rows,
    build_niuone_context,
    enrich_rows,
    score_niu_emerging,
    score_niu_leader,
    score_niu_reversal_probe,
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
        "stock_role": "leader",
        "stock_leader_rank": 1,
        "stock_leader_tier": True,
        "stock_strong_score": 92.0,
        "stock_sector_rank": 95.0,
        "distance_pct": 1.0,
        "stop_price": 9.5,
        "stop_source": "niu_structure_low",
        "stop_distance_pct": 5.0,
        "atr": 0.3,
        "atr_period": 14,
        "atr20": 0.3,
        "gap_buffer_pct": 1.0,
        "execution_buffer_pct": 0.2,
        "effective_loss_distance_pct": 6.2,
        "per_trade_risk_budget_pct": 1.5,
        "max_position_pct_by_risk": 24.1935,
    }
    candidate.update(updates)
    return candidate


def reversal_candidate(**updates) -> dict:
    candidate = niu_candidate(
        best_strategy="niu_reversal_probe",
        entry_threshold=7.6,
        mainline_state="candidate",
        sector_status="candidate",
        mainline_score=45.0,
        mainline_cross_day_persistent=False,
        mainline_confirmed=False,
        stock_strong=False,
        stock_role="follower",
        stock_leader_rank=4,
        stock_leader_tier=False,
        reversal_candidate=True,
        reversal_confirmed=True,
        reversal_confirmation_count=2,
        stock_reversal_strong=True,
        stock_reversal_leader_rank=1,
        stock_reversal_leader_tier=True,
        today_breadth_pct=75.0,
        stop_price=9.7,
        stop_source="niu_reversal_low",
        stop_distance_pct=3.0,
        atr=0.3,
        atr20=0.3,
        gap_buffer_pct=1.0,
        execution_buffer_pct=0.2,
        effective_loss_distance_pct=4.2,
        per_trade_risk_budget_pct=0.35,
        max_position_pct_by_risk=5.0,
    )
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

    def _prepared_reversal_market(self) -> list[dict]:
        prepared = []
        rebound_changes = (3.6, 3.4, 3.2, 2.0)
        for theme_index, industry in enumerate(("半导体", "银行", "汽车", "医药")):
            for member_index in range(4):
                code = f"{600000 + theme_index * 10 + member_index:06d}"
                rows = make_rows(code, industry, -0.03 if theme_index == 0 else 0.005)
                for row in rows[-20:]:
                    row["high"] = float(row["close"]) * 1.02
                    row["low"] = float(row["close"]) * 0.98
                enrich_rows(rows)
                rows[-1].update({
                    "symbol_code": code,
                    "stock_name": f"测试{code}",
                    "industry": industry,
                    "quote_amount": 2.5e9,
                })
                previous_close = float(rows[-2]["close"])
                if theme_index == 0:
                    change_pct = rebound_changes[member_index]
                    quote = {
                        "price": previous_close * (1 + change_pct / 100),
                        "prev_close": previous_close,
                        "low": previous_close * 0.997,
                        "change_pct": change_pct,
                        "amount": 2.5e9 - member_index * 1e8,
                    }
                else:
                    quote = {
                        "price": previous_close * 0.995,
                        "prev_close": previous_close,
                        "low": previous_close * 0.99,
                        "change_pct": -0.5,
                        "amount": 1e9,
                    }
                prepared.append({
                    "code": code,
                    "name": f"测试{code}",
                    "industry": industry,
                    "quote": quote,
                    "rows": rows,
                })
        return prepared

    def test_reversal_probe_requires_two_time_spaced_broad_confirmations(self):
        prepared = self._prepared_reversal_market()
        context_args = {
            "market_snapshot": {
                "up": 3000,
                "down": 1500,
                "median_change_pct": 0.8,
                "limit_up": 20,
                "limit_down": 2,
                "core_index_count": 3,
                "index_below_ma20_count": 0,
            },
            "flow_rows": {"inflow": [{"name": "半导体", "net_flow_yi": 10}], "outflow": []},
            "as_of_date": "2026-07-31",
            "previous_trading_day": "2026-07-30",
        }
        first = build_niuone_context(
            prepared,
            sample_at="2026-07-31 10:00:00",
            **context_args,
        )
        too_soon = build_niuone_context(
            prepared,
            previous_context=first,
            sample_at="2026-07-31 10:10:00",
            **context_args,
        )
        confirmed = build_niuone_context(
            prepared,
            previous_context=too_soon,
            sample_at="2026-07-31 10:25:00",
            **context_args,
        )

        first_theme = first["themes"]["半导体"]
        too_soon_theme = too_soon["themes"]["半导体"]
        theme = confirmed["themes"]["半导体"]
        self.assertTrue(first_theme["reversal_candidate"])
        self.assertEqual(first_theme["reversal_confirmation_count"], 1)
        self.assertFalse(too_soon_theme["reversal_confirmed"])
        self.assertEqual(too_soon_theme["reversal_confirmation_count"], 1)
        self.assertTrue(theme["reversal_confirmed"])
        self.assertEqual(theme["reversal_confirmation_count"], 2)
        self.assertEqual(theme["reversal_sample_gap_minutes"], 25)
        self.assertEqual(confirmed["mainline"]["reversal_primary"], "半导体")
        self.assertEqual(confirmed["mainline"]["mode"], "none")

        rows = [dict(row) for row in prepared[0]["rows"]]
        previous_close = float(rows[-2]["close"])
        rows[-1].update({
            "open": previous_close * 0.995,
            "close": previous_close * 1.036,
            "high": previous_close * 1.041,
            "low": previous_close * 0.997,
            "quote_change_pct": 3.6,
        })
        enrich_rows(rows)
        rows[-1].update({
            "symbol_code": "600000",
            "stock_name": "测试600000",
            "industry": "半导体",
            "quote_amount": 2.5e9,
        })
        result = score_niu_reversal_probe(rows, confirmed)

        self.assertIsNotNone(result)
        self.assertTrue(result["actionable"])
        self.assertEqual(result["stop_source"], "niu_reversal_low")
        self.assertEqual(result["per_trade_risk_budget_pct"], 0.35)
        self.assertLessEqual(result["absolute_position_cap_pct"], 5)
        self.assertEqual(result["hard_blockers"], [])
        self.assertTrue(candidate_is_trade_ready(result))

    def test_reversal_probe_accepts_seventy_five_percent_quotes_but_requires_weak_origin(self):
        prepared = self._prepared_reversal_market()
        missing_quote = next(item for item in prepared if item["code"] == "600003")
        missing_quote["quote"].pop("change_pct")
        context_args = {
            "market_snapshot": {
                "up": 3000,
                "down": 1500,
                "median_change_pct": 0.8,
                "limit_up": 20,
                "limit_down": 2,
                "core_index_count": 3,
                "index_below_ma20_count": 0,
            },
            "flow_rows": {"inflow": [{"name": "半导体", "net_flow_yi": 10}], "outflow": []},
            "as_of_date": "2026-07-31",
            "previous_trading_day": "2026-07-30",
            "sample_at": "2026-07-31 10:00:00",
        }

        partial = build_niuone_context(prepared, **context_args)
        theme = partial["themes"]["半导体"]
        self.assertEqual(theme["today_data_coverage"], 0.75)
        self.assertFalse(theme["today_eligible_data"])
        self.assertTrue(theme["reversal_quote_coverage_ok"])
        self.assertTrue(theme["reversal_candidate"])

        for item in prepared:
            if item["industry"] != "半导体":
                continue
            rising_rows = make_rows(item["code"], "半导体", 0.03)
            for row in rising_rows[-20:]:
                row["high"] = float(row["close"]) * 1.02
                row["low"] = float(row["close"]) * 0.98
            enrich_rows(rising_rows)
            rising_rows[-1].update({
                "symbol_code": item["code"],
                "stock_name": item["name"],
                "industry": "半导体",
                "quote_amount": 2.5e9,
            })
            previous_close = float(rising_rows[-2]["close"])
            item["rows"] = rising_rows
            item["quote"]["prev_close"] = previous_close
            change_pct = float(item["quote"].get("change_pct") or 3.2)
            item["quote"]["price"] = previous_close * (1 + change_pct / 100)
            item["quote"]["low"] = previous_close * 0.997

        no_weak_origin = build_niuone_context(prepared, **context_args)
        strong_origin_theme = no_weak_origin["themes"]["半导体"]
        self.assertFalse(strong_origin_theme["reversal_origin_weak"])
        self.assertFalse(strong_origin_theme["reversal_candidate"])

        repeated_strong_origin = build_niuone_context(
            prepared,
            previous_context=no_weak_origin,
            **{**context_args, "sample_at": "2026-07-31 10:25:00"},
        )
        repeated_theme = repeated_strong_origin["themes"]["半导体"]
        self.assertFalse(repeated_theme["reversal_origin_weak"])
        self.assertFalse(repeated_theme["reversal_candidate"])

    def test_context_confirms_mainline_from_multiple_strong_stocks(self):
        prepared = self._prepared_market()
        prepared[0]["quote"]["change_pct"] = 7.35
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
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )
        confirmed = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            flow_rows={"inflow": [{"name": "半导体", "net_flow_yi": 30}], "outflow": []},
            previous_context=context,
            as_of_date="2026-07-28",
            previous_trading_day="2026-07-27",
        )

        theme = confirmed["themes"]["半导体"]
        self.assertEqual(context["theme_basis"], "industry_proxy")
        self.assertEqual(confirmed["market"]["state"], "offensive")
        self.assertEqual(confirmed["market"]["per_trade_risk_pct"], 1.5)
        self.assertGreaterEqual(theme["strong_stock_count"], 3)
        self.assertGreaterEqual(theme["effective_strong_count"], 2.4)
        self.assertAlmostEqual(
            theme["effective_breadth_pct"],
            theme["effective_strong_count"] / theme["member_count"] * 100,
            delta=0.2,
        )
        self.assertFalse(theme["single_stock_dominated"])
        self.assertEqual(theme["strong_stocks"][0]["code"], "600000")
        self.assertEqual(theme["strong_stocks"][0]["role"], "leader")
        self.assertEqual(theme["strong_stocks"][0]["leader_rank"], 1)
        self.assertTrue(all(stock["leader_tier"] for stock in theme["strong_stocks"][:3]))
        self.assertFalse(theme["strong_stocks"][3]["leader_tier"])
        self.assertEqual(theme["strong_stocks"][0]["change_pct"], 7.35)
        self.assertTrue(all(stock["role"] == "core" for stock in theme["strong_stocks"][1:]))
        self.assertEqual(theme["state"], "mainline")
        self.assertTrue(theme["cross_day_confirmed"])
        self.assertGreaterEqual(theme["core_overlap_count"], 2)
        self.assertEqual(theme["confirmation_count"], 2)
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

    def test_today_metrics_surface_broad_rebound_without_rewriting_structure(self):
        prepared = self._prepared_market()
        today_changes = [6.0, 5.5, 5.0, 4.5]
        for item in prepared:
            item["quote"]["change_pct"] = -1.0
            if item["industry"] != "半导体":
                continue
            member_index = int(item["code"][-1])
            item["rows"] = make_rows(item["code"], "半导体", -0.08)
            item["quote"] = {
                "amount": 5e8,
                "change_pct": today_changes[member_index],
            }

        context = build_niuone_context(prepared)
        theme = context["themes"]["半导体"]

        self.assertEqual(theme["strong_stock_count"], 0)
        self.assertEqual(theme["effective_breadth_pct"], 0)
        self.assertEqual(theme["leader_concentration"], 0)
        self.assertEqual(theme["concentration_penalty"], 0)
        self.assertFalse(theme["single_stock_dominated"])
        self.assertTrue(theme["today_eligible_data"])
        self.assertEqual(theme["today_quote_count"], 4)
        self.assertEqual(theme["today_up_count"], 4)
        self.assertEqual(theme["today_3pct_count"], 4)
        self.assertEqual(theme["today_5pct_count"], 3)
        self.assertEqual(theme["today_breadth_pct"], 100)
        self.assertEqual(theme["today_median_change_pct"], 5.25)
        self.assertEqual(theme["today_strength_score"], 96.25)
        self.assertEqual(theme["today_leadership_score"], 55)
        self.assertEqual(theme["today_leaders"][0]["change_pct"], 6)
        self.assertEqual(context["mainline"]["today_primary"], "半导体")
        self.assertEqual(context["mainline"]["today_primary_score"], 96.25)
        self.assertEqual(context["mainline"]["mode"], "none")

    def test_today_ranking_requires_fresh_quote_coverage(self):
        prepared = self._prepared_market()
        for item in prepared:
            item["quote"].pop("change_pct", None)
        semiconductor = [item for item in prepared if item["industry"] == "半导体"]
        semiconductor[0]["quote"]["change_pct"] = 6.0
        semiconductor[1]["quote"]["change_pct"] = 5.0

        context = build_niuone_context(prepared)
        theme = context["themes"]["半导体"]

        self.assertEqual(theme["today_quote_count"], 2)
        self.assertEqual(theme["today_data_coverage"], 0.5)
        self.assertFalse(theme["today_eligible_data"])
        self.assertEqual(context["mainline"]["today_primary"], "")

    def test_context_classifies_every_uncovered_reference_stock(self):
        valid = {
            "code": "600001",
            "name": "有效样本",
            "industry": "半导体",
            "quote": {"amount": 1.5e9},
            "rows": make_rows("600001", "半导体"),
        }
        missing_industry = {
            "code": "600002",
            "name": "无行业样本",
            "industry": "",
            "quote": {"amount": 1.5e9},
            "rows": make_rows("600002", ""),
        }
        insufficient = {
            "code": "600003",
            "name": "历史不足样本",
            "industry": "银行",
            "quote": {"amount": 1.5e9},
            "rows": make_rows("600003", "银行")[:40],
        }
        invalid_rows = make_rows("600004", "汽车")
        invalid_rows[-21]["close"] = 0
        invalid_metrics = {
            "code": "600004",
            "name": "指标无效样本",
            "industry": "汽车",
            "quote": {"amount": 1.5e9},
            "rows": invalid_rows,
        }

        context = build_niuone_context(
            [valid, missing_industry, insufficient, invalid_metrics],
            reference_pool_count=5,
        )

        diagnostics = context["coverage_diagnostics"]
        reasons = {reason["key"]: reason["count"] for reason in diagnostics["reasons"]}
        self.assertEqual(context["mapped_stock_count"], 1)
        self.assertEqual(context["data_coverage"], 0.2)
        self.assertEqual(diagnostics["uncovered_stock_count"], 4)
        self.assertEqual(reasons, {
            "kline_unavailable": 1,
            "insufficient_history": 1,
            "invalid_metrics": 1,
            "industry_unmapped": 1,
        })

    def test_context_sanitizes_non_finite_market_values_before_serialization(self):
        prepared = self._prepared_market()
        prepared[0]["quote"]["amount"] = float("nan")
        prepared[1]["quote"]["price"] = float("inf")

        context = build_niuone_context(
            prepared,
            market_snapshot={
                "up": 100,
                "down": 20,
                "median_change_pct": float("nan"),
                "limit_up": 5,
                "limit_down": 1,
            },
        )

        self.assertTrue(math.isfinite(context["market"]["score"]))
        json.dumps(context, ensure_ascii=False, allow_nan=False)

    def test_raw_defensive_market_immediately_zeroes_new_buy_budget(self):
        context = build_niuone_context(
            self._prepared_market(),
            market_snapshot={
                "up": 1,
                "down": 199,
                "median_change_pct": -1.5,
                "limit_up": 0,
                "limit_down": 20,
                "core_index_count": 0,
                "index_below_ma20_count": 0,
            },
            previous_context={
                "market": {
                    "state": "offensive",
                    "raw_state": "offensive",
                    "confirmation_count": 2,
                }
            },
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )

        market = context["market"]
        self.assertEqual(market["raw_state"], "defensive")
        self.assertEqual(market["risk_state"], "defensive")
        self.assertFalse(market["allow_new_buys"])
        self.assertEqual(market["per_trade_risk_pct"], 0.0)
        self.assertEqual(market["max_total_position_pct"], 0.0)

    def test_same_day_repeated_scans_remain_intraday_observation(self):
        prepared = self._prepared_market()
        first = build_niuone_context(
            prepared,
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )
        second = build_niuone_context(
            prepared,
            previous_context=first,
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )

        theme = second["themes"]["半导体"]
        self.assertEqual(theme["state"], "emerging")
        self.assertEqual(theme["intraday_state"], "intraday_mainline")
        self.assertEqual(theme["confirmation_count"], 1)
        self.assertEqual(theme["intraday_confirmation_count"], 2)
        self.assertFalse(theme["cross_day_confirmed"])
        self.assertEqual(second["mainline"]["mode"], "none")
        self.assertEqual(second["mainline"]["intraday_primary"], "半导体")

        rows = make_rows("600000", "半导体", 0.01)
        emerging = score_niu_emerging(rows, second)
        self.assertIsNotNone(emerging)
        self.assertFalse(emerging["actionable"])
        self.assertIn("启动主题尚未跨交易日延续", emerging["hard_blockers"])

    def test_changed_core_stocks_do_not_confirm_mainline_next_day(self):
        first = build_niuone_context(
            self._prepared_market(),
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )
        first["themes"]["半导体"]["core_stock_codes"] = ["601001", "601002", "601003"]
        second = build_niuone_context(
            self._prepared_market(),
            previous_context=first,
            as_of_date="2026-07-28",
            previous_trading_day="2026-07-27",
        )

        theme = second["themes"]["半导体"]
        self.assertEqual(theme["state"], "emerging")
        self.assertEqual(theme["core_overlap_count"], 0)
        self.assertFalse(theme["core_continuity_met"])
        self.assertFalse(theme["cross_day_confirmed"])

    def test_legacy_same_day_mainline_cache_is_not_trusted_as_cross_day_confirmation(self):
        prepared = self._prepared_market()
        legacy = build_niuone_context(
            prepared,
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )
        legacy["version"] = 1
        theme = legacy["themes"]["半导体"]
        theme["state"] = "mainline"
        theme.pop("mainline_confirmed", None)
        theme.pop("cross_day_confirmed", None)

        current = build_niuone_context(
            prepared,
            previous_context=legacy,
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )

        self.assertEqual(current["themes"]["半导体"]["state"], "emerging")
        self.assertFalse(current["themes"]["半导体"]["cross_day_confirmed"])

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
                    "cross_day_persistent": True, "cross_day_confirmed": True,
                    "mainline_confirmed": True, "core_overlap_count": 3,
                }
            },
            "stocks": {
                "600000": {
                    "theme_rank": 95, "market_rank": 92, "strong_score": 92,
                    "strong": True, "role": "leader", "leader_rank": 1,
                    "leader_tier": True, "news_precheck": {},
                }
            },
        }

        result = score_niu_leader(rows, context)

        self.assertIsNotNone(result)
        self.assertEqual(result["mainline_state"], "mainline")
        self.assertEqual(result["stock_role"], "leader")
        self.assertEqual(result["stop_source"], "niu_structure_low")
        self.assertEqual(result["atr_period"], 14)
        self.assertEqual(result["atr"], result["atr20"])
        self.assertEqual(result["per_trade_risk_budget_pct"], 1.5)
        self.assertFalse(any("BBI" in blocker for blocker in result["hard_blockers"]))

        context["stocks"]["600000"].update({
            "role": "core",
            "leader_rank": 2,
            "leader_tier": True,
            "theme_rank": 66,
        })
        second_rank = score_niu_leader(rows, context)
        self.assertIsNotNone(second_rank)
        self.assertEqual(second_rank["stock_leader_rank"], 2)
        self.assertTrue(second_rank["stock_leader_tier"])
        self.assertNotIn("个股未进入强势行业龙头梯队", second_rank["hard_blockers"])

        rows[-1]["quote_change_pct"] = 5.1
        expanded = score_niu_leader(rows, context)
        self.assertIsNotNone(expanded)
        self.assertNotIn("领航战法单日涨幅>4%", expanded["hard_blockers"])
        self.assertIn("领航买点偏扩张，已按行情弹性上限复核", expanded["risk_flags"])

        rows[-1]["quote_change_pct"] = 7.1
        chased = score_niu_leader(rows, context)
        self.assertIsNotNone(chased)
        self.assertFalse(chased["actionable"])
        self.assertIn("领航战法单日涨幅>7%", chased["hard_blockers"])

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
            "mainline_cross_day_confirmed": True,
            "mainline_confirmed": True,
            "single_stock_dominated": False,
            "stock_strong": True,
            "stock_role": "leader",
            "stock_leader_rank": 1,
            "stock_leader_tier": True,
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
        self.assertTrue(candidate_is_trade_ready({
            **payload,
            "best_strategy": "niu_leader",
            "best_score": 9.0,
            "stock_role": "core",
            "stock_leader_rank": 2,
        }))
        self.assertFalse(candidate_is_trade_ready({
            **payload,
            "best_strategy": "niu_leader",
            "best_score": 9.0,
            "stock_role": "core",
            "stock_leader_rank": 4,
            "stock_leader_tier": False,
        }))

    def test_niuone_entry_limits_expand_only_in_stronger_market_states(self):
        self.assertEqual(
            niuone_chase_limits("niu_leader", "offensive"),
            {"max_entry_change_pct": 7.0, "max_entry_extension_atr": 1.5},
        )
        self.assertEqual(
            niuone_chase_limits("niu_leader", "rotation"),
            {"max_entry_change_pct": 5.0, "max_entry_extension_atr": 1.25},
        )
        self.assertTrue(niuone_structure_risk_ok(9.5, 2.4, "offensive"))
        self.assertTrue(niuone_structure_risk_ok(7.5, 1.9, "rotation"))
        self.assertFalse(niuone_structure_risk_ok(8.1, 1.9, "rotation"))
        self.assertFalse(niuone_structure_risk_ok(6.1, 1.5, "recovery"))

        payload = with_strategy_profile("niu_leader", {
            "score": 9.0,
            "extension_atr": 1.25,
            "max_entry_change_pct": 5.0,
            "max_entry_extension_atr": 1.25,
            "change_pct": 5.000000000000003,
            "market_allows_buys": True,
            "market_hard_stop": False,
            "market_regime": "rotation",
            "sector_data_eligible": True,
            "sector_status": "mainline",
            "mainline_selected": True,
            "mainline_cross_day_confirmed": True,
            "single_stock_dominated": False,
            "stock_strong": True,
            "stock_leader_tier": True,
            "breakout": True,
            "pullback": False,
            "risk_ok": True,
            "effective_loss_distance_pct": 7.0,
            "max_position_pct_by_risk": 10.0,
            "risk_flags": [],
        })
        self.assertTrue(payload["actionable"])
        self.assertNotIn("领航战法单日涨幅>5%", payload["hard_blockers"])

        above_limit = with_strategy_profile("niu_leader", {**payload, "change_pct": 5.01})
        self.assertFalse(above_limit["actionable"])
        self.assertIn("领航战法单日涨幅>5%", above_limit["hard_blockers"])

    def test_all_niuone_profiles_require_the_strong_industry_leader(self):
        for strategy_id in ("niu_leader", "niu_pullback", "niu_emerging"):
            with self.subTest(strategy_id=strategy_id):
                blocked = with_strategy_profile(strategy_id, {
                    "score": 10.0,
                    "stock_role": "follower",
                    "stock_leader_rank": 4,
                    "stock_leader_tier": False,
                    "stock_strong": True,
                    "risk_flags": [],
                })
                self.assertIn("个股未进入强势行业龙头梯队", blocked["hard_blockers"])

                leader_tier = with_strategy_profile(strategy_id, {
                    "score": 10.0,
                    "stock_role": "core",
                    "stock_leader_rank": 2,
                    "stock_leader_tier": True,
                    "stock_strong": True,
                    "risk_flags": [],
                })
                self.assertNotIn("个股未进入强势行业龙头梯队", leader_tier["hard_blockers"])

                weak_leader = with_strategy_profile(strategy_id, {
                    "score": 10.0,
                    "stock_role": "leader",
                    "stock_leader_rank": 1,
                    "stock_leader_tier": True,
                    "stock_strong": False,
                    "risk_flags": [],
                })
                self.assertIn("个股未进入强势行业龙头梯队", weak_leader["hard_blockers"])

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
            self.assertEqual(pos["entry_atr"], 0.3)
            self.assertEqual(pos["entry_atr_period"], 14)
            self.assertEqual(pos["entry_atr20"], 0.3)
            self.assertEqual(pos["mainline_state"], "mainline")
            self.assertEqual(pos["stock_role"], "leader")
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

    def test_execution_rechecks_niuone_structural_limits_by_market_regime(self):
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

            offensive_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            offensive_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "牛牛领航确认"}]
            }
            offensive_candidate = niu_candidate(
                stop_price=9.28,
                stop_distance_pct=7.2,
                stop_atr=2.4,
                atr=None,
                atr_period=None,
                atr20=0.3,
                effective_loss_distance_pct=8.4,
            )
            executed = trader.execute_actions(
                offensive_state,
                offensive_decision,
                [offensive_candidate],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(len(executed), 1)
            self.assertEqual(offensive_state["positions"]["600000"]["risk_budget_regime"], "offensive")

            atr_blocked_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            atr_blocked_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "牛牛领航确认"}]
            }
            atr_blocked_candidate = niu_candidate(
                stop_price=9.1,
                stop_distance_pct=9.0,
                stop_atr=3.0,
                atr=0.3,
                atr20=0.3,
                effective_loss_distance_pct=10.2,
            )
            blocked = trader.execute_actions(
                atr_blocked_state,
                atr_blocked_decision,
                [atr_blocked_candidate],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(blocked, [])
            self.assertIn("10%/2.5ATR", atr_blocked_decision["execution_blocked_reason"])

            rotation_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            rotation_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "牛牛领航确认"}]
            }
            rotation_candidate = niu_candidate(
                market_regime="rotation",
                stop_price=9.3,
                stop_distance_pct=7.0,
                stop_atr=1.75,
                atr=0.4,
                atr20=0.4,
                effective_loss_distance_pct=8.2,
                per_trade_risk_budget_pct=1.0,
            )
            executed = trader.execute_actions(
                rotation_state,
                rotation_decision,
                [rotation_candidate],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(len(executed), 1)
            self.assertEqual(rotation_state["positions"]["600000"]["risk_budget_regime"], "rotation")
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_execution_accepts_second_rank_and_rejects_stock_outside_leader_tier(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "行业跟随股", "source": "test"}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            second_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            second_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "第一名涨停，顺延第二名"}]
            }
            second_rank = niu_candidate(
                stock_role="core",
                stock_leader_rank=2,
                stock_leader_tier=True,
            )
            executed = trader.execute_actions(
                second_state,
                second_decision,
                [second_rank],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(len(executed), 1)
            self.assertEqual(second_state["positions"]["600000"]["stock_leader_rank"], 2)

            blocked_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            blocked_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "模型误选第四名"}]
            }
            blocked = trader.execute_actions(
                blocked_state,
                blocked_decision,
                [niu_candidate(stock_role="core", stock_leader_rank=4, stock_leader_tier=False)],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(blocked, [])
            self.assertEqual(blocked_state["positions"], {})
            self.assertIn("个股未进入强势行业龙头梯队", blocked_decision["execution_blocked_reason"])

            trader.execution_quote = lambda code: {
                "price": 11.0,
                "prev_close": 10.0,
                "change_pct": 10.0,
                "name": "涨停龙头",
                "source": "test",
            }
            limit_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            limit_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "第一名已涨停"}]
            }
            at_limit = trader.execute_actions(
                limit_state,
                limit_decision,
                [niu_candidate(name="涨停龙头")],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(at_limit, [])
            self.assertIn("不在涨停价模拟买入", limit_decision["execution_blocked_reason"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_niuone_emerging_position_adds_only_after_confirmed_upgrade(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "牛牛启动", "source": "test"}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            original_position = {
                "code": "600000",
                "name": "牛牛启动",
                "qty": 100,
                "avg_cost": 10.0,
                "last_price": 10.0,
                "buy_strategy": "niu_emerging",
                "strategy_mark": {"strategy_id": "niu_emerging"},
                "industry": "半导体",
                "entry_reason": "牛牛启动观察仓",
                "entry_stop_price": 9.5,
                "gap_buffer_pct": 1.0,
                "execution_buffer_pct": 0.2,
                "buy_date_lots": {"2000-01-01": 100},
            }

            blocked_state = {"cash": 99000.0, "positions": {"600000": dict(original_position)}, "trade_log": []}
            blocked_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "启动主题延续"}]
            }
            blocked_candidate = niu_candidate(
                best_strategy="niu_emerging",
                mainline_state="emerging",
                sector_status="emerging",
                mainline_confirmed=False,
            )

            blocked = trader.execute_actions(
                blocked_state,
                blocked_decision,
                [blocked_candidate],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(blocked, [])
            self.assertEqual(blocked_state["positions"]["600000"]["qty"], 100)
            self.assertIn("升级为确认主线前禁止加仓", blocked_decision["execution_blocked_reason"])

            upgraded_state = {"cash": 99000.0, "positions": {"600000": dict(original_position)}, "trade_log": []}
            upgraded_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "升级主线后加仓"}]
            }
            upgraded_candidate = niu_candidate(
                best_strategy="niu_leader",
                mainline_state="mainline",
                sector_status="mainline",
                mainline_confirmed=True,
            )

            upgraded = trader.execute_actions(
                upgraded_state,
                upgraded_decision,
                [upgraded_candidate],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(len(upgraded), 1)
            position = upgraded_state["positions"]["600000"]
            self.assertEqual(position["qty"], 200)
            self.assertEqual(position["initial_buy_strategy"], "niu_emerging")
            self.assertEqual(position["buy_strategy"], "niu_leader")
            self.assertEqual(position["strategy_mark"]["strategy_id"], "niu_leader")
            self.assertEqual(position["strategy_mark"]["source"], "BUY_UPGRADE")
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_reversal_probe_is_small_and_cannot_add_on_entry_day(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "牛牛反转", "source": "test"}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            first_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "V形反转双确认"}]
            }

            first = trader.execute_actions(
                state,
                first_decision,
                [reversal_candidate()],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(len(first), 1)
            position = state["positions"]["600000"]
            self.assertEqual(position["buy_strategy"], "niu_reversal_probe")
            self.assertEqual(position["entry_stop_source"], "niu_reversal_low")
            self.assertEqual(position["absolute_position_cap_pct"], 5.0)
            self.assertEqual(position["per_trade_risk_budget_pct"], 0.35)

            add_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "反转继续走强"}]
            }
            added = trader.execute_actions(
                state,
                add_decision,
                [reversal_candidate()],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(added, [])
            self.assertEqual(state["positions"]["600000"]["qty"], 100)
            self.assertIn("当日禁止加仓", add_decision["execution_blocked_reason"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_reversal_probe_can_upgrade_to_cross_day_emerging(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "牛牛反转", "source": "test"}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            position = {
                "code": "600000",
                "name": "牛牛反转",
                "qty": 100,
                "avg_cost": 10.0,
                "last_price": 10.0,
                "buy_strategy": "niu_reversal_probe",
                "strategy_mark": {"strategy_id": "niu_reversal_probe"},
                "industry": "半导体",
                "entry_reason": "V形反转双确认",
                "entry_stop_price": 9.7,
                "entry_stop_source": "niu_reversal_low",
                "gap_buffer_pct": 1.0,
                "execution_buffer_pct": 0.2,
                "buy_date_lots": {"2000-01-01": 100},
            }
            state = {"cash": 99000.0, "positions": {"600000": position}, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "跨日延续升级启动"}]
            }
            candidate = niu_candidate(
                best_strategy="niu_emerging",
                mainline_state="emerging",
                sector_status="emerging",
                mainline_cross_day_persistent=True,
                mainline_confirmed=False,
            )

            upgraded = trader.execute_actions(
                state,
                decision,
                [candidate],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(len(upgraded), 1)
            position = state["positions"]["600000"]
            self.assertEqual(position["qty"], 200)
            self.assertEqual(position["initial_buy_strategy"], "niu_reversal_probe")
            self.assertEqual(position["buy_strategy"], "niu_emerging")
            self.assertEqual(position["strategy_mark"]["source"], "BUY_UPGRADE")
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_limit_up_execution_guard_respects_board_specific_limits(self):
        self.assertTrue(trader.quote_is_at_limit_up(
            "600000",
            "主板龙头",
            {"price": 11.0, "prev_close": 10.0, "change_pct": 10.0},
        ))
        self.assertFalse(trader.quote_is_at_limit_up(
            "300001",
            "创业板龙头",
            {"price": 11.0, "prev_close": 10.0, "change_pct": 10.0},
        ))
        self.assertTrue(trader.quote_is_at_limit_up(
            "300001",
            "创业板龙头",
            {"price": 12.0, "prev_close": 10.0, "change_pct": 20.0},
        ))
        self.assertTrue(trader.quote_is_at_limit_up(
            "600001",
            "ST测试",
            {"price": 10.5, "prev_close": 10.0, "change_pct": 5.0},
        ))

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

    def test_lost_leader_status_requires_two_observed_trading_days_before_exit(self):
        state = {
            "positions": {
                "600000": {
                    "code": "600000", "name": "牛牛测试", "qty": 400,
                    "avg_cost": 10.0, "last_price": 10.2, "close": 10.2,
                    "buy_strategy": "niu_leader", "industry": "半导体",
                    "entry_stop_price": 9.5, "entry_stop_source": "niu_structure_low",
                    "stock_role": "leader", "stock_leader_rank": 1,
                    "stock_leader_tier": True, "stock_strong": True,
                    "buy_date_lots": {"2026-07-10": 400},
                }
            }
        }

        def payload(day: str, stock: dict) -> dict:
            return {
                "generated_at": f"{day} 14:30:00",
                "niuone_context": {
                    "previous_trading_day": {
                        "2026-07-15": "2026-07-14",
                        "2026-07-16": "2026-07-15",
                        "2026-07-17": "2026-07-16",
                    }[day],
                    "market": {"state": "rotation", "score": 72, "hard_stop": False, "allow_new_buys": True},
                    "themes": {"半导体": {"score": 82, "state": "mainline", "raw_state": "mainline"}},
                    "stocks": {"600000": {"industry": "半导体", "theme_rank": 80, **stock}},
                },
            }

        trader.sync_niuone_position_context(state, payload("2026-07-15", {}))
        self.assertNotIn("niu_leader_lost_count", state["positions"]["600000"])

        trader.sync_niuone_position_context(
            state,
            payload("2026-07-16", {"role": "core", "leader_rank": 4, "leader_tier": False, "strong": True}),
        )
        trader.sync_niuone_position_context(
            state,
            payload("2026-07-16", {"role": "core", "leader_rank": 4, "leader_tier": False, "strong": True}),
        )
        self.assertEqual(state["positions"]["600000"]["niu_leader_lost_count"], 1)
        no_exit = trader.evaluate_sell_signal(
            "600000",
            state["positions"]["600000"],
            "2026-07-16",
            time_exit_allowed=False,
        )
        self.assertIsNone(no_exit)

        trader.sync_niuone_position_context(
            state,
            payload("2026-07-17", {"role": "core", "leader_rank": 4, "leader_tier": False, "strong": True}),
        )
        signal = trader.evaluate_sell_signal(
            "600000",
            state["positions"]["600000"],
            "2026-07-17",
            time_exit_allowed=False,
        )
        self.assertEqual(signal["signal"], "niu_leader_lost")
        self.assertIn("连续2个交易日跌出强势行业龙头梯队", signal["reason"])

    def test_niuone_uses_two_r_and_independent_risk_budget(self):
        self.assertEqual(niuone_risk_budget("offensive")["per_trade_risk_pct"], 1.5)
        self.assertEqual(
            niuone_risk_budget("offensive", "niu_reversal_probe")["per_trade_risk_pct"],
            0.35,
        )
        self.assertEqual(
            niuone_risk_budget("recovery", "niu_reversal_probe")["max_sector_position_pct"],
            8.0,
        )
        self.assertEqual(
            niuone_structural_stop_limits("offensive", "niu_reversal_probe"),
            {"max_stop_distance_pct": 4.0, "max_stop_atr": 1.2},
        )
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

    def test_niuone_time_exit_counts_trading_days_across_a_weekend(self):
        pos = {
            "qty": 100,
            "avg_cost": 10.0,
            "last_price": 10.0,
            "close": 10.0,
            "buy_strategy": "niu_emerging",
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_structure_low",
            "mainline_score": 70,
            "mainline_state": "emerging",
            "mainline_weak_count": 0,
            "buy_date_lots": {"2026-07-24": 100},
        }

        monday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-27",
            time_exit_allowed=True,
        )
        tuesday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-28",
            time_exit_allowed=True,
        )

        self.assertIsNone(monday)
        self.assertEqual(tuesday["signal"], "niu_emerging_unconfirmed")

    def test_reversal_probe_exits_on_t1_without_confirmation_and_t2_if_not_upgraded(self):
        pos = {
            "qty": 100,
            "avg_cost": 10.0,
            "last_price": 10.0,
            "close": 10.0,
            "buy_strategy": "niu_reversal_probe",
            "entry_stop_price": 9.7,
            "entry_stop_source": "niu_reversal_low",
            "mainline_score": 45,
            "mainline_state": "candidate",
            "buy_date_lots": {"2026-07-24": 100},
        }

        monday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-27",
            time_exit_allowed=True,
        )
        self.assertEqual(monday["signal"], "niu_reversal_unconfirmed")

        pos["mainline_cross_day_persistent"] = True
        confirmed_monday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-27",
            time_exit_allowed=True,
        )
        self.assertIsNone(confirmed_monday)

        tuesday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-28",
            time_exit_allowed=True,
        )
        self.assertEqual(tuesday["signal"], "niu_reversal_not_upgraded")

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
            as_of_date="2026-07-24",
            previous_trading_day="2026-07-23",
        )
        context = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            flow_rows=flow_rows,
            previous_context=first,
            dragon_tiger_snapshot=dragon_tiger,
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )
        rows = make_rows("600000", "半导体", 0.01)

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
