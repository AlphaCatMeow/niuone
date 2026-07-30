from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.screening.niuone_minute import NiuOneMinuteEngine


def history_rows(price: float = 10.0) -> list[dict[str, object]]:
    start = datetime(2026, 5, 1)
    return [
        {
            "date": (start + timedelta(days=index)).strftime("%Y-%m-%d"),
            "open": price,
            "close": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "volume": 100_000,
        }
        for index in range(60)
    ]


def quote_snapshot(generated_at: str, prices: list[float]) -> dict[str, object]:
    quotes = {}
    for index, price in enumerate(prices, start=1):
        code = f"600{index:03d}"
        quotes[f"sh{code}"] = {
            "code": code,
            "name": f"测试股份{index}",
            "price": price,
            "prev_close": 10.0,
            "open": 10.0,
            "high": price,
            "low": 9.9,
            "volume": 100_000 * index,
            "amount": 100_000_000 * index,
            "turnover": float(index),
            "change_pct": (price / 10.0 - 1) * 100,
            "quote_time": generated_at.replace("-", "").replace(" ", "").replace(":", ""),
        }
    changes = [float(quote["change_pct"]) for quote in quotes.values()]
    return {
        "generated_at": generated_at,
        "quote_count": len(quotes),
        "quotes": quotes,
        "market_snapshot": {
            "up": sum(1 for value in changes if value > 0),
            "down": sum(1 for value in changes if value < 0),
            "flat": sum(1 for value in changes if value == 0),
            "median_change_pct": sorted(changes)[len(changes) // 2],
            "limit_up": 0,
            "limit_down": 0,
        },
    }


class NiuOneMinuteRefreshTests(unittest.TestCase):
    def test_recalculates_from_each_fresh_quote_and_reuses_local_history(self) -> None:
        loader_calls: list[list[str]] = []

        def kline_loader(symbols, **_kwargs):
            requested = list(symbols)
            loader_calls.append(requested)
            return {symbol: history_rows() for symbol in requested}

        with tempfile.TemporaryDirectory(prefix="niuone-minute-") as directory:
            root = Path(directory)
            industry_path = root / "industries.json"
            industry_path.write_text("{}", encoding="utf-8")
            engine = NiuOneMinuteEngine(
                kline_cache_path=root / "klines.sqlite3",
                industry_cache_path=industry_path,
                kline_loader=kline_loader,
                industry_loader=lambda _path: {
                    f"600{index:03d}": "半导体" for index in range(1, 5)
                },
                minimum_coverage=0.75,
            )
            previous_payload = {
                "generated_at": "2026-07-29 15:00:00",
                "reference_pool_count": 4,
                "niuone_context": {
                    "as_of_date": "2026-07-29",
                    "previous_trading_day": "2026-07-28",
                    "dragon_tiger": {"available": True, "as_of_date": "2026-07-29"},
                    "news": {"configured": True, "available": True},
                    "themes": {
                        "半导体": {
                            "industry": "半导体",
                            "state": "emerging",
                            "raw_state": "emerging",
                            "as_of_date": "2026-07-29",
                            "confirmation_component": 7.5,
                            "core_stock_codes": ["600001", "600002"],
                        }
                    },
                },
            }

            first = engine.build_scan(
                quote_snapshot("2026-07-30 10:01:00", [15.0, 14.0, 12.0, 10.5]),
                previous_payload=previous_payload,
                now=datetime(2026, 7, 30, 10, 1, 5),
            )
            second = engine.build_scan(
                quote_snapshot("2026-07-30 10:02:00", [15.5, 14.2, 12.2, 10.6]),
                previous_payload=first,
                now=datetime(2026, 7, 30, 10, 2, 4),
            )

        self.assertEqual(len(loader_calls), 1)
        self.assertEqual(first["quote_generated_at"], "2026-07-30 10:01:00")
        self.assertEqual(second["quote_generated_at"], "2026-07-30 10:02:00")
        first_theme = first["niuone_context"]["themes"]["半导体"]
        second_theme = second["niuone_context"]["themes"]["半导体"]
        self.assertEqual(first_theme["confirmation_component"], 7.5)
        self.assertEqual(second_theme["confirmation_component"], 7.5)
        self.assertEqual(first_theme["strong_stocks"][0]["change_pct"], 50.0)
        self.assertEqual(second_theme["strong_stocks"][0]["change_pct"], 55.0)
        self.assertEqual(second["niuone_context"]["refresh_mode"], "minute_quotes")

    def test_rejects_stale_quotes_without_producing_a_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="niuone-minute-stale-") as directory:
            root = Path(directory)
            engine = NiuOneMinuteEngine(
                kline_cache_path=root / "klines.sqlite3",
                industry_cache_path=root / "industries.json",
                kline_loader=lambda symbols, **_kwargs: {
                    symbol: history_rows() for symbol in symbols
                },
                industry_loader=lambda _path: {
                    f"600{index:03d}": "半导体" for index in range(1, 5)
                },
            )
            with self.assertRaisesRegex(ValueError, "stale"):
                engine.build_scan(
                    quote_snapshot("2026-07-30 09:59:00", [15.0, 14.0, 12.0, 10.5]),
                    now=datetime(2026, 7, 30, 10, 2, 1),
                )


if __name__ == "__main__":
    unittest.main()
