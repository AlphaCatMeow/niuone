from __future__ import annotations

import unittest

from app.strategies.lifecycle import (
    NIUONE_LIFECYCLE_ACTION_LABELS,
    NIUONE_LIFECYCLE_CLIMAX_SCORE,
    NIUONE_LIFECYCLE_STAGE_ORDER,
    NIUONE_LIFECYCLE_STAGES,
    niuone_lifecycle_entry_blocker,
    niuone_lifecycle_metadata,
    niuone_lifecycle_stage,
    niuone_lifecycle_transition,
)
from app.strategies.policy import candidate_buy_blockers
from app.strategies.registry import STRATEGY_DEFINITIONS, classify_strategy_text
from app.strategies.registry import STRATEGY_SUITES, default_trade_discipline_text
from app.strategies.scoring.common import with_strategy_profile


class NiuOneLifecycleTests(unittest.TestCase):
    def test_stage_order_and_policies_form_one_way_mainline_flow(self):
        self.assertEqual(
            NIUONE_LIFECYCLE_STAGE_ORDER,
            ("brewing", "markup", "climax", "divergence", "fade"),
        )
        self.assertEqual(
            [NIUONE_LIFECYCLE_STAGES[key]["label"] for key in NIUONE_LIFECYCLE_STAGE_ORDER],
            ["主线酝酿", "主线主升", "主线高潮", "主线分歧", "主线退幕"],
        )
        self.assertEqual(
            NIUONE_LIFECYCLE_STAGES["climax"]["entry_policy"],
            "selective_participation_or_reduce",
        )
        self.assertEqual(
            NIUONE_LIFECYCLE_STAGES["divergence"]["entry_policy"],
            "selective_repair_reclaim_or_reduce",
        )
        self.assertEqual(
            NIUONE_LIFECYCLE_STAGES["fade"]["entry_policy"],
            "exit_only",
        )
        self.assertEqual(
            NIUONE_LIFECYCLE_STAGES["markup"][
                "allowed_entry_strategy_ids"
            ],
            ("niu_emerging", "niu_leader"),
        )
        self.assertEqual(
            NIUONE_LIFECYCLE_STAGES["climax"][
                "allowed_entry_strategy_ids"
            ],
            ("niu_leader", "niu_pullback"),
        )
        self.assertEqual(
            NIUONE_LIFECYCLE_STAGES["divergence"][
                "allowed_entry_strategy_ids"
            ],
            ("niu_leader", "niu_pullback"),
        )

    def test_stage_entry_contract_routes_each_production_action(self):
        self.assertIsNone(niuone_lifecycle_entry_blocker(
            "niu_reversal_probe", {"niuone_lifecycle_stage": "brewing"},
        ))
        self.assertIsNone(niuone_lifecycle_entry_blocker(
            "niu_emerging", {"niuone_lifecycle_stage": "markup"},
        ))
        self.assertIsNone(niuone_lifecycle_entry_blocker(
            "niu_leader", {"niuone_lifecycle_stage": "markup"},
        ))
        self.assertIsNone(niuone_lifecycle_entry_blocker(
            "niu_pullback", {"niuone_lifecycle_stage": "divergence"},
        ))
        self.assertIsNone(niuone_lifecycle_entry_blocker(
            "niu_leader", {
                "niuone_lifecycle_stage": "divergence",
            },
        ))
        self.assertIsNone(niuone_lifecycle_entry_blocker(
            "niu_leader", {"niuone_lifecycle_stage": "climax"},
        ))
        self.assertIsNone(niuone_lifecycle_entry_blocker(
            "niu_pullback", {"niuone_lifecycle_stage": "climax"},
        ))
        for stage, strategy_id in (
            ("brewing", "niu_emerging"),
            ("markup", "niu_reversal_probe"),
            ("markup", "niu_pullback"),
            ("climax", "niu_reversal_probe"),
            ("climax", "niu_emerging"),
            ("divergence", "niu_reversal_probe"),
            ("divergence", "niu_emerging"),
            ("fade", "niu_pullback"),
        ):
            with self.subTest(stage=stage, strategy_id=strategy_id):
                action_label = NIUONE_LIFECYCLE_ACTION_LABELS[strategy_id]
                self.assertIn(
                    f"不允许{action_label}开新仓",
                    niuone_lifecycle_entry_blocker(
                        strategy_id,
                        {"niuone_lifecycle_stage": stage},
                    ) or "",
                )

    def test_stage_entry_contract_is_a_production_hard_blocker(self):
        blocked = with_strategy_profile("niu_reversal_probe", {
            "score": 10.0,
            "niuone_lifecycle_stage": "markup",
        })
        self.assertFalse(blocked["actionable"])
        self.assertIn(
            "主线主升阶段不允许牛牛试仓开新仓",
            blocked["hard_blockers"],
        )

        routed = with_strategy_profile("niu_pullback", {
            "score": 10.0,
            "niuone_lifecycle_stage": "divergence",
        })
        self.assertFalse(any(
            "阶段不允许" in reason
            for reason in routed["hard_blockers"]
        ))

    def test_execution_policy_rechecks_route_without_profiled_blockers(self):
        candidate = {
            "best_strategy": "niu_leader",
            "best_score": 10.0,
            "entry_threshold": 8.0,
            "actionable": True,
            "niuone_lifecycle_stage": "climax",
            "stock_leader_rank": 1,
            "stock_leader_tier": True,
            "stock_strong": True,
        }

        self.assertFalse(any(
            "阶段不允许" in reason
            for reason in candidate_buy_blockers(candidate)
        ))

        candidate["niuone_lifecycle_stage"] = "markup"
        self.assertFalse(any(
            "阶段不允许" in reason
            for reason in candidate_buy_blockers(candidate)
        ))

    def test_mapper_uses_only_current_state(self):
        self.assertEqual(niuone_lifecycle_stage({"state": "candidate"}), "brewing")
        self.assertEqual(
            niuone_lifecycle_stage({
                "state": "emerging", "cross_day_persistent": True,
            }),
            "markup",
        )
        self.assertEqual(
            niuone_lifecycle_stage({
                "state": "mainline",
                "mainline_confirmed": True,
                "score": NIUONE_LIFECYCLE_CLIMAX_SCORE,
            }),
            "climax",
        )
        self.assertEqual(
            niuone_lifecycle_stage({
                "state": "mainline",
                "mainline_confirmed": True,
                "score": NIUONE_LIFECYCLE_CLIMAX_SCORE - 0.01,
            }),
            "markup",
        )
        self.assertEqual(niuone_lifecycle_stage({"state": "mainline"}), "markup")
        self.assertEqual(
            niuone_lifecycle_stage({
                "state": "diverging", "mainline_confirmed": True, "score": 60,
            }),
            "divergence",
        )
        self.assertEqual(
            niuone_lifecycle_stage({
                "state": "diverging", "mainline_confirmed": False, "score": 69,
            }),
            "divergence",
        )
        self.assertEqual(niuone_lifecycle_stage({"state": "fading"}), "fade")
        self.assertEqual(niuone_lifecycle_stage({"state": "inactive"}), "")

    def test_transition_prevents_backward_stage_noise_and_resets_after_inactive(self):
        self.assertEqual(
            niuone_lifecycle_transition(
                {"state": "mainline", "niuone_lifecycle_stage": "markup"},
                {"state": "emerging", "cross_day_persistent": False},
            ),
            "divergence",
        )
        self.assertEqual(
            niuone_lifecycle_transition(
                {"state": "mainline", "niuone_lifecycle_stage": "markup"},
                {"state": "mainline", "mainline_confirmed": True, "score": 77},
            ),
            "markup",
        )
        self.assertEqual(
            niuone_lifecycle_transition(
                {"state": "mainline", "niuone_lifecycle_stage": "climax"},
                {"state": "mainline", "mainline_confirmed": True, "score": 77},
            ),
            "divergence",
        )
        self.assertEqual(
            niuone_lifecycle_transition(
                {"state": "emerging", "niuone_lifecycle_stage": "divergence"},
                {"state": "emerging", "cross_day_persistent": False},
            ),
            "divergence",
        )
        self.assertEqual(
            niuone_lifecycle_transition(
                {"state": "emerging", "niuone_lifecycle_stage": "divergence"},
                {"state": "fading"},
            ),
            "fade",
        )
        self.assertEqual(
            niuone_lifecycle_transition(
                {"state": "diverging", "niuone_lifecycle_stage": "climax"},
                {
                    "state": "mainline",
                    "mainline_confirmed": True,
                    "score": NIUONE_LIFECYCLE_CLIMAX_SCORE,
                },
            ),
            "climax",
        )
        self.assertEqual(
            niuone_lifecycle_transition(
                {"state": "fading", "niuone_lifecycle_stage": "fade"},
                {"state": "candidate"},
            ),
            "brewing",
        )
        self.assertEqual(
            niuone_lifecycle_transition(
                {"state": "candidate", "niuone_lifecycle_stage": "fade"},
                {"state": "candidate"},
            ),
            "fade",
        )
        self.assertEqual(
            niuone_lifecycle_transition(
                {"state": "inactive", "niuone_lifecycle_stage": "fade"},
                {"state": "candidate"},
            ),
            "brewing",
        )

    def test_metadata_and_legacy_action_aliases_remain_compatible(self):
        metadata = niuone_lifecycle_metadata({
            "mainline_state": "candidate",
        })
        self.assertEqual(metadata["niuone_lifecycle_label"], "主线酝酿")
        self.assertEqual(metadata["niuone_lifecycle_entry_policy"], "probe_only")
        recorded = niuone_lifecycle_metadata({
            "mainline_state": "emerging",
            "niuone_lifecycle_stage": "divergence",
            "niuone_lifecycle_label": "过期标签",
        })
        self.assertEqual(recorded["niuone_lifecycle_stage"], "divergence")
        self.assertEqual(recorded["niuone_lifecycle_label"], "主线分歧")
        self.assertEqual(
            recorded["niuone_lifecycle_entry_policy"],
            "selective_repair_reclaim_or_reduce",
        )
        self.assertEqual(STRATEGY_DEFINITIONS["niu_reversal_probe"]["label"], "牛牛试仓")
        self.assertEqual(STRATEGY_DEFINITIONS["niu_leader"]["label"], "牛牛领涨")
        self.assertEqual(STRATEGY_DEFINITIONS["niu_pullback"]["label"], "牛牛转强")
        self.assertEqual(classify_strategy_text("牛牛反转"), "niu_reversal_probe")
        self.assertEqual(classify_strategy_text("牛牛领涨"), "niu_leader")
        self.assertEqual(classify_strategy_text("牛牛领航"), "niu_leader")
        self.assertEqual(classify_strategy_text("牛牛转强"), "niu_pullback")
        self.assertEqual(classify_strategy_text("牛牛承接"), "niu_pullback")
        self.assertEqual(classify_strategy_text("牛牛回踩"), "niu_pullback")
        self.assertIn("酝酿、主升、高潮、分歧、退幕", STRATEGY_SUITES["niuone"]["desc"])
        self.assertIn(
            "主线酝酿→主升→高潮→分歧→退幕",
            default_trade_discipline_text(niuone_enabled=True),
        )


if __name__ == "__main__":
    unittest.main()
