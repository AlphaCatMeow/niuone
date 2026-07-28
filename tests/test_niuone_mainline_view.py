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
            },
            "themes": {
                "工业金属": {
                    "industry": "工业金属",
                    "score": 72.5,
                    "state": "emerging",
                    "intraday_state": "intraday_mainline",
                    "member_count": 8,
                    "strong_stock_count": 4,
                    "effective_strong_count": 3.2,
                    "core_overlap_count": 1,
                    "core_overlap_ratio": 0.25,
                    "continued_core_codes": ["603979"],
                    "strong_stocks": [
                        {"code": "603979", "name": "金诚信", "strong_score": 8.1}
                    ],
                    "internal_samples": [1, 2, 3],
                },
                "银行": {
                    "industry": "银行",
                    "score": 79.03,
                    "state": "emerging",
                    "intraday_state": "intraday_mainline",
                    "strong_stock_count": 4,
                },
            },
        },
    }


class NiuOneMainlineViewTests(unittest.TestCase):
    def test_independent_cache_keeps_mainline_state_but_drops_large_raw_context(self) -> None:
        payload = build_niuone_mainline_cache_payload(sample_scan())

        self.assertEqual(payload["generated_at"], "2026-07-28 14:12:14")
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
        self.assertEqual(view["mainline"]["intraday_primary"], "银行")
        self.assertEqual([theme["industry"] for theme in view["themes"]], ["银行", "工业金属"])
        industrial_metals = next(theme for theme in view["themes"] if theme["industry"] == "工业金属")
        self.assertEqual(industrial_metals["effective_strong_count"], 3.2)
        self.assertEqual(industrial_metals["effective_breadth_pct"], 40)
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


if __name__ == "__main__":
    unittest.main()
