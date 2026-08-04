"""Isolated stdin entry for one CPU-heavy minute theme refresh."""

from __future__ import annotations

import json
import sys
from typing import Any

from dashboard.server import run_niuone_mainline_minute_refresh


def main() -> int:
    try:
        quote_snapshot: Any = json.load(sys.stdin.buffer)
        if not isinstance(quote_snapshot, dict):
            raise ValueError("quote snapshot must be an object")
        run_niuone_mainline_minute_refresh(quote_snapshot)
        return 0
    except Exception as exc:
        print(
            f"[WARN] Minute theme-strength refresh retained previous cache: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
