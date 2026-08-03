"""Thin supported entrypoint for one isolated administrator backtest."""
from __future__ import annotations

from app.backtesting.worker import main


if __name__ == "__main__":
    raise SystemExit(main())
