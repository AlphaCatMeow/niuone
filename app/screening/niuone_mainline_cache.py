"""Independent persisted state for the NiuOne mainline scanner."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

try:
    from app.core.json_cache import read_json_cache, write_json_cache
except ModuleNotFoundError:  # Standalone entrypoints add app/ directly to sys.path.
    from core.json_cache import read_json_cache, write_json_cache


NIUONE_MAINLINE_CACHE_SCHEMA_VERSION = 9

_THEME_ATTRIBUTION_FIELDS = (
    "theme",
    "theme_member_count",
    "membership_source",
    "current_score",
    "historical_prior_score",
    "attribution_score",
    "attribution_weight",
    "cohort_alignment_score",
    "peer_resonance_score",
    "return_correlation_score",
    "return_correlation_rank_score",
    "return_correlation_observation_count",
    "return_correlation_peer_count",
    "theme_specificity_score",
    "observation_count",
    "wave_count",
)


def _compact_stock_attributions(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    attributions = [
        {
            key: item.get(key)
            for key in _THEME_ATTRIBUTION_FIELDS
            if key in item
        }
        for item in list(value.get("theme_attributions") or [])
        if isinstance(item, Mapping) and str(item.get("theme") or "").strip()
    ]
    if not attributions:
        return None
    return {"theme_attributions": attributions}


def build_niuone_mainline_cache_payload(scan: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the state needed for cross-day confirmation and the mainline page."""
    context = scan.get("niuone_context") if isinstance(scan.get("niuone_context"), Mapping) else {}
    themes = context.get("themes") if isinstance(context.get("themes"), Mapping) else {}
    raw_stocks = context.get("stocks") if isinstance(context.get("stocks"), Mapping) else {}
    compact_context = {
        key: value
        for key, value in context.items()
        if key not in {"stocks", "industry_money_flow"}
    }
    compact_context["themes"] = dict(themes)
    compact_context["stocks"] = {
        str(code): compact
        for code, value in raw_stocks.items()
        if (compact := _compact_stock_attributions(value)) is not None
    }
    return {
        "schema_version": NIUONE_MAINLINE_CACHE_SCHEMA_VERSION,
        "generated_at": str(scan.get("generated_at") or "")[:19],
        "quote_generated_at": str(scan.get("quote_generated_at") or "")[:19],
        "refresh_mode": str(scan.get("refresh_mode") or "")[:32],
        "calculation_duration_ms": max(0, int(scan.get("calculation_duration_ms") or 0)),
        "reference_stock_universe": list(scan.get("reference_stock_universe") or []),
        "reference_stock_universe_label": str(scan.get("reference_stock_universe_label") or ""),
        "reference_pool_count": int(scan.get("reference_pool_count") or 0),
        "reference_analysis_count": int(scan.get("reference_analysis_count") or 0),
        "niuone_context": compact_context,
    }


def load_cached_niuone_context(path: Path) -> dict[str, Any] | None:
    """Return a persisted NiuOne context without exposing unrelated scan data."""
    payload = read_json_cache(Path(path))
    context = payload.get("niuone_context") if isinstance(payload, Mapping) else None
    if not isinstance(context, Mapping):
        return None
    loaded = dict(context)
    if not loaded.get("as_of_date"):
        loaded["as_of_date"] = str(payload.get("generated_at") or "")[:10]
    return loaded


def write_niuone_mainline_cache(path: Path, scan: Mapping[str, Any]) -> dict[str, Any]:
    payload = build_niuone_mainline_cache_payload(scan)
    write_json_cache(Path(path), payload)
    return payload
