"""Isolated process boundary for one administrator backtest request."""
from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from app.core.json_cache import read_json_cache, write_json_cache
except ImportError:  # pragma: no cover - legacy top-level import path
    from core.json_cache import read_json_cache, write_json_cache

from .tasks import _safe_error, run_strategy_backtest_request


def _lower_process_priority() -> None:
    if os.name != "posix":
        return
    try:
        os.nice(10)
    except OSError:
        # Isolation remains valuable on hosts that disallow niceness changes.
        pass


def run_worker(
    *,
    request_path: Path,
    progress_path: Path,
    result_path: Path,
    error_path: Path,
    cache_dir: Path,
) -> int:
    payload = read_json_cache(request_path)
    request = payload.get("request") if isinstance(payload, dict) else None
    if not isinstance(request, dict):
        write_json_cache(error_path, {"error": "BacktestWorkerError: 无效任务参数"})
        return 2
    sequence = 0

    def report(
        percent: int,
        phase: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        nonlocal sequence
        sequence += 1
        write_json_cache(progress_path, {
            "sequence": sequence,
            "progress": min(99, max(0, int(percent))),
            "phase": str(phase or "running"),
            "message": str(message or "正在回测")[:200],
            "details": dict(details or {}),
        })

    def progress(percent: int, phase: str, message: str) -> None:
        report(percent, phase, message)

    setattr(progress, "report", report)
    try:
        _lower_process_priority()
        result = run_strategy_backtest_request(
            request,
            progress_callback=progress,
            replay_cache_dir=cache_dir,
        )
        write_json_cache(result_path, {"result": result})
        return 0
    except Exception as exc:
        write_json_cache(error_path, {
            "error_type": type(exc).__name__,
            "error": _safe_error(exc),
        })
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--progress", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--error", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    arguments = parser.parse_args(argv)
    return run_worker(
        request_path=arguments.request,
        progress_path=arguments.progress,
        result_path=arguments.result,
        error_path=arguments.error,
        cache_dir=arguments.cache_dir,
    )


__all__ = ["main", "run_worker"]
