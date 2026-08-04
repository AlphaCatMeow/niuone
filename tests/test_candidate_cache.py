from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core.json_cache import read_json_cache
from app.screening.candidate_cache import (
    PRACTICE_CANDIDATES_CACHE_SCHEMA_VERSION,
    build_practice_candidates_cache_payload,
    write_practice_candidates_cache,
)


class CandidateCacheTests(unittest.TestCase):
    def test_small_snapshot_keeps_candidate_rows_without_full_market_context(self) -> None:
        scan = {
            "generated_at": "2026-08-04 10:00:00",
            "strategy_suite": "niuone",
            "items": [{"code": "600001", "best_score": 8.8}],
            "candidates": [{"code": "600001", "best_score": 8.8}],
            "trade_items": [],
            "strategy_meta": {"niu_leader": {"label": "牛牛领涨"}},
            "market_snapshot": {"sample_count": 5226},
            "niuone_context": {"stocks": {"600001": {"private": "large"}}},
            "sector_tide_context": {"stocks": {"600001": {"private": "large"}}},
            "zettaranc_context": {"industry_money_flow": [{"private": "large"}]},
        }

        payload = build_practice_candidates_cache_payload(
            scan,
            source_cache_name="multi_strategy_latest.json",
        )

        self.assertEqual(
            payload["schema_version"],
            PRACTICE_CANDIDATES_CACHE_SCHEMA_VERSION,
        )
        self.assertEqual(payload["source_cache"], "multi_strategy_latest.json")
        self.assertEqual(payload["items"], scan["items"])
        self.assertEqual(payload["trade_items"], [])
        self.assertEqual(payload["trade_count"], 0)
        self.assertNotIn("niuone_context", payload)
        self.assertNotIn("sector_tide_context", payload)
        self.assertNotIn("zettaranc_context", payload)

    def test_writer_persists_source_provenance_atomically(self) -> None:
        with tempfile.TemporaryDirectory(prefix="niuone-candidates-") as directory:
            root = Path(directory)
            source = root / "multi_strategy_latest.json"
            target = root / "practice_candidates_latest.json"
            source.write_text("{}", encoding="utf-8")

            written = write_practice_candidates_cache(
                target,
                {"items": [{"code": "600001"}], "trade_items": []},
                source_path=source,
            )

            self.assertEqual(read_json_cache(target), written)
            self.assertEqual(written["source_cache"], source.name)
            self.assertEqual(list(root.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
