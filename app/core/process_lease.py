"""Small cross-process leases for bounded background jobs."""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any


class FileLease:
    """Claim one job across processes sharing the same runtime directory.

    The caller must choose ``stale_after_seconds`` above the job's hard timeout.
    That makes recovery after a killed process possible without stealing a live
    lease from a bounded task.
    """

    def __init__(self, path: Path, *, stale_after_seconds: int | float) -> None:
        self.path = Path(path)
        self.stale_after_seconds = max(1.0, float(stale_after_seconds))
        self.token = secrets.token_hex(16)
        self.acquired = False

    def _payload(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "created_at": time.time(),
            "token": self.token,
        }

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                try:
                    age_seconds = time.time() - self.path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age_seconds <= self.stale_after_seconds:
                    return False
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    continue
                continue
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(self._payload(), stream, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                raise
            self.acquired = True
            return True
        return False

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            payload = {}
        if str(payload.get("token") or "") == self.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def __enter__(self) -> "FileLease":
        if not self.acquire():
            raise RuntimeError(f"job lease already held: {self.path.name}")
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
