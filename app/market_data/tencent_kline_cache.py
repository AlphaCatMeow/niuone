"""Durable Tencent daily-K-line cache used by full-market scans.

The cache stores one compact JSON series per symbol in SQLite.  A pre-market
refresh replaces only successfully downloaded symbols, so transient upstream
failures never delete the latest valid local history.
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from app.core.paths import get_dashboard_home
except ImportError:  # pragma: no cover - legacy top-level import path
    from core.paths import get_dashboard_home


SCHEMA_VERSION = 1
DEFAULT_KLINE_COUNT = 120
DEFAULT_PREWARM_WORKERS = 12
DEFAULT_HTTP_TIMEOUT_SECONDS = 15.0
TENCENT_KLINE_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = get_dashboard_home(PROJECT_ROOT) / "market_data" / "tencent_daily_klines.sqlite3"


def kline_cache_path() -> Path:
    """Return the private runtime cache path, allowing an explicit override."""
    return Path(
        os.environ.get("DASHBOARD_KLINE_CACHE_DB") or DEFAULT_CACHE_PATH
    ).expanduser()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS kline_series (
            symbol TEXT PRIMARY KEY,
            adjustment TEXT NOT NULL,
            first_trade_date TEXT NOT NULL,
            last_trade_date TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            rows_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            updated_ts REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_kline_series_last_date
        ON kline_series(last_trade_date);

        CREATE TABLE IF NOT EXISTS kline_attempts (
            symbol TEXT PRIMARY KEY,
            attempted_at TEXT NOT NULL,
            error_code TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS prewarm_runs (
            target_date TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            requested_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            duration_seconds REAL NOT NULL DEFAULT 0,
            error_summary TEXT NOT NULL DEFAULT ''
        );
        """
    )
    connection.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    connection.commit()
    return connection


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_kline_rows(rows: Iterable[Mapping[str, Any]], *, limit: int = DEFAULT_KLINE_COUNT) -> list[dict[str, Any]]:
    """Validate, deduplicate and order a bounded daily series."""
    by_date: dict[str, dict[str, Any]] = {}
    for raw in rows or []:
        if not isinstance(raw, Mapping):
            continue
        matched = re.search(r"\d{4}-\d{2}-\d{2}", str(raw.get("date") or ""))
        if not matched:
            continue
        values = {
            key: _finite_float(raw.get(key))
            for key in ("open", "close", "high", "low", "volume")
        }
        if any(values[key] is None for key in ("open", "close", "high", "low")):
            continue
        if any(float(values[key] or 0) <= 0 for key in ("open", "close", "high", "low")):
            continue
        by_date[matched.group(0)] = {
            "date": matched.group(0),
            "open": float(values["open"] or 0),
            "close": float(values["close"] or 0),
            "high": float(values["high"] or 0),
            "low": float(values["low"] or 0),
            "volume": max(0.0, float(values["volume"] or 0)),
        }
    ordered = [by_date[key] for key in sorted(by_date)]
    return ordered[-max(1, int(limit or DEFAULT_KLINE_COUNT)):]


def fetch_tencent_daily_klines(
    symbol: str,
    count: int = DEFAULT_KLINE_COUNT,
    *,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Fetch one bounded qfq daily series from Tencent."""
    normalized_symbol = re.sub(r"[^a-zA-Z0-9]", "", str(symbol or "")).lower()
    if not re.fullmatch(r"(?:sh|sz)\d{6}", normalized_symbol):
        return []
    bounded_count = max(30, min(500, int(count or DEFAULT_KLINE_COUNT)))
    url = f"{TENCENT_KLINE_URL}?param={normalized_symbol},day,,,{bounded_count},qfq"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))
        symbol_payload = (payload.get("data") or {}).get(normalized_symbol) or {}
        raw_rows = symbol_payload.get("day") or symbol_payload.get("qfqday") or []
    except Exception:
        return []
    parsed = []
    for item in raw_rows:
        if not isinstance(item, list) or len(item) < 6:
            continue
        parsed.append({
            "date": item[0],
            "open": item[1],
            "close": item[2],
            "high": item[3],
            "low": item[4],
            "volume": item[5],
        })
    return normalize_kline_rows(parsed, limit=bounded_count)


def store_kline_series(
    series_by_symbol: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    path: Path | None = None,
    fetched_at: str = "",
) -> int:
    """Atomically replace successful symbol series and retain all others."""
    normalized: list[tuple[Any, ...]] = []
    timestamp = fetched_at or _now_text()
    updated_ts = time.time()
    for raw_symbol, raw_rows in (series_by_symbol or {}).items():
        symbol = re.sub(r"[^a-zA-Z0-9]", "", str(raw_symbol or "")).lower()
        if not re.fullmatch(r"(?:sh|sz)\d{6}", symbol):
            continue
        rows = normalize_kline_rows(raw_rows, limit=DEFAULT_KLINE_COUNT)
        if not rows:
            continue
        normalized.append((
            symbol,
            "qfq",
            str(rows[0]["date"]),
            str(rows[-1]["date"]),
            len(rows),
            json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
            timestamp,
            updated_ts,
        ))
    if not normalized:
        return 0
    connection = _open_database(Path(path or kline_cache_path()))
    try:
        with connection:
            connection.executemany(
                """
                INSERT INTO kline_series(
                    symbol, adjustment, first_trade_date, last_trade_date,
                    row_count, rows_json, fetched_at, updated_ts
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    adjustment=excluded.adjustment,
                    first_trade_date=excluded.first_trade_date,
                    last_trade_date=excluded.last_trade_date,
                    row_count=excluded.row_count,
                    rows_json=excluded.rows_json,
                    fetched_at=excluded.fetched_at,
                    updated_ts=excluded.updated_ts
                """,
                normalized,
            )
            connection.executemany(
                "DELETE FROM kline_attempts WHERE symbol=?",
                [(row[0],) for row in normalized],
            )
    finally:
        connection.close()
    return len(normalized)


def record_kline_failures(
    failures: Mapping[str, str],
    *,
    path: Path | None = None,
    attempted_at: str = "",
) -> None:
    """Record bounded diagnostics without overwriting previously valid rows."""
    rows = [
        (
            re.sub(r"[^a-zA-Z0-9]", "", str(symbol or "")).lower(),
            attempted_at or _now_text(),
            re.sub(r"[^a-zA-Z0-9_.-]", "", str(error or "unavailable"))[:80] or "unavailable",
        )
        for symbol, error in (failures or {}).items()
    ]
    rows = [row for row in rows if re.fullmatch(r"(?:sh|sz)\d{6}", row[0])]
    if not rows:
        return
    connection = _open_database(Path(path or kline_cache_path()))
    try:
        with connection:
            connection.executemany(
                """
                INSERT INTO kline_attempts(symbol, attempted_at, error_code)
                VALUES(?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    attempted_at=excluded.attempted_at,
                    error_code=excluded.error_code
                """,
                rows,
            )
    finally:
        connection.close()


def load_kline_series_map(
    symbols: Iterable[str],
    *,
    path: Path | None = None,
    accepted_last_dates: set[str] | None = None,
    min_rows: int = 30,
    count: int = DEFAULT_KLINE_COUNT,
) -> dict[str, list[dict[str, Any]]]:
    """Bulk-load fresh cached histories with one SQLite read per symbol chunk."""
    unique = list(dict.fromkeys(
        re.sub(r"[^a-zA-Z0-9]", "", str(symbol or "")).lower()
        for symbol in symbols
    ))
    unique = [symbol for symbol in unique if re.fullmatch(r"(?:sh|sz)\d{6}", symbol)]
    cache_path = Path(path or kline_cache_path())
    if not unique or not cache_path.exists():
        return {}
    accepted = {str(value)[:10] for value in (accepted_last_dates or set()) if str(value)[:10]}
    result: dict[str, list[dict[str, Any]]] = {}
    connection = _open_database(cache_path)
    try:
        for offset in range(0, len(unique), 800):
            chunk = unique[offset:offset + 800]
            placeholders = ",".join("?" for _ in chunk)
            query = (
                "SELECT symbol, last_trade_date, row_count, rows_json "
                f"FROM kline_series WHERE symbol IN ({placeholders})"
            )
            for row in connection.execute(query, chunk):
                if int(row["row_count"] or 0) < max(1, int(min_rows or 1)):
                    continue
                if accepted and str(row["last_trade_date"] or "")[:10] not in accepted:
                    continue
                try:
                    parsed = json.loads(str(row["rows_json"] or "[]"))
                except (TypeError, ValueError):
                    continue
                normalized_rows = normalize_kline_rows(parsed, limit=count)
                if len(normalized_rows) >= max(1, int(min_rows or 1)):
                    result[str(row["symbol"])] = normalized_rows
    finally:
        connection.close()
    return result


def load_cached_kline_symbols(
    *,
    path: Path | None = None,
    min_rows: int = 1,
) -> tuple[str, ...]:
    """Return cached symbols without loading their JSON histories."""
    cache_path = Path(path or kline_cache_path())
    if not cache_path.exists():
        return ()
    connection = _open_database(cache_path)
    try:
        rows = connection.execute(
            "SELECT symbol FROM kline_series WHERE row_count >= ? ORDER BY symbol",
            (max(1, int(min_rows or 1)),),
        )
        return tuple(str(row["symbol"] or "") for row in rows if row["symbol"])
    finally:
        connection.close()


def quote_trade_date(quote: Mapping[str, Any] | None) -> str:
    matched = re.search(
        r"(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})",
        str((quote or {}).get("quote_time") or ""),
    )
    if not matched:
        return ""
    return f"{matched.group('year')}-{matched.group('month')}-{matched.group('day')}"


def merge_live_quote(
    historical_rows: Iterable[Mapping[str, Any]],
    quote: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Append or replace today's bar without mutating cached completed history."""
    rows = normalize_kline_rows(historical_rows, limit=DEFAULT_KLINE_COUNT)
    trade_date = quote_trade_date(quote)
    price = _finite_float((quote or {}).get("price"))
    if not trade_date or price is None or price <= 0:
        return rows
    open_price = _finite_float((quote or {}).get("open")) or price
    high = _finite_float((quote or {}).get("high")) or price
    low = _finite_float((quote or {}).get("low")) or price
    volume = _finite_float((quote or {}).get("volume")) or 0.0
    live = {
        "date": trade_date,
        "open": open_price,
        "close": price,
        "high": max(high, open_price, price),
        "low": min(low, open_price, price),
        "volume": max(0.0, volume),
    }
    if rows and rows[-1]["date"] == trade_date:
        rows[-1] = live
    else:
        rows.append(live)
    return rows[-DEFAULT_KLINE_COUNT:]


def prewarm_completed_for_date(
    target_date: str,
    *,
    path: Path | None = None,
    minimum_coverage: float = 0.90,
) -> bool:
    cache_path = Path(path or kline_cache_path())
    if not cache_path.exists():
        return False
    connection = _open_database(cache_path)
    try:
        row = connection.execute(
            "SELECT requested_count, success_count, status FROM prewarm_runs WHERE target_date=?",
            (str(target_date)[:10],),
        ).fetchone()
    finally:
        connection.close()
    if not row or str(row["status"] or "") != "completed":
        return False
    requested = int(row["requested_count"] or 0)
    success = int(row["success_count"] or 0)
    return requested > 0 and success / requested >= max(0.0, min(1.0, minimum_coverage))


def prewarm_kline_cache(
    symbols: Iterable[str],
    *,
    path: Path | None = None,
    target_date: str = "",
    workers: int = DEFAULT_PREWARM_WORKERS,
    count: int = DEFAULT_KLINE_COUNT,
    max_attempts: int = 2,
    fetcher: Callable[[str, int], list[dict[str, Any]]] | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> dict[str, Any]:
    """Refresh all symbols concurrently and commit successes in bounded batches."""
    unique = list(dict.fromkeys(
        re.sub(r"[^a-zA-Z0-9]", "", str(symbol or "")).lower()
        for symbol in symbols
    ))
    unique = [symbol for symbol in unique if re.fullmatch(r"(?:sh|sz)\d{6}", symbol)]
    cache_path = Path(path or kline_cache_path())
    run_date = str(target_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    started_at = _now_text()
    started = time.monotonic()
    active_fetcher = fetcher or fetch_tencent_daily_klines
    worker_count = max(1, min(16, int(workers or DEFAULT_PREWARM_WORKERS), len(unique) or 1))
    connection = _open_database(cache_path)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO prewarm_runs(
                    target_date, started_at, requested_count, status
                ) VALUES(?, ?, ?, 'running')
                ON CONFLICT(target_date) DO UPDATE SET
                    started_at=excluded.started_at,
                    finished_at='',
                    requested_count=excluded.requested_count,
                    success_count=0,
                    failure_count=0,
                    status='running',
                    duration_seconds=0,
                    error_summary=''
                """,
                (run_date, started_at, len(unique)),
            )
    finally:
        connection.close()

    def fetch_one(symbol: str) -> tuple[str, list[dict[str, Any]], str]:
        last_error = "empty_response"
        for attempt in range(max(1, int(max_attempts or 1))):
            try:
                rows = normalize_kline_rows(active_fetcher(symbol, count), limit=count)
            except Exception as exc:
                rows = []
                last_error = type(exc).__name__
            if rows:
                return symbol, rows, ""
            if attempt + 1 < max_attempts:
                time.sleep(0.15 * (attempt + 1))
        return symbol, [], last_error

    successes = 0
    failures: dict[str, str] = {}
    pending: dict[str, list[dict[str, Any]]] = {}
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(fetch_one, symbol) for symbol in unique]
        for future in concurrent.futures.as_completed(futures):
            symbol, rows, error = future.result()
            completed += 1
            if rows:
                pending[symbol] = rows
            else:
                failures[symbol] = error or "unavailable"
            if len(pending) >= 100:
                successes += store_kline_series(pending, path=cache_path, fetched_at=started_at)
                pending.clear()
            if progress and (completed % 100 == 0 or completed == len(unique)):
                progress(completed, len(unique), len(failures))
    if pending:
        successes += store_kline_series(pending, path=cache_path, fetched_at=started_at)
    record_kline_failures(failures, path=cache_path, attempted_at=started_at)

    duration = round(time.monotonic() - started, 3)
    error_summary = ",".join(sorted(set(failures.values())))[:500]
    connection = _open_database(cache_path)
    try:
        with connection:
            connection.execute(
                """
                UPDATE prewarm_runs
                SET finished_at=?, success_count=?, failure_count=?, status='completed',
                    duration_seconds=?, error_summary=?
                WHERE target_date=?
                """,
                (_now_text(), successes, len(failures), duration, error_summary, run_date),
            )
    finally:
        connection.close()
    return {
        "target_date": run_date,
        "requested_count": len(unique),
        "success_count": successes,
        "failure_count": len(failures),
        "workers": worker_count,
        "duration_seconds": duration,
        "cache_path": str(cache_path),
        "status": "completed",
    }


__all__ = [
    "DEFAULT_CACHE_PATH",
    "DEFAULT_KLINE_COUNT",
    "DEFAULT_PREWARM_WORKERS",
    "fetch_tencent_daily_klines",
    "kline_cache_path",
    "load_cached_kline_symbols",
    "load_kline_series_map",
    "merge_live_quote",
    "normalize_kline_rows",
    "prewarm_completed_for_date",
    "prewarm_kline_cache",
    "quote_trade_date",
    "record_kline_failures",
    "store_kline_series",
]
