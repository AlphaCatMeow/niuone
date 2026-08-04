"""Bounded candidate read model shared by scanners and the Dashboard."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from app.core.json_cache import write_json_cache
except ModuleNotFoundError:  # Standalone entrypoints add app/ directly to sys.path.
    from core.json_cache import write_json_cache


PRACTICE_CANDIDATES_CACHE_SCHEMA_VERSION = 1

_CANDIDATE_METADATA_FIELDS = (
    "generated_at",
    "refreshed_at",
    "started_at",
    "finished_at",
    "running",
    "stage",
    "stage_label",
    "error",
    "cooldown_remaining_seconds",
    "strategy_suite",
    "enabled_strategy_ids",
    "configured_stock_universe",
    "configured_stock_universe_label",
    "stock_universe",
    "stock_universe_label",
    "reference_stock_universe",
    "reference_stock_universe_label",
    "reference_pool_count",
    "reference_prefilter_count",
    "reference_analysis_count",
    "total_analyzed",
    "strategy_distribution",
    "strategy_meta",
    "strategy_score_profiles",
    "market_snapshot",
    "candidate_refresh",
    "schedule_slot",
    "schedule_run_kind",
    "schedule_triggered_at",
)


def _mapping_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def build_practice_candidates_cache_payload(
    scan: Mapping[str, Any],
    *,
    source_cache_name: str = "",
) -> dict[str, Any]:
    """Keep candidate rows and bounded metadata, excluding full-market context."""
    payload = {
        key: scan.get(key)
        for key in _CANDIDATE_METADATA_FIELDS
        if key in scan
    }
    items = _mapping_rows(scan.get("items") or scan.get("candidates") or [])
    candidates = _mapping_rows(
        scan.get("candidates") if "candidates" in scan else items
    )
    trade_items = _mapping_rows(
        scan.get("trade_items")
        if "trade_items" in scan
        else items
    )
    payload.update(
        {
            "schema_version": PRACTICE_CANDIDATES_CACHE_SCHEMA_VERSION,
            "snapshot_kind": "practice_candidates",
            "source_cache": str(source_cache_name or ""),
            "items": items,
            "candidates": candidates,
            "count": len(items),
            "trade_items": trade_items,
            "trade_count": len(trade_items),
        }
    )
    return payload


def write_practice_candidates_cache(
    path: Path,
    scan: Mapping[str, Any],
    *,
    source_path: Path | None = None,
) -> dict[str, Any]:
    payload = build_practice_candidates_cache_payload(
        scan,
        source_cache_name=Path(source_path).name if source_path is not None else "",
    )
    if source_path is not None:
        try:
            stat = Path(source_path).stat()
        except OSError:
            pass
        else:
            payload["source_version"] = {
                "device": int(stat.st_dev),
                "inode": int(stat.st_ino),
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
    write_json_cache(Path(path), payload)
    return payload
