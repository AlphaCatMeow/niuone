from __future__ import annotations

import io
import json
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone

from app.market_data.eastmoney_concept_boards import (
    EASTMONEY_CONCEPT_BOARD_FILTER,
    EastmoneyConceptBoardError,
    EastmoneyConceptBoardSignalCache,
    fetch_eastmoney_concept_board_signal,
    normalize_eastmoney_concept_name,
    parse_eastmoney_concept_board_payload,
)


CN_TIMEZONE = timezone(timedelta(hours=8))


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self._body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self._body.read(limit)


def sample_payload() -> dict[str, object]:
    quote_time = int(
        datetime(2026, 8, 4, 14, 31, 5, tzinfo=CN_TIMEZONE).timestamp()
    )
    return {
        "data": {
            "total": 503,
            "diff": [
                {
                    "f12": "BK1128",
                    "f14": "AIGC概念",
                    "f2": 1234.5,
                    "f3": 4.6,
                    "f62": 350_000_000,
                    "f104": 80,
                    "f105": 30,
                    "f106": 5,
                    "f128": "利欧股份",
                    "f140": 2131,
                    "f141": 0,
                    "f136": 9.98,
                    "f124": quote_time,
                    "raw_private_field": "must-not-survive",
                },
                {
                    "f12": "BK2000",
                    "f14": "云计算",
                    "f3": 3.2,
                    "f62": -120_000_000,
                    "f104": 60,
                    "f105": 70,
                    "f106": 10,
                    "f128": "美利云",
                    "f140": "000815",
                    "f141": 0,
                    "f136": 10.0,
                    "f124": quote_time - 1,
                },
            ],
        }
    }


class EastmoneyConceptBoardTests(unittest.TestCase):
    def test_parser_keeps_only_bounded_cross_check_fields(self) -> None:
        signal = parse_eastmoney_concept_board_payload(
            sample_payload(),
            captured_at="2026-08-04 14:31:10",
        )

        self.assertEqual(signal.total_count, 503)
        self.assertEqual(signal.quote_generated_at, "2026-08-04 14:31:05")
        self.assertEqual([board.rank for board in signal.boards], [1, 2])
        self.assertEqual(signal.boards[0].name, "AIGC概念")
        self.assertEqual(signal.boards[0].main_net_yi, 3.5)
        self.assertEqual(signal.boards[0].leader_code, "002131")
        serialized = json.dumps(signal.to_dict(), ensure_ascii=False)
        self.assertNotIn("raw_private_field", serialized)

    def test_fetch_uses_change_rank_filter_and_host_fallback(self) -> None:
        requests: list[tuple[str, float]] = []

        def opener(request: object, timeout: float) -> FakeResponse:
            url = request.full_url
            requests.append((url, timeout))
            if "push2delay" in url:
                raise OSError("primary unavailable")
            return FakeResponse(sample_payload())

        signal = fetch_eastmoney_concept_board_signal(
            opener=opener,
            timeout_seconds=2.5,
            now=datetime(2026, 8, 4, 14, 31, 10, tzinfo=CN_TIMEZONE),
        )

        self.assertEqual(len(requests), 2)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(requests[1][0]).query)
        self.assertEqual(query["fid"], ["f3"])
        self.assertEqual(query["fs"], [EASTMONEY_CONCEPT_BOARD_FILTER])
        self.assertEqual(query["pz"], ["100"])
        self.assertEqual(requests[1][1], 2.5)
        self.assertEqual(signal.boards[0].name, "AIGC概念")

    def test_cache_reuses_fresh_data_and_marks_bounded_fallback_stale(self) -> None:
        signal = parse_eastmoney_concept_board_payload(
            sample_payload(),
            captured_at="2026-08-04 14:31:10",
        )
        clock = [0.0]
        calls = [0]

        def fetcher():
            calls[0] += 1
            if calls[0] > 1:
                raise EastmoneyConceptBoardError("temporary failure")
            return signal

        cache = EastmoneyConceptBoardSignalCache()
        first = cache.load(fetcher=fetcher, monotonic=lambda: clock[0])
        clock[0] = 45.0
        fresh = cache.load(fetcher=fetcher, monotonic=lambda: clock[0])
        clock[0] = 61.0
        stale = cache.load(fetcher=fetcher, monotonic=lambda: clock[0])
        clock[0] = 70.0
        backed_off = cache.load(fetcher=fetcher, monotonic=lambda: clock[0])

        self.assertFalse(first.stale)
        self.assertFalse(fresh.stale)
        self.assertTrue(stale.stale)
        self.assertTrue(backed_off.stale)
        self.assertEqual(calls[0], 2)

        clock[0] = 700.0
        with self.assertRaises(EastmoneyConceptBoardError):
            cache.load(fetcher=fetcher, monotonic=lambda: clock[0])

    def test_parser_rejects_empty_payload_and_name_matching_is_conservative(self) -> None:
        with self.assertRaises(EastmoneyConceptBoardError):
            parse_eastmoney_concept_board_payload(
                {"data": {"total": 0, "diff": []}},
                captured_at="2026-08-04 14:31:10",
            )

        self.assertEqual(normalize_eastmoney_concept_name(" AIGC概念 "), "aigc")
        self.assertEqual(normalize_eastmoney_concept_name("云计算"), "云计算")
        self.assertNotEqual(
            normalize_eastmoney_concept_name("AI应用"),
            normalize_eastmoney_concept_name("人工智能"),
        )


if __name__ == "__main__":
    unittest.main()
