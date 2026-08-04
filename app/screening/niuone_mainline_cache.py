"""Independent persisted state for the NiuOne mainline scanner."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

try:
    from app.core.json_cache import read_json_cache, write_json_cache
except ModuleNotFoundError:  # Standalone entrypoints add app/ directly to sys.path.
    from core.json_cache import read_json_cache, write_json_cache


NIUONE_MAINLINE_CACHE_SCHEMA_VERSION = 10
NIUONE_MAINLINE_SUMMARY_CACHE_SCHEMA_VERSION = 1

_THEME_ATTRIBUTION_FIELDS = (
    "theme",
    "theme_member_count",
    "membership_source",
    "current_score",
    "historical_prior_score",
    "attribution_score",
    "attribution_weight",
    "leadership_eligible",
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

_SUMMARY_CONTEXT_FIELDS = (
    "as_of_date",
    "theme_count",
    "strong_stock_count",
    "mapped_stock_count",
)
_SUMMARY_MARKET_FIELDS = (
    "score",
    "state",
    "raw_state",
    "hard_stop",
    "breadth_score",
    "median_change_pct",
    "limit_up",
    "limit_down",
)
_SUMMARY_MAINLINE_FIELDS = (
    "mode",
    "primary",
    "primary_score",
    "secondary",
    "secondary_score",
    "score_gap",
    "reason",
    "intraday_primary",
    "intraday_primary_score",
    "today_primary",
    "today_primary_score",
    "today_primary_breadth_pct",
)
_SUMMARY_THEME_FIELDS = (
    "industry",
    "score",
    "state",
    "raw_state",
    "intraday_state",
    "niuone_lifecycle_stage",
    "niuone_lifecycle_label",
    "niuone_lifecycle_order",
    "niuone_lifecycle_entry_policy",
    "member_count",
    "attributed_member_count",
    "eligible_data",
    "today_eligible_data",
    "today_quote_count",
    "today_data_coverage",
    "today_attributed_data_coverage",
    "today_up_count",
    "today_1_5pct_count",
    "today_3pct_count",
    "today_5pct_count",
    "today_breadth_pct",
    "today_attributed_quote_count",
    "today_attributed_up_count",
    "today_attributed_breadth_pct",
    "today_adjusted_breadth_pct",
    "today_median_change_pct",
    "today_strength_score",
    "today_leadership_score",
    "strong_stock_count",
    "raw_strong_stock_count",
    "attributed_strong_stock_count",
    "effective_strong_count",
    "effective_breadth_pct",
    "leader_concentration",
    "single_stock_dominated",
    "confirmation_count",
    "intraday_confirmation_count",
    "cross_day_persistent",
    "cross_day_confirmed",
    "mainline_confirmed",
    "core_overlap_count",
    "core_overlap_ratio",
    "continued_core_codes",
    "as_of_date",
    "previous_as_of_date",
    "score_change",
    "flow_net_yi",
    "related_themes",
)
_SUMMARY_STOCK_FIELDS = (
    "code",
    "name",
    "strong_score",
    "change_pct",
    "attribution_score",
    "attribution_weight",
    "role",
)
_SUMMARY_COVERAGE_REASON_FIELDS = (
    "key",
    "label",
    "count",
    "description",
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


def _copy_mapping_fields(
    value: object,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {key: value.get(key) for key in fields if key in value}


def _summary_stock_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        row
        for item in value[:5]
        if (row := _copy_mapping_fields(item, _SUMMARY_STOCK_FIELDS))
    ]


def _summary_theme(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    theme = _copy_mapping_fields(value, _SUMMARY_THEME_FIELDS)
    theme["continued_core_codes"] = list(
        value.get("continued_core_codes") or []
    )[:5]
    theme["related_themes"] = list(value.get("related_themes") or [])[:5]
    theme["strong_stocks"] = _summary_stock_rows(value.get("strong_stocks"))
    theme["today_leaders"] = _summary_stock_rows(value.get("today_leaders"))
    return theme if str(theme.get("industry") or "").strip() else None


def build_niuone_mainline_summary_cache_payload(
    scan: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the bounded theme read model used by Dashboard projections."""
    context = (
        scan.get("niuone_context")
        if isinstance(scan.get("niuone_context"), Mapping)
        else {}
    )
    raw_themes = (
        context.get("themes")
        if isinstance(context.get("themes"), Mapping)
        else {}
    )
    diagnostics = (
        context.get("coverage_diagnostics")
        if isinstance(context.get("coverage_diagnostics"), Mapping)
        else {}
    )
    reasons = [
        compact
        for reason in list(diagnostics.get("reasons") or [])[:8]
        if (compact := _copy_mapping_fields(
            reason,
            _SUMMARY_COVERAGE_REASON_FIELDS,
        ))
    ]
    compact_context = _copy_mapping_fields(context, _SUMMARY_CONTEXT_FIELDS)
    compact_context.update(
        {
            "market": _copy_mapping_fields(
                context.get("market"), _SUMMARY_MARKET_FIELDS
            ),
            "mainline": _copy_mapping_fields(
                context.get("mainline"), _SUMMARY_MAINLINE_FIELDS
            ),
            "themes": {
                str(name): theme
                for name, value in raw_themes.items()
                if (theme := _summary_theme(value)) is not None
            },
            "coverage_diagnostics": {
                "prepared_stock_count": diagnostics.get("prepared_stock_count"),
                "reasons": reasons,
            },
        }
    )
    return {
        "schema_version": NIUONE_MAINLINE_SUMMARY_CACHE_SCHEMA_VERSION,
        "snapshot_kind": "niuone_mainline_summary",
        "generated_at": str(scan.get("generated_at") or "")[:19],
        "quote_generated_at": str(scan.get("quote_generated_at") or "")[:19],
        "refresh_mode": str(scan.get("refresh_mode") or "")[:32],
        "calculation_duration_ms": max(
            0, int(scan.get("calculation_duration_ms") or 0)
        ),
        "reference_stock_universe_label": str(
            scan.get("reference_stock_universe_label") or ""
        ),
        "reference_pool_count": int(scan.get("reference_pool_count") or 0),
        "reference_analysis_count": int(
            scan.get("reference_analysis_count") or 0
        ),
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


def write_niuone_mainline_summary_cache(
    path: Path,
    scan: Mapping[str, Any],
) -> dict[str, Any]:
    payload = build_niuone_mainline_summary_cache_payload(scan)
    write_json_cache(Path(path), payload)
    return payload
