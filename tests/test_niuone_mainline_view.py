from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.dashboard.niuone_mainline import build_niuone_mainline_view
from app.screening.niuone_mainline_cache import (
    build_niuone_mainline_cache_payload,
    load_cached_niuone_context,
    write_niuone_mainline_cache,
)


def sample_scan() -> dict[str, object]:
    return {
        "generated_at": "2026-07-28 14:12:14",
        "quote_generated_at": "2026-07-28 14:12:08",
        "refresh_mode": "minute_quotes",
        "calculation_duration_ms": 3210,
        "configured_stock_universe_label": "主板",
        "reference_stock_universe_label": "创业板、科创板、主板（全量非 ST）",
        "reference_pool_count": 4995,
        "reference_analysis_count": 4291,
        "total_analyzed": 2813,
        "trade_count": 0,
        "items": [
            {
                "code": "603979",
                "name": "金诚信",
                "industry": "工业金属",
                "best_strategy": "niu_leader",
                "best_score": 7.6,
                "private_note": "do-not-publish",
                "strategies": {
                    "niu_leader": {
                        "score": 7.6,
                        "entry_threshold": 8.5,
                        "actionable": False,
                        "change_pct": 3.2,
                        "stock_role": "leader",
                        "mainline_intraday_state": "intraday_mainline",
                        "hard_blockers": ["题材尚未跨日确认"],
                        "provider_token": "secret",
                    }
                },
            }
        ],
        "trade_items": [],
        "niuone_context": {
            "as_of_date": "2026-07-28",
            "theme_count": 2,
            "strong_stock_count": 8,
            "mapped_stock_count": 4291,
            "data_coverage": 0.8601,
            "coverage_diagnostics": {
                "prepared_stock_count": 4800,
                "reasons": [
                    {
                        "key": "kline_unavailable",
                        "label": "K线不可用或少于30根",
                        "count": 300,
                        "description": "K线请求失败或过短",
                    },
                    {
                        "key": "industry_unmapped",
                        "label": "行业映射缺失",
                        "count": 404,
                        "description": "没有行业归属",
                    },
                ],
            },
            "stocks": {"603979": {"raw_news": "private"}},
            "industry_money_flow": [{"raw": "private"}],
            "market": {
                "score": 34,
                "state": "defensive",
                "allow_new_buys": False,
                "max_total_position_pct": 0,
            },
            "mainline": {
                "mode": "none",
                "primary": "",
                "intraday_primary": "银行",
                "intraday_primary_score": 79.03,
                "observation_reason": "日内强势仅观察",
                "today_primary": "工业金属",
                "today_primary_score": 80.0,
                "today_primary_breadth_pct": 87.5,
                "today_observation_reason": "今日强度仅作观察",
                "reversal_primary": "工业金属",
                "reversal_primary_score": 82.5,
                "reversal_confirmation_count": 2,
            },
            "themes": {
                "工业金属": {
                    "industry": "工业金属",
                    "score": 72.5,
                    "state": "emerging",
                    "niuone_lifecycle_stage": "markup",
                    "niuone_lifecycle_label": "主线主升",
                    "niuone_lifecycle_order": 20,
                    "niuone_lifecycle_entry_policy": "participate",
                    "intraday_state": "intraday_mainline",
                    "member_count": 8,
                    "strong_stock_count": 4,
                    "effective_strong_count": 3.2,
                    "today_eligible_data": True,
                    "today_quote_count": 8,
                    "today_data_coverage": 1.0,
                    "today_up_count": 7,
                    "today_1_5pct_count": 5,
                    "today_3pct_count": 5,
                    "today_5pct_count": 2,
                    "today_breadth_pct": 87.5,
                    "today_median_change_pct": 3.2,
                    "today_median_rebound_pct": 2.4,
                    "today_prior_median_ret5_pct": -2.1,
                    "today_strength_score": 80.0,
                    "today_leadership_score": 65.0,
                    "reversal_candidate": True,
                    "reversal_confirmed": True,
                    "reversal_confirmation_count": 2,
                    "reversal_min_sample_gap_minutes": 20,
                    "reversal_sample_gap_minutes": 25,
                    "reversal_origin_weak": True,
                    "reversal_quote_coverage_ok": True,
                    "reversal_flow_available": True,
                    "reversal_flow_positive": True,
                    "reversal_score": 82.5,
                    "core_overlap_count": 1,
                    "core_overlap_ratio": 0.25,
                    "continued_core_codes": ["603979"],
                    "strong_stocks": [
                        {
                            "code": "603979",
                            "name": "金诚信",
                            "strong_score": 8.1,
                            "change_pct": 5.26,
                            "rebound_from_low_pct": 3.1,
                            "reclaim_previous_close": True,
                            "role": "leader",
                        },
                        {
                            "code": "600111",
                            "name": "北方稀土",
                            "strong_score": 7.8,
                            "change_pct": -1.35,
                            "role": "core",
                        },
                    ],
                    "today_leaders": [
                        {
                            "code": "603979",
                            "name": "金诚信",
                            "strong_score": 8.1,
                            "change_pct": 5.26,
                            "role": "today_leader",
                        }
                    ],
                    "internal_samples": [1, 2, 3],
                },
                "银行": {
                    "industry": "银行",
                    "score": 79.03,
                    "state": "emerging",
                    "intraday_state": "intraday_mainline",
                    "strong_stock_count": 4,
                    "today_eligible_data": True,
                    "today_quote_count": 6,
                    "today_data_coverage": 1.0,
                    "today_up_count": 3,
                    "today_breadth_pct": 50.0,
                    "today_median_change_pct": 0.6,
                    "today_strength_score": 40.0,
                },
            },
        },
    }


class NiuOneMainlineViewTests(unittest.TestCase):
    def test_independent_cache_keeps_mainline_state_but_drops_large_raw_context(self) -> None:
        payload = build_niuone_mainline_cache_payload(sample_scan())

        self.assertEqual(payload["generated_at"], "2026-07-28 14:12:14")
        self.assertEqual(payload["quote_generated_at"], "2026-07-28 14:12:08")
        self.assertEqual(payload["refresh_mode"], "minute_quotes")
        self.assertEqual(payload["calculation_duration_ms"], 3210)
        self.assertEqual(payload["reference_pool_count"], 4995)
        self.assertNotIn("configured_stock_universe_label", payload)
        self.assertNotIn("items", payload)
        self.assertNotIn("trade_items", payload)
        self.assertNotIn("stocks", payload["niuone_context"])
        self.assertNotIn("industry_money_flow", payload["niuone_context"])
        self.assertIn("工业金属", payload["niuone_context"]["themes"])

        with tempfile.TemporaryDirectory(prefix="niuone-mainline-") as directory:
            path = Path(directory) / "niuone_mainline_latest.json"
            write_niuone_mainline_cache(path, sample_scan())
            loaded = load_cached_niuone_context(path)

        self.assertEqual(loaded["as_of_date"], "2026-07-28")
        self.assertEqual(loaded["mainline"]["intraday_primary"], "银行")

    def test_public_view_sorts_themes_and_exposes_only_page_fields(self) -> None:
        view = build_niuone_mainline_view(sample_scan())

        self.assertTrue(view["available"])
        self.assertEqual(view["quote_generated_at"], "2026-07-28 14:12:08")
        self.assertEqual(view["refresh_mode"], "minute_quotes")
        self.assertEqual(view["mainline"]["intraday_primary"], "银行")
        self.assertEqual(view["mainline"]["today_primary"], "工业金属")
        self.assertEqual(view["mainline"]["today_primary_breadth_pct"], 87.5)
        self.assertEqual(view["mainline"]["reversal_primary"], "工业金属")
        self.assertEqual(view["mainline"]["reversal_confirmation_count"], 2)
        self.assertEqual([theme["industry"] for theme in view["themes"]], ["银行", "工业金属"])
        self.assertEqual([theme["industry"] for theme in view["today_themes"]], ["工业金属", "银行"])
        self.assertEqual([theme["industry"] for theme in view["reversal_themes"]], ["工业金属"])
        industrial_metals = next(theme for theme in view["themes"] if theme["industry"] == "工业金属")
        self.assertEqual(industrial_metals["effective_strong_count"], 3.2)
        self.assertEqual(industrial_metals["niuone_lifecycle_stage"], "markup")
        self.assertEqual(industrial_metals["niuone_lifecycle_label"], "主线主升")
        self.assertEqual(industrial_metals["niuone_lifecycle_order"], 20)
        self.assertEqual(
            industrial_metals["niuone_lifecycle_entry_policy"],
            "participate",
        )
        self.assertEqual(industrial_metals["effective_breadth_pct"], 40)
        self.assertEqual(industrial_metals["leader_stock"]["code"], "603979")
        self.assertEqual(industrial_metals["leader_stock"]["role"], "leader")
        self.assertEqual(industrial_metals["leader_stock"]["change_pct"], 5.26)
        self.assertEqual(industrial_metals["today_strength_score"], 80)
        self.assertEqual(industrial_metals["today_breadth_pct"], 87.5)
        self.assertTrue(industrial_metals["reversal_confirmed"])
        self.assertEqual(industrial_metals["reversal_sample_gap_minutes"], 25)
        self.assertEqual(industrial_metals["today_median_rebound_pct"], 2.4)
        self.assertEqual(industrial_metals["today_leader_stock"]["code"], "603979")
        self.assertEqual(industrial_metals["leader_stock"]["rebound_from_low_pct"], 3.1)
        self.assertTrue(industrial_metals["leader_stock"]["reclaim_previous_close"])
        self.assertEqual(
            [(stock["code"], stock["change_pct"]) for stock in industrial_metals["strong_stocks"]],
            [("603979", 5.26), ("600111", -1.35)],
        )
        self.assertEqual(view["data_quality"]["coverage"], 0.8591)
        self.assertEqual(view["data_quality"]["prepared_stock_count"], 4800)
        self.assertEqual(
            [(reason["key"], reason["count"]) for reason in view["data_quality"]["uncovered_reasons"]],
            [("kline_unavailable", 300), ("industry_unmapped", 404)],
        )
        self.assertNotIn("candidates", view)
        self.assertNotIn("trade_candidates", view)
        self.assertNotIn("allow_new_buys", view["market"])
        self.assertNotIn("max_total_position_pct", view["market"])
        serialized = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("do-not-publish", serialized)
        self.assertNotIn("provider_token", serialized)
        self.assertNotIn("raw_news", serialized)
        self.assertNotIn("internal_samples", serialized)

    def test_empty_payload_returns_stable_unavailable_view(self) -> None:
        view = build_niuone_mainline_view(None)

        self.assertFalse(view["available"])
        self.assertEqual(view["themes"], [])
        self.assertEqual(view["today_themes"], [])
        self.assertEqual(view["reversal_themes"], [])

    def test_legacy_snapshot_marks_uncovered_reason_as_pending(self) -> None:
        scan = sample_scan()
        scan["niuone_context"].pop("coverage_diagnostics")

        view = build_niuone_mainline_view(scan)

        self.assertEqual(view["data_quality"]["uncovered_reasons"], [{
            "key": "legacy_unclassified",
            "label": "历史快照未记录明细",
            "count": 704,
            "description": "下一次题材强度扫描将按数据处理阶段补齐原因",
        }])

    def test_public_view_limits_theme_watchlist_to_top_five(self) -> None:
        scan = sample_scan()
        scan["niuone_context"]["themes"] = {
            f"题材{index}": {
                "industry": f"题材{index}",
                "score": index,
                "state": "candidate",
            }
            for index in range(1, 9)
        }

        view = build_niuone_mainline_view(scan)

        self.assertEqual(len(view["themes"]), 5)
        self.assertEqual(
            [theme["industry"] for theme in view["themes"]],
            ["题材8", "题材7", "题材6", "题材5", "题材4"],
        )

    def test_public_view_keeps_an_independent_today_top_five(self) -> None:
        scan = sample_scan()
        scan["niuone_context"]["themes"] = {
            f"题材{index}": {
                "industry": f"题材{index}",
                "score": index,
                "state": "candidate",
                "today_eligible_data": True,
                "today_quote_count": 6,
                "today_data_coverage": 1.0,
                "today_breadth_pct": 100 - index,
                "today_median_change_pct": 9 - index,
                "today_strength_score": 100 - index,
            }
            for index in range(1, 9)
        }

        view = build_niuone_mainline_view(scan)

        self.assertEqual(
            [theme["industry"] for theme in view["themes"]],
            ["题材8", "题材7", "题材6", "题材5", "题材4"],
        )
        self.assertEqual(
            [theme["industry"] for theme in view["today_themes"]],
            ["题材1", "题材2", "题材3", "题材4", "题材5"],
        )

    def test_public_view_keeps_reversal_list_independent_of_structure_top_five(self) -> None:
        scan = sample_scan()
        scan["niuone_context"]["themes"] = {
            f"题材{index}": {
                "industry": f"题材{index}",
                "score": index,
                "state": "candidate",
                "reversal_candidate": index == 1,
                "reversal_confirmed": index == 1,
                "reversal_score": 88 if index == 1 else 0,
            }
            for index in range(1, 9)
        }

        view = build_niuone_mainline_view(scan)

        self.assertEqual(
            [theme["industry"] for theme in view["themes"]],
            ["题材8", "题材7", "题材6", "题材5", "题材4"],
        )
        self.assertEqual(
            [theme["industry"] for theme in view["reversal_themes"]],
            ["题材1"],
        )


if __name__ == "__main__":
    unittest.main()
