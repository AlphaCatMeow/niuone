#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

from app.market_data import tencent_kline_cache as cache


def sample_rows(last_day: str = "2026-07-28", count: int = 60) -> list[dict]:
    year, month, day = [int(value) for value in last_day.split("-")]
    rows = []
    for index in range(count):
        current_day = max(1, day - count + index + 1)
        date_text = f"{year:04d}-{month:02d}-{current_day:02d}"
        price = 10 + index / 100
        rows.append({
            "date": date_text,
            "open": price,
            "close": price + 0.02,
            "high": price + 0.05,
            "low": price - 0.05,
            "volume": 1000 + index,
        })
    # The cache only requires ordered ISO dates; use unique earlier months for
    # synthetic histories that would otherwise underflow the calendar month.
    for index, row in enumerate(rows):
        row["date"] = f"2026-{5 + index // 28:02d}-{index % 28 + 1:02d}"
    rows[-1]["date"] = last_day
    return rows


class TencentKlineCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="niuone-kline-cache-")
        self.path = Path(self.temp.name) / "daily.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_store_and_bulk_load_only_accept_fresh_completed_history(self):
        stored = cache.store_kline_series(
            {"sh600001": sample_rows(), "sz000001": sample_rows("2026-07-25")},
            path=self.path,
            fetched_at="2026-07-29 09:10:00",
        )

        self.assertEqual(stored, 2)
        loaded = cache.load_kline_series_map(
            ["sh600001", "sz000001"],
            path=self.path,
            accepted_last_dates={"2026-07-28"},
            min_rows=55,
        )
        self.assertEqual(list(loaded), ["sh600001"])
        self.assertEqual(loaded["sh600001"][-1]["date"], "2026-07-28")

    def test_failed_refresh_preserves_previous_valid_series(self):
        original = sample_rows()
        cache.store_kline_series({"sh600001": original}, path=self.path)
        cache.record_kline_failures({"sh600001": "timeout"}, path=self.path)

        loaded = cache.load_kline_series_map(
            ["sh600001"],
            path=self.path,
            accepted_last_dates={"2026-07-28"},
            min_rows=55,
        )
        self.assertEqual(len(loaded["sh600001"]), 60)
        self.assertEqual(loaded["sh600001"][-1]["close"], original[-1]["close"])

    def test_lists_cached_symbols_without_loading_history_payloads(self):
        cache.store_kline_series(
            {"sh600001": sample_rows(), "sz000001": sample_rows(count=20)},
            path=self.path,
        )

        self.assertEqual(
            cache.load_cached_kline_symbols(path=self.path, min_rows=30),
            ("sh600001",),
        )

    def test_merge_live_quote_appends_or_replaces_without_mutating_cache(self):
        historical = sample_rows()
        original_close = historical[-1]["close"]
        quote = {
            "quote_time": "20260729100501",
            "open": 11.0,
            "price": 11.5,
            "high": 11.8,
            "low": 10.9,
            "volume": 8888,
        }

        merged = cache.merge_live_quote(historical, quote)
        replaced = cache.merge_live_quote(merged, {**quote, "price": 11.7, "high": 12.0})

        self.assertEqual(historical[-1]["close"], original_close)
        self.assertEqual(merged[-1]["date"], "2026-07-29")
        self.assertEqual(merged[-1]["close"], 11.5)
        self.assertEqual(replaced[-1]["close"], 11.7)
        self.assertEqual(len(replaced), len(merged))

    def test_prewarm_records_coverage_and_keeps_successes(self):
        def fetcher(symbol, _count):
            if symbol == "sz000002":
                return []
            return sample_rows()

        result = cache.prewarm_kline_cache(
            ["sh600001", "sz000001", "sz000002"],
            path=self.path,
            target_date="2026-07-29",
            workers=3,
            max_attempts=1,
            fetcher=fetcher,
        )

        self.assertEqual(result["success_count"], 2)
        self.assertEqual(result["failure_count"], 1)
        self.assertFalse(
            cache.prewarm_completed_for_date(
                "2026-07-29", path=self.path, minimum_coverage=0.90
            )
        )
        self.assertTrue(
            cache.prewarm_completed_for_date(
                "2026-07-29", path=self.path, minimum_coverage=0.60
            )
        )


if __name__ == "__main__":
    unittest.main()
