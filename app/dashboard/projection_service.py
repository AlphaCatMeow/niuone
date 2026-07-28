"""Server-side orchestration for the Dashboard v2 public read model."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.dashboard.public_projection import build_public_sections
from app.dashboard.public_snapshots import SnapshotPublisher


class DashboardSources(Protocol):
    def practice(self) -> dict[str, Any]: ...
    def candidates(self) -> dict[str, Any]: ...
    def benchmarks(self) -> dict[str, Any]: ...
    def messages(self) -> dict[str, Any]: ...
    def market_summary(self) -> dict[str, Any]: ...
    def niuone_mainline(self) -> dict[str, Any]: ...


@dataclass(slots=True)
class LegacyDashboardSources:
    """Thin adapter around the compatibility-loaded legacy dashboard module."""

    legacy: Any

    def practice(self) -> dict[str, Any]:
        return self.legacy.get_practice_payload_fast()

    def candidates(self) -> dict[str, Any]:
        return self.legacy.load_practice_candidates_cache()

    def benchmarks(self) -> dict[str, Any]:
        return self.legacy.get_practice_benchmarks()

    def messages(self) -> dict[str, Any]:
        return self.legacy.merge_records_from_db(limit=40)

    def market_summary(self) -> dict[str, Any]:
        return self.legacy.get_practice_market_summary_status()

    def niuone_mainline(self) -> dict[str, Any]:
        return self.legacy.load_niuone_mainline_cache_payload()


class ProjectionService:
    """Refresh public projections independently from browser request traffic."""

    def __init__(self, sources: DashboardSources, publisher: SnapshotPublisher, *, interval_seconds: float = 15.0):
        self.sources = sources
        self.publisher = publisher
        self.interval_seconds = max(2.0, float(interval_seconds))
        self._stop = threading.Event()
        self._refresh_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_error = ""
        self._last_refresh_at = ""

    def refresh(self) -> dict[str, Any]:
        with self._refresh_lock:
            practice = self.sources.practice()
            failures: set[str] = set()
            niuone_mainline_reader = getattr(self.sources, "niuone_mainline", lambda: {})
            sections = build_public_sections(
                practice,
                candidates=self._read_optional("candidates", self.sources.candidates, failures),
                benchmarks=self._read_optional("benchmarks", self.sources.benchmarks, failures),
                messages=self._read_optional("messages", self.sources.messages, failures),
                market_summary=self._read_optional("market_summary", self.sources.market_summary, failures),
                niuone_mainline=self._read_optional("niuone_mainline", niuone_mainline_reader, failures),
            )
            for name in failures:
                previous = self._previous_section(name)
                if previous is not None:
                    sections[name] = previous
            if failures:
                sections["metadata"]["degraded"] = True
                sections["metadata"]["stale_sections"] = sorted(failures)
            sections["metadata"]["app_version"] = str(os.environ.get("NIUONE_VERSION") or "dev")
            generated_at = str(practice.get("generated_at") or datetime.now().astimezone().isoformat(timespec="seconds"))
            latest = self.publisher.publish(sections, generated_at=generated_at)
            self._last_refresh_at = datetime.now().astimezone().isoformat(timespec="seconds")
            self._last_error = ""
            return latest

    def _read_optional(
        self,
        name: str,
        reader: Any,
        failures: set[str],
    ) -> dict[str, Any]:
        try:
            value = reader()
            if not isinstance(value, dict):
                raise TypeError(f"{name} source returned {type(value).__name__}")
            return value
        except Exception:
            failures.add(name)
            return {}

    def _previous_section(self, name: str) -> dict[str, Any] | None:
        latest = self.publisher.read_latest() or {}
        revision = int(latest.get("revision") or 0)
        manifest = self.publisher.read_manifest(revision) if revision else None
        reference = (manifest or {}).get("sections", {}).get(name)
        if not isinstance(reference, dict):
            return None
        return self.publisher.read_object(str(reference.get("digest") or ""))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="niuone-public-projection", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(0.0, timeout))

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "last_refresh_at": self._last_refresh_at,
            "last_error": self._last_error,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
            self._stop.wait(self.interval_seconds)
