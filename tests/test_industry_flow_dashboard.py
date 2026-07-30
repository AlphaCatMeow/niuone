#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from dashboard.apis.industry_flow import (  # noqa: E402
    append_industry_flow_sample,
    build_industry_flow_payload,
    is_industry_flow_session_timestamp,
    normalize_industry_flow_sampling_windows,
)


class IndustryFlowPayloadTests(unittest.TestCase):
    def test_builds_observable_inflow_and_outflow_pressure_without_links(self):
        payload = build_industry_flow_payload({
            "generated_at": "2026-07-20 11:00:00",
            "outflow": [
                {"name": "行业A", "net_flow_yi": -30, "inflow_yi": 20, "outflow_yi": 50},
                {"name": "行业B", "net_flow_yi": -10, "inflow_yi": 10, "outflow_yi": 20},
            ],
            "inflow": [
                {"name": "行业C", "net_flow_yi": 20, "inflow_yi": 30, "outflow_yi": 10},
                {"name": "行业D", "net_flow_yi": 25, "inflow_yi": 40, "outflow_yi": 15},
            ],
        })

        nodes = {node["name"]: node for node in payload["nodes"]}
        self.assertTrue(payload["available"])
        self.assertEqual(nodes["行业A"]["magnitude_yi"], 30)
        self.assertEqual(nodes["行业A"]["role"], "outflow")
        self.assertEqual(nodes["行业D"]["magnitude_yi"], 25)
        self.assertEqual(nodes["行业D"]["role"], "inflow")
        self.assertEqual(payload["links"], [])
        self.assertEqual(payload["totals"]["visible_inflow_yi"], 45)
        self.assertEqual(payload["totals"]["visible_outflow_yi"], 40)
        self.assertEqual(payload["totals"]["visible_balance_yi"], 5)
        self.assertEqual(payload["totals"]["inflow_count"], 2)
        self.assertEqual(payload["totals"]["outflow_count"], 2)
        self.assertFalse(payload["inference"]["enabled"])
        self.assertIn("不推断", payload["inference"]["caveat"])

    def test_deduplicates_rows_filters_zeroes_and_limits_each_side(self):
        inflow = [
            {"name": f"流入{i}", "net_flow_yi": i, "inflow_yi": i + 10, "outflow_yi": 10}
            for i in range(1, 13)
        ]
        outflow = [
            {"name": f"流出{i}", "net_flow_yi": -i, "inflow_yi": 10, "outflow_yi": i + 10}
            for i in range(1, 13)
        ]
        inflow.extend([
            {"name": "流入12", "net_flow_yi": 2},
            {"name": "零流动", "net_flow_yi": 0},
            {"name": "坏数据", "net_flow_yi": "nan"},
        ])

        payload = build_industry_flow_payload({"inflow": inflow, "outflow": outflow})

        self.assertEqual(len(payload["nodes"]), 20)
        self.assertEqual(
            {node["name"] for node in payload["nodes"] if node["role"] == "inflow"},
            {f"流入{i}" for i in range(3, 13)},
        )
        self.assertEqual(
            {node["name"] for node in payload["nodes"] if node["role"] == "outflow"},
            {f"流出{i}" for i in range(3, 13)},
        )
        self.assertEqual(len({node["id"] for node in payload["nodes"]}), 20)

    def test_timeline_ranks_each_shared_snapshot_instead_of_latest_names(self):
        first = {
            "generated_at": "2026-07-20 10:00:00",
            "items": [
                *({"name": f"早盘流入{i}", "net_flow_yi": i} for i in range(1, 11)),
                *({"name": f"早盘流出{i}", "net_flow_yi": -i} for i in range(1, 11)),
            ],
        }
        latest = {
            "generated_at": "2026-07-20 10:01:00",
            "inflow": [{"name": f"午盘流入{i}", "net_flow_yi": i} for i in range(1, 11)],
            "outflow": [{"name": f"午盘流出{i}", "net_flow_yi": -i} for i in range(1, 11)],
            "count": 90,
        }

        payload = build_industry_flow_payload(latest, history_samples=[first, latest])

        first_names = {node["name"] for node in payload["timeline"][0]["nodes"]}
        latest_names = {node["name"] for node in payload["nodes"]}
        self.assertEqual(len(first_names), 20)
        self.assertTrue(all(name.startswith("早盘") for name in first_names))
        self.assertEqual(len(latest_names), 20)
        self.assertTrue(all(name.startswith("午盘") for name in latest_names))
        self.assertEqual(payload["money_flow"]["generated_at"], latest["generated_at"])
        self.assertEqual(payload["money_flow"]["inflow"], latest["inflow"])
        self.assertEqual(payload["money_flow"]["outflow"], latest["outflow"])

    def test_older_net_only_cache_keeps_real_net_and_pressure_magnitude(self):
        payload = build_industry_flow_payload({
            "inflow": [{"name": "半导体", "net_flow_yi": 12.5}],
            "outflow": [{"name": "银行", "net_flow_yi": -8.0}],
            "stale_cache": True,
            "error": "upstream down",
        })
        nodes = {node["name"]: node for node in payload["nodes"]}

        self.assertAlmostEqual(nodes["半导体"]["magnitude_yi"], 12.5)
        self.assertAlmostEqual(nodes["银行"]["magnitude_yi"], 8.0)
        self.assertEqual(nodes["半导体"]["inflow_yi"], 12.5)
        self.assertEqual(nodes["半导体"]["outflow_yi"], 0.0)
        self.assertEqual(nodes["银行"]["inflow_yi"], 0.0)
        self.assertEqual(nodes["银行"]["outflow_yi"], 8.0)
        self.assertTrue(payload["stale_cache"])
        self.assertEqual(payload["error"], "upstream down")

    def test_payload_propagates_shared_main_net_metric_and_source(self):
        source = {
            "schema_version": 2,
            "metric": "industry_main_net_flow",
            "metric_label": "今日主力净额",
            "source": "东方财富行业板块主力净额",
            "source_url": "https://data.eastmoney.com/bkzj/hy.html",
            "generated_at": "2026-07-20 10:00:00",
            "inflow": [{"name": "银行", "net_flow_yi": 6}],
            "outflow": [{"name": "半导体", "net_flow_yi": -102.1}],
            "count": 128,
        }

        payload = build_industry_flow_payload(source)

        self.assertEqual(payload["metric"], "industry_main_net_flow")
        self.assertEqual(payload["metric_label"], "今日主力净额")
        self.assertEqual(payload["source"], "东方财富行业板块主力净额")
        self.assertEqual(payload["source_url"], source["source_url"])
        self.assertEqual(payload["money_flow"]["metric"], "industry_main_net_flow")
        self.assertEqual(payload["money_flow"]["source"], "东方财富行业板块主力净额")
        self.assertEqual(payload["money_flow"]["count"], 128)
        self.assertIn("主力净额", payload["inference"]["method"])
        self.assertIn("主力净额", payload["inference"]["caveat"])

    def test_history_keeps_bounded_current_day_samples_and_replaces_same_timestamp(self):
        history: dict = {}
        for minute in range(4):
            history = append_industry_flow_sample(history, {
                "generated_at": f"2026-07-20 10:0{minute}:00",
                "items": [{"name": "半导体", "net_flow_yi": minute + 1}],
            }, max_points=3)
        history = append_industry_flow_sample(history, {
            "generated_at": "2026-07-20 10:03:00",
            "items": [{"name": "半导体", "net_flow_yi": 9}],
        }, max_points=3)

        self.assertEqual([sample["generated_at"] for sample in history["samples"]], [
            "2026-07-20 10:01:00",
            "2026-07-20 10:02:00",
            "2026-07-20 10:03:00",
        ])
        self.assertEqual(history["samples"][-1]["items"][0]["net_flow_yi"], 9)

        next_day = append_industry_flow_sample(history, {
            "generated_at": "2026-07-21 09:30:00",
            "items": [{"name": "银行", "net_flow_yi": -2}],
        })
        self.assertEqual(next_day["date"], "2026-07-21")
        self.assertEqual(len(next_day["samples"]), 1)

    def test_payload_exposes_real_sample_timeline_for_number_animation(self):
        first = {
            "generated_at": "2026-07-20 10:00:00",
            "items": [
                {"name": "半导体", "net_flow_yi": 10, "inflow_yi": 30, "outflow_yi": 20},
                {"name": "银行", "net_flow_yi": -6, "inflow_yi": 12, "outflow_yi": 18},
            ],
        }
        latest = {
            "generated_at": "2026-07-20 10:01:00",
            "items": [
                {"name": "半导体", "net_flow_yi": 18, "inflow_yi": 42, "outflow_yi": 24},
                {"name": "银行", "net_flow_yi": -3, "inflow_yi": 20, "outflow_yi": 23},
            ],
        }

        payload = build_industry_flow_payload(latest, history_samples=[first, latest])

        self.assertTrue(payload["sampling"]["actual_playback"])
        self.assertEqual(payload["sampling"]["point_count"], 2)
        self.assertEqual(payload["sampling"]["first_sample_at"], "2026-07-20 10:00:00")
        self.assertEqual(payload["sampling"]["last_sample_at"], "2026-07-20 10:01:00")
        self.assertEqual(payload["sampling"]["expected_points_through_last_sample"], 37)
        self.assertEqual(payload["sampling"]["expected_full_day_points"], 249)
        self.assertEqual(payload["sampling"]["timezone"], "Asia/Shanghai")
        self.assertEqual(payload["sampling"]["windows"], [
            {"start": "09:25", "end": "11:31"},
            {"start": "13:00", "end": "15:01"},
        ])
        self.assertEqual(payload["sampling"]["storage_limit"], 300)
        self.assertFalse(payload["sampling"]["historical_backfill"]["available"])
        self.assertEqual([frame["generated_at"] for frame in payload["timeline"]], [
            "2026-07-20 10:00:00",
            "2026-07-20 10:01:00",
        ])
        first_nodes = {node["name"]: node for node in payload["timeline"][0]["nodes"]}
        last_nodes = {node["name"]: node for node in payload["timeline"][1]["nodes"]}
        self.assertEqual(first_nodes["半导体"]["net_flow_yi"], 10)
        self.assertEqual(last_nodes["半导体"]["net_flow_yi"], 18)
        self.assertIn("本地周期采样", payload["source"])

    def test_timeline_excludes_samples_outside_the_fixed_sessions(self):
        def sample(generated_at: str, net_flow_yi: float) -> dict:
            return {
                "generated_at": generated_at,
                "items": [{"name": "半导体", "net_flow_yi": net_flow_yi}],
            }

        history = [
            sample("2026-07-20 09:24:00", 1),
            sample("2026-07-20 09:25:00", 2),
            sample("2026-07-20 11:31:00", 3),
            sample("2026-07-20 11:32:00", 4),
            sample("2026-07-20 13:00:00", 5),
            sample("2026-07-20 15:01:00", 6),
        ]
        latest = sample("2026-07-20 15:02:00", 7)

        payload = build_industry_flow_payload(latest, history_samples=history)

        self.assertEqual([frame["generated_at"] for frame in payload["timeline"]], [
            "2026-07-20 09:25:00",
            "2026-07-20 11:31:00",
            "2026-07-20 13:00:00",
            "2026-07-20 15:01:00",
        ])
        self.assertEqual(payload["sampling"]["expected_full_day_points"], 249)

    def test_payload_applies_configured_side_limit_sampling_interval_and_playback_speed(self):
        latest = {
            "generated_at": "2026-07-20 15:00:00",
            "items": [
                *({"name": f"流入{i}", "net_flow_yi": i} for i in range(1, 4)),
                *({"name": f"流出{i}", "net_flow_yi": -i} for i in range(1, 4)),
            ],
        }

        payload = build_industry_flow_payload(
            latest,
            side_limit=2,
            history_samples=[latest],
            sample_interval_seconds=120,
            playback_speed=10,
        )
        history = append_industry_flow_sample({}, latest, interval_seconds=120)

        self.assertEqual(len(payload["nodes"]), 4)
        self.assertEqual(payload["settings"], {
            "side_limit": 2,
            "playback_speed": 10,
        })
        self.assertEqual(payload["sampling"]["interval_seconds"], 120)
        self.assertEqual(payload["sampling"]["expected_full_day_points"], 125)
        self.assertEqual(history["interval_seconds"], 120)

    def test_custom_sampling_windows_control_filtering_and_coverage(self):
        windows = (("09:20", "11:00"), ("13:30", "14:30"))
        history = [
            {"generated_at": "2026-07-20 09:20:00", "items": [{"name": "银行", "net_flow_yi": 1}]},
            {"generated_at": "2026-07-20 11:01:00", "items": [{"name": "银行", "net_flow_yi": 2}]},
            {"generated_at": "2026-07-20 13:30:00", "items": [{"name": "银行", "net_flow_yi": 3}]},
        ]
        latest = {
            "generated_at": "2026-07-20 14:30:00",
            "items": [{"name": "银行", "net_flow_yi": 4}],
        }

        payload = build_industry_flow_payload(
            latest,
            history_samples=history,
            sampling_windows=windows,
        )

        self.assertEqual(payload["sampling"]["windows"], [
            {"start": "09:20", "end": "11:00"},
            {"start": "13:30", "end": "14:30"},
        ])
        self.assertEqual(payload["sampling"]["expected_full_day_points"], 162)
        self.assertEqual([frame["generated_at"] for frame in payload["timeline"]], [
            "2026-07-20 09:20:00",
            "2026-07-20 13:30:00",
            "2026-07-20 14:30:00",
        ])
        self.assertTrue(is_industry_flow_session_timestamp(
            "2026-07-20 14:30:00",
            sampling_windows=windows,
        ))
        self.assertFalse(is_industry_flow_session_timestamp(
            "2026-07-20 14:31:00",
            sampling_windows=windows,
        ))

    def test_sampling_windows_reject_invalid_order(self):
        with self.assertRaisesRegex(ValueError, "上午开始"):
            normalize_industry_flow_sampling_windows(
                (("11:31", "09:25"), ("13:00", "15:01"))
            )

    def test_equal_chip_volume_normalizes_same_day_net_only(self):
        latest = {
            "generated_at": "2026-07-20 10:30:00",
            "items": [
                {"name": "银行", "net_flow_yi": -60, "inflow_yi": 10, "outflow_yi": 70},
                {"name": "半导体", "net_flow_yi": 30, "inflow_yi": 50, "outflow_yi": 20},
                {"name": "煤炭", "net_flow_yi": 0.5, "inflow_yi": 3, "outflow_yi": 2.5},
            ],
        }

        payload = build_industry_flow_payload(latest)
        nodes = {node["name"]: node for node in payload["nodes"]}

        # S = max(8, 60) = 60; volume = 1 + 0.8 * clip(net/S, -1, 1)
        self.assertAlmostEqual(nodes["银行"]["volume_scale_yi"], 60.0)
        self.assertAlmostEqual(nodes["银行"]["volume_score"], -1.0)
        self.assertAlmostEqual(nodes["银行"]["volume_yi"], 0.2)
        self.assertAlmostEqual(nodes["半导体"]["volume_score"], 0.5)
        self.assertAlmostEqual(nodes["半导体"]["volume_yi"], 1.4)
        self.assertAlmostEqual(nodes["煤炭"]["volume_yi"], 1.0 + 0.8 * (0.5 / 60.0), places=4)
        self.assertEqual(nodes["银行"]["base_volume_yi"], 1.0)
        self.assertAlmostEqual(nodes["银行"]["magnitude_yi"], 60.0)
        self.assertEqual(payload["sampling"]["volume_model"]["mode"], "equal_chip_same_day_net")
        self.assertIn("开盘体积相同", payload["inference"]["caveat"])
        self.assertIn("跨日", payload["inference"]["caveat"])


if __name__ == "__main__":
    unittest.main()
