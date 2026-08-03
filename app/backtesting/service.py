"""Compose historical data clients with selection-signal backtesting."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .historical_data import (
    HistoricalDataError,
    HistoricalDataResult,
    HistoricalFetchConfig,
    SourceFetcher,
    fetch_historical_data,
    normalize_a_share_symbol,
)
from .selection import (
    HistoricalBar,
    PositionExitStrategy,
    SelectionBacktestConfig,
    SelectionBacktestResult,
    SelectionFunction,
    SelectionStrategy,
    run_selection_backtest,
)


IndustryMapLoader = Callable[[set[str]], Mapping[str, str]]
ThemeMapLoader = Callable[[set[str]], Mapping[str, Iterable[str]]]
BacktestProgress = Callable[[int, str, str], None]
AnnotationProgress = Callable[[int, int, str], None]


def _code(value: Any) -> str:
    matched = re.search(r"\d{6}", str(value or ""))
    return matched.group(0) if matched else ""


def _normalized_metadata_map(values: Mapping[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_symbol, raw_value in (values or {}).items():
        value = str(raw_value or "").strip()
        if not value:
            continue
        try:
            symbol = normalize_a_share_symbol(raw_symbol)
        except HistoricalDataError:
            continue
        result[symbol] = value
        result[symbol[-6:]] = value
    return result


def _normalized_theme_map(
    values: Mapping[str, Iterable[str]] | None,
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for raw_symbol, raw_values in (values or {}).items():
        if isinstance(raw_values, str):
            candidates: Iterable[Any] = raw_values.split(",")
        else:
            candidates = raw_values or ()
        labels = tuple(dict.fromkeys(
            str(item or "").strip() for item in candidates if str(item or "").strip()
        ))
        if not labels:
            continue
        try:
            symbol = normalize_a_share_symbol(raw_symbol)
        except HistoricalDataError:
            continue
        result[symbol] = labels
        result[symbol[-6:]] = labels
    return result


class _ClassificationMap(dict):
    def __init__(
        self,
        values: Mapping[str, Any],
        *,
        source: str,
        as_of_date: str,
        stale: bool,
    ) -> None:
        super().__init__(values)
        self.source = source
        self.as_of_date = as_of_date
        self.stale = stale


def _current_eastmoney_snapshot(symbols: Iterable[str]):
    codes = {_code(symbol) for symbol in symbols}
    codes.discard("")
    if not codes:
        return None
    try:
        from app.core.paths import get_dashboard_home
        from app.market_data.eastmoney_boards import load_eastmoney_board_snapshot
    except ImportError:  # pragma: no cover - legacy top-level import path
        from core.paths import get_dashboard_home
        from market_data.eastmoney_boards import load_eastmoney_board_snapshot
    project_root = Path(__file__).resolve().parents[2]
    cache_path = (
        get_dashboard_home(project_root)
        / "cron" / "output" / "eastmoney_stock_boards.json"
    )
    return load_eastmoney_board_snapshot(cache_path=cache_path)


def load_current_industry_map(symbols: Iterable[str]) -> dict[str, str]:
    """Return current Eastmoney ``f100`` industries for one bounded universe."""
    codes = {_code(symbol) for symbol in symbols}
    codes.discard("")
    if not codes:
        return {}
    snapshot = _current_eastmoney_snapshot(codes)
    if snapshot is None:
        return {}
    return _ClassificationMap(
        snapshot.industry_map(codes),
        source=snapshot.source,
        as_of_date=snapshot.as_of_date,
        stale=snapshot.stale,
    )


def load_current_theme_map(symbols: Iterable[str]) -> Mapping[str, Iterable[str]]:
    """Return current Eastmoney concepts, falling back only to Eastmoney industry."""
    codes = {_code(symbol) for symbol in symbols}
    codes.discard("")
    if not codes:
        return {}
    snapshot = _current_eastmoney_snapshot(codes)
    if snapshot is None:
        return {}
    return _ClassificationMap(
        snapshot.theme_map(codes),
        source=snapshot.source,
        as_of_date=snapshot.as_of_date,
        stale=snapshot.stale,
    )


@dataclass(frozen=True)
class IndustryAnnotationQuality:
    mode: str
    total_bar_count: int
    matched_bar_count: int
    missing_bar_count: int
    covered_symbol_count: int
    requested_symbol_count: int
    source: str = ""
    snapshot_summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "total_bar_count": self.total_bar_count,
            "matched_bar_count": self.matched_bar_count,
            "missing_bar_count": self.missing_bar_count,
            "bar_coverage_ratio": (
                self.matched_bar_count / self.total_bar_count
                if self.total_bar_count else 0.0
            ),
            "covered_symbol_count": self.covered_symbol_count,
            "requested_symbol_count": self.requested_symbol_count,
            "symbol_coverage_ratio": (
                self.covered_symbol_count / self.requested_symbol_count
                if self.requested_symbol_count else 0.0
            ),
            "source": self.source,
            "snapshot_summary": dict(self.snapshot_summary or {}),
        }


@dataclass(frozen=True)
class HistoricalSelectionBacktestRun:
    data: HistoricalDataResult
    selection: SelectionBacktestResult
    warnings: tuple[str, ...] = ()
    industry_quality: IndustryAnnotationQuality | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data.to_dict(),
            "selection": self.selection.to_dict(),
            "warnings": list(self.warnings),
            "industry_quality": (
                self.industry_quality.to_dict() if self.industry_quality else None
            ),
        }


def _annotated_bars(
    data: HistoricalDataResult,
    symbols: tuple[str, ...],
    *,
    industry_by_symbol: Mapping[str, str] | None,
    industry_loader: IndustryMapLoader | None,
    theme_by_symbol: Mapping[str, Iterable[str]] | None,
    theme_loader: ThemeMapLoader | None,
    name_by_symbol: Mapping[str, str] | None,
    progress_callback: AnnotationProgress | None = None,
) -> tuple[
    Mapping[str, tuple[HistoricalBar, ...]],
    list[str],
    IndustryAnnotationQuality,
]:
    warnings: list[str] = []
    total = len(data.bars_by_symbol)
    if progress_callback is not None:
        progress_callback(0, total, "")
    static_industries = _normalized_metadata_map(industry_by_symbol)
    static_themes = _normalized_theme_map(theme_by_symbol)
    classification_source = ""
    classification_summary: dict[str, Any] = {}
    if industry_loader is not None:
        loaded = industry_loader({_code(symbol) for symbol in symbols})
        static_industries.update(_normalized_metadata_map(loaded))
        classification_source = str(getattr(loaded, "source", "") or "")
        classification_summary.update({
            "as_of_date": str(getattr(loaded, "as_of_date", "") or ""),
            "stale": bool(getattr(loaded, "stale", False)),
        })
    if theme_loader is not None:
        loaded_themes = theme_loader({_code(symbol) for symbol in symbols})
        static_themes.update(_normalized_theme_map(loaded_themes))
        classification_source = str(
            getattr(loaded_themes, "source", "") or classification_source
        )
        classification_summary.update({
            "as_of_date": str(
                getattr(loaded_themes, "as_of_date", "")
                or classification_summary.get("as_of_date")
                or ""
            ),
            "stale": bool(
                getattr(loaded_themes, "stale", False)
                or classification_summary.get("stale")
            ),
        })
    names = _normalized_metadata_map(name_by_symbol)
    bars_by_symbol: dict[str, tuple[HistoricalBar, ...]] = {}
    total_bar_count = 0
    matched_bar_count = 0
    covered_symbols: set[str] = set()
    for completed, (symbol, rows) in enumerate(data.bars_by_symbol.items(), start=1):
        annotated: list[HistoricalBar] = []
        for raw in rows:
            total_bar_count += 1
            row = dict(raw)
            industry = str(
                static_industries.get(symbol)
                or static_industries.get(symbol[-6:])
                or ""
            ).strip()
            themes = tuple(
                static_themes.get(symbol)
                or static_themes.get(symbol[-6:])
                or ()
            )
            if not themes and industry:
                themes = (industry,)
            if themes or industry:
                row["themes"] = list(themes)
                row["industry"] = industry or themes[0]
                matched_bar_count += 1
                covered_symbols.add(symbol)
            else:
                # Raw rows do not own classification metadata. Missing current
                # Eastmoney coverage must remain visibly unclassified.
                row.pop("industry", None)
                row.pop("sector", None)
                row.pop("themes", None)
            name = names.get(symbol) or names.get(symbol[-6:])
            if name:
                row["name"] = name
            annotated.append(HistoricalBar.from_value(symbol, row))
        bars_by_symbol[symbol] = tuple(annotated)
        if progress_callback is not None:
            progress_callback(completed, total, symbol)
    fallback_symbols = [
        symbol for symbol, series in data.series.items() if series.attempts
    ]
    if fallback_symbols:
        displayed = ", ".join(fallback_symbols[:10])
        remaining = len(fallback_symbols) - 10
        suffix = f" (+{remaining} more)" if remaining > 0 else ""
        warnings.append(
            "fallback source used after earlier source failures for "
            f"{len(fallback_symbols)} symbols: {displayed}{suffix}"
        )
    if data.failures:
        warnings.append(
            "partial universe fetched because HistoricalFetchConfig.strict=False: "
            + ", ".join(data.failures)
        )
    missing_bar_count = max(0, total_bar_count - matched_bar_count)
    snapshot_summary: Mapping[str, Any] | None = classification_summary or None
    source = classification_source
    quality = IndustryAnnotationQuality(
        mode="eastmoney_current" if static_industries or static_themes else "missing",
        total_bar_count=total_bar_count,
        matched_bar_count=matched_bar_count,
        missing_bar_count=missing_bar_count,
        covered_symbol_count=len(covered_symbols),
        requested_symbol_count=len(data.bars_by_symbol),
        source=source,
        snapshot_summary=snapshot_summary,
    )
    return MappingProxyType(bars_by_symbol), warnings, quality


def run_historical_selection_backtest(
    symbols: Iterable[str],
    signal_start_date: str,
    signal_end_date: str,
    selector: SelectionStrategy | SelectionFunction,
    *,
    fetch_config: HistoricalFetchConfig | None = None,
    selection_config: SelectionBacktestConfig | None = None,
    position_exit_strategy: PositionExitStrategy | None = None,
    warmup_calendar_days: int = 150,
    forward_calendar_days: int = 45,
    minimum_coverage_ratio: float = 0.0,
    source_fetchers: Mapping[str, SourceFetcher] | None = None,
    industry_by_symbol: Mapping[str, str] | None = None,
    industry_loader: IndustryMapLoader | None = None,
    theme_by_symbol: Mapping[str, Iterable[str]] | None = None,
    theme_loader: ThemeMapLoader | None = None,
    name_by_symbol: Mapping[str, str] | None = None,
    progress_callback: BacktestProgress | None = None,
) -> HistoricalSelectionBacktestRun:
    """Download warmup/forward buffers and evaluate selected stocks."""
    try:
        start = datetime.strptime(str(signal_start_date)[:10], "%Y-%m-%d").date()
        end = datetime.strptime(str(signal_end_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HistoricalDataError("signal dates must use YYYY-MM-DD") from None
    if start > end:
        raise HistoricalDataError("signal_start_date cannot be after signal_end_date")
    if not 0 <= int(warmup_calendar_days) <= 730:
        raise HistoricalDataError("warmup_calendar_days must be between 0 and 730")
    if not 0 <= int(forward_calendar_days) <= 366:
        raise HistoricalDataError("forward_calendar_days must be between 0 and 366")
    if not 0 <= float(minimum_coverage_ratio) <= 1:
        raise HistoricalDataError("minimum_coverage_ratio must be between 0 and 1")
    normalized_symbols = tuple(dict.fromkeys(
        normalize_a_share_symbol(symbol) for symbol in symbols
    ))
    if not normalized_symbols:
        raise HistoricalDataError("at least one symbol is required")
    fetch_start = (start - timedelta(days=int(warmup_calendar_days))).isoformat()
    fetch_end = (end + timedelta(days=int(forward_calendar_days))).isoformat()
    if progress_callback is not None:
        progress_callback(2, "preparing", "正在校验回测参数")

    def fetch_progress(completed: int, total: int, symbol: str, succeeded: bool) -> None:
        if progress_callback is None:
            return
        percent = 5 + round(completed / max(1, total) * 52)
        action = "已获取" if succeeded else "获取失败"
        progress_callback(percent, "fetching", f"{action} {symbol}（{completed}/{total}）")

    cancellation_check = getattr(progress_callback, "check_cancelled", None)
    data = fetch_historical_data(
        normalized_symbols,
        fetch_start,
        fetch_end,
        config=fetch_config,
        source_fetchers=source_fetchers,
        progress_callback=fetch_progress,
        cancellation_check=(
            cancellation_check if callable(cancellation_check) else None
        ),
    )
    coverage_ratio = len(data.series) / len(normalized_symbols)
    if coverage_ratio + 1e-12 < float(minimum_coverage_ratio):
        raise HistoricalDataError(
            "historical universe coverage below minimum: "
            f"{len(data.series)}/{len(normalized_symbols)} "
            f"({coverage_ratio:.1%} < {float(minimum_coverage_ratio):.1%})"
        )
    def annotation_progress(completed: int, total: int, _symbol: str) -> None:
        if progress_callback is None:
            return
        bounded_completed = min(max(0, completed), max(0, total))
        pending = max(0, total - bounded_completed)
        percent = 58 + round(bounded_completed / max(1, total) * 5)
        progress_callback(
            percent,
            "annotating",
            f"正在补充行业信息：已处理 {bounded_completed} 只 / 待处理 {pending} 只",
        )

    bars_by_symbol, warnings, industry_quality = _annotated_bars(
        data,
        tuple(data.bars_by_symbol),
        industry_by_symbol=industry_by_symbol,
        industry_loader=industry_loader,
        theme_by_symbol=theme_by_symbol,
        theme_loader=theme_loader,
        name_by_symbol=name_by_symbol,
        progress_callback=annotation_progress,
    )
    if len(data.series) < len(normalized_symbols):
        warnings.append(
            "historical universe coverage: "
            f"{len(data.series)}/{len(normalized_symbols)} ({coverage_ratio:.1%})"
        )
    resolved_selection = replace(
        selection_config or SelectionBacktestConfig(),
        signal_start_date=start.isoformat(),
        signal_end_date=end.isoformat(),
    )
    def normalization_progress(completed: int, total: int) -> None:
        if progress_callback is None:
            return
        bounded_completed = min(max(0, completed), max(0, total))
        pending = max(0, total - bounded_completed)
        percent = 64 + round(bounded_completed / max(1, total))
        progress_callback(
            percent,
            "normalizing",
            f"正在整理历史行情：已处理 {bounded_completed} 只 / 待处理 {pending} 只",
        )

    def preparation_progress(completed: int, total: int) -> None:
        if progress_callback is None:
            return
        bounded_completed = min(max(0, completed), max(0, total))
        pending = max(0, total - bounded_completed)
        percent = 65 + round(bounded_completed / max(1, total) * 2)
        progress_callback(
            percent,
            "precomputing",
            f"正在预计算技术指标：已处理 {bounded_completed} 只 / 待处理 {pending} 只",
        )

    def selection_progress(completed: int, total: int, trading_date: str) -> None:
        if progress_callback is None:
            return
        percent = 68 + round(completed / max(1, total) * 29)
        progress_callback(
            percent,
            "evaluating",
            f"正在回放 {trading_date}（{completed}/{total}）",
        )

    selection = run_selection_backtest(
        bars_by_symbol,
        selector,
        config=resolved_selection,
        position_exit_strategy=position_exit_strategy,
        progress_callback=selection_progress,
        normalization_progress_callback=normalization_progress,
        preparation_progress_callback=preparation_progress,
    )
    if progress_callback is not None:
        progress_callback(100, "completed", "回测完成")
    return HistoricalSelectionBacktestRun(
        data=data,
        selection=selection,
        warnings=tuple(warnings),
        industry_quality=industry_quality,
    )


__all__ = [
    "HistoricalSelectionBacktestRun",
    "BacktestProgress",
    "IndustryMapLoader",
    "ThemeMapLoader",
    "IndustryAnnotationQuality",
    "load_current_industry_map",
    "load_current_theme_map",
    "run_historical_selection_backtest",
]
