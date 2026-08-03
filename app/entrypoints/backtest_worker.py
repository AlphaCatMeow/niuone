"""Thin supported entrypoint for one isolated administrator backtest."""
from __future__ import annotations

import sys
from pathlib import Path


_ENTRYPOINT_DIR = Path(__file__).resolve().parent
if str(_ENTRYPOINT_DIR) not in sys.path:
    sys.path.insert(0, str(_ENTRYPOINT_DIR))

# Importing the shared bootstrap installs app/compat, app, and the project root
# before domain modules load. Historical bare imports must resolve exactly as
# they do in the Dashboard process.
import _bootstrap  # noqa: F401, E402

from app.backtesting.worker import main


if __name__ == "__main__":
    raise SystemExit(main())
