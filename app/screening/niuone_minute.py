"""Minute-level NiuOne theme refresh from one validated quote snapshot.

The fast path deliberately owns no network I/O.  It reuses the current
full-market quote batch, reads completed daily history and industry mappings
from private local caches, and keeps slow external confirmation factors from
the newest full research scan.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from app.market_data.tencent_kline_cache import (
        DEFAULT_KLINE_COUNT,
        load_kline_series_map,
        merge_live_quote,
    )
    from app.screening.stock_universe import (
        FULL_SUPPORTED_NON_ST_UNIVERSE,
        friendly_stock_universe,
        stock_in_universe,
    )
    from app.strategies.scoring.common import compute_ema
    from app.strategies.scoring.niuone import build_niuone_context
except ImportError:  # pragma: no cover - standalone entrypoints add app/ to sys.path
    from market_data.tencent_kline_cache import (
        DEFAULT_KLINE_COUNT,
        load_kline_series_map,
        merge_live_quote,
    )
    from screening.stock_universe import (
        FULL_SUPPORTED_NON_ST_UNIVERSE,
        friendly_stock_universe,
        stock_in_universe,
    )
    from strategies.scoring.common import compute_ema
    from strategies.scoring.niuone import build_niuone_context


MINUTE_REFRESH_MODE = "minute_quotes"
DEFAULT_MINIMUM_COVERAGE = 0.75
DEFAULT_MAX_QUOTE_AGE_SECONDS = 120.0


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stock_code(symbol: Any, quote: Mapping[str, Any]) -> str:
    for value in (quote.get("code"), symbol):
        match = re.search(r"\d{6}", str(value or ""))
        if match:
            return match.group(0)
    return ""


def _quote_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()[:19]
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _industry_cache_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (int(stat.st_mtime_ns), int(stat.st_size))


def load_stock_industry_map(path: Path) -> dict[str, str]:
    """Read the private scanner-produced industry cache without network fallback."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    result: dict[str, str] = {}
    for raw_code, raw_industry in payload.items():
        match = re.search(r"\d{6}", str(raw_code or ""))
        industry = re.sub(r"\s+", "", str(raw_industry or "")).strip()
        if match and industry:
            result[match.group(0)] = industry
    return result


class NiuOneMinuteEngine:
    """Cache slow local inputs and rebuild themes from each fresh quote batch."""

    def __init__(
        self,
        *,
        kline_cache_path: Path,
        industry_cache_path: Path,
        kline_loader: Callable[..., dict[str, list[dict[str, Any]]]] = load_kline_series_map,
        industry_loader: Callable[[Path], dict[str, str]] = load_stock_industry_map,
        minimum_coverage: float = DEFAULT_MINIMUM_COVERAGE,
        max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    ) -> None:
        self.kline_cache_path = Path(kline_cache_path)
        self.industry_cache_path = Path(industry_cache_path)
        self.kline_loader = kline_loader
        self.industry_loader = industry_loader
        self.minimum_coverage = max(0.0, min(1.0, float(minimum_coverage)))
        self.max_quote_age_seconds = max(30.0, float(max_quote_age_seconds))
        self._lock = threading.Lock()
        self._history_date = ""
        self._histories: dict[str, list[dict[str, Any]]] = {}
        self._industry_signature = (0, 0)
        self._industries: dict[str, str] = {}

    def _eligible_quotes(
        self,
        quote_snapshot: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        raw_quotes = quote_snapshot.get("quotes")
        if not isinstance(raw_quotes, Mapping):
            return {}
        eligible: dict[str, dict[str, Any]] = {}
        for raw_symbol, raw_quote in raw_quotes.items():
            if not isinstance(raw_quote, Mapping):
                continue
            symbol = re.sub(r"[^a-zA-Z0-9]", "", str(raw_symbol or "")).lower()
            if not re.fullmatch(r"(?:sh|sz)\d{6}", symbol):
                continue
            quote = dict(raw_quote)
            code = _stock_code(symbol, quote)
            name = str(quote.get("name") or "").strip()
            price = _finite_float(quote.get("price"))
            if (
                not code
                or price is None
                or price <= 0
                or not stock_in_universe(code, name, FULL_SUPPORTED_NON_ST_UNIVERSE)
            ):
                continue
            quote["code"] = code
            eligible[symbol] = quote
        return eligible

    def _load_slow_inputs(
        self,
        symbols: Iterable[str],
        *,
        quote_date: str,
        accepted_last_dates: set[str],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
        unique_symbols = list(dict.fromkeys(symbols))
        with self._lock:
            if self._history_date != quote_date:
                self._history_date = quote_date
                self._histories = {}
            missing = [symbol for symbol in unique_symbols if symbol not in self._histories]
            if missing:
                loaded = self.kline_loader(
                    missing,
                    path=self.kline_cache_path,
                    accepted_last_dates=accepted_last_dates,
                    min_rows=30,
                    count=DEFAULT_KLINE_COUNT,
                )
                for symbol, rows in (loaded or {}).items():
                    self._histories[str(symbol)] = list(rows or [])

            signature = _industry_cache_signature(self.industry_cache_path)
            if not self._industries or signature != self._industry_signature:
                self._industries = self.industry_loader(self.industry_cache_path)
                self._industry_signature = signature

            return (
                {symbol: self._histories.get(symbol, []) for symbol in unique_symbols},
                dict(self._industries),
            )

    def build_scan(
        self,
        quote_snapshot: Mapping[str, Any],
        *,
        previous_payload: Mapping[str, Any] | None = None,
        flow_rows: Any = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Build one cache-ready scan or raise without replacing valid state."""

        started = time.monotonic()
        quote_generated_at = str(quote_snapshot.get("generated_at") or "")[:19]
        quote_time = _quote_timestamp(quote_generated_at)
        current = now or datetime.now()
        if quote_time is None:
            raise ValueError("minute quote snapshot has no valid timestamp")
        quote_age = (current - quote_time).total_seconds()
        if quote_age < -120 or quote_age > self.max_quote_age_seconds:
            raise ValueError("minute quote snapshot is stale")

        quotes = self._eligible_quotes(quote_snapshot)
        if not quotes:
            raise ValueError("minute quote snapshot has no supported non-ST stocks")

        previous_payload = previous_payload if isinstance(previous_payload, Mapping) else {}
        previous_context = (
            previous_payload.get("niuone_context")
            if isinstance(previous_payload.get("niuone_context"), Mapping)
            else {}
        )
        quote_date = quote_time.strftime("%Y-%m-%d")
        previous_context_date = str(previous_context.get("as_of_date") or "")[:10]
        previous_trading_day = (
            previous_context_date
            if previous_context_date and previous_context_date != quote_date
            else str(previous_context.get("previous_trading_day") or "")[:10]
        )
        accepted_dates = {
            value
            for value in (
                quote_date,
                previous_context_date,
                previous_trading_day,
            )
            if value
        }
        histories, industries = self._load_slow_inputs(
            quotes,
            quote_date=quote_date,
            accepted_last_dates=accepted_dates,
        )

        prepared_items: list[dict[str, Any]] = []
        for symbol, quote in quotes.items():
            rows = merge_live_quote(histories.get(symbol, []), quote)
            if len(rows) < 30:
                continue
            closes = [_finite_float(row.get("close")) for row in rows]
            if any(value is None for value in closes):
                continue
            close_values = [float(value) for value in closes if value is not None]
            ema20 = compute_ema(close_values, 20)
            ema50 = compute_ema(close_values, 50)
            rows[-1]["ema20"] = ema20[-1] if ema20 else None
            rows[-1]["ema50"] = ema50[-1] if ema50 else None
            code = str(quote.get("code") or "")
            prepared_items.append({
                "code": code,
                "name": str(quote.get("name") or ""),
                "industry": str(industries.get(code) or ""),
                "quote": quote,
                "rows": rows,
            })

        previous_pool_count = int(previous_payload.get("reference_pool_count") or 0)
        reference_pool_count = max(previous_pool_count, len(quotes))
        prepared_coverage = len(prepared_items) / reference_pool_count if reference_pool_count else 0.0
        if prepared_coverage < self.minimum_coverage:
            raise ValueError(
                f"minute K-line coverage {prepared_coverage:.1%} is below "
                f"{self.minimum_coverage:.0%}"
            )

        market_snapshot = (
            dict(quote_snapshot.get("market_snapshot") or {})
            if isinstance(quote_snapshot.get("market_snapshot"), Mapping)
            else {}
        )
        context = build_niuone_context(
            prepared_items,
            reference_pool_count=reference_pool_count,
            market_snapshot=market_snapshot,
            flow_rows=flow_rows,
            previous_context=dict(previous_context),
            as_of_date=quote_date,
            previous_trading_day=previous_trading_day,
            reuse_previous_external_context=True,
        )
        if float(context.get("data_coverage") or 0.0) < self.minimum_coverage:
            raise ValueError("minute theme coverage is below the safe publish threshold")
        context["quote_generated_at"] = quote_generated_at
        context["refresh_mode"] = MINUTE_REFRESH_MODE
        context["external_context_mode"] = "cached_full_scan"

        generated_at = current.strftime("%Y-%m-%d %H:%M:%S")
        return {
            "generated_at": generated_at,
            "quote_generated_at": quote_generated_at,
            "refresh_mode": MINUTE_REFRESH_MODE,
            "calculation_duration_ms": int((time.monotonic() - started) * 1000),
            "reference_stock_universe": list(FULL_SUPPORTED_NON_ST_UNIVERSE),
            "reference_stock_universe_label": (
                f"{friendly_stock_universe(FULL_SUPPORTED_NON_ST_UNIVERSE)}（全量非 ST）"
            ),
            "reference_pool_count": reference_pool_count,
            "reference_analysis_count": len(quotes),
            "niuone_context": context,
        }


__all__ = [
    "DEFAULT_MAX_QUOTE_AGE_SECONDS",
    "DEFAULT_MINIMUM_COVERAGE",
    "MINUTE_REFRESH_MODE",
    "NiuOneMinuteEngine",
    "load_stock_industry_map",
]
