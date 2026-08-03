from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.backtesting.worker import run_worker
from app.core.json_cache import read_json_cache, write_json_cache


class BacktestWorkerTests(unittest.TestCase):
    def test_worker_writes_progress_and_result_through_private_files(self):
        with tempfile.TemporaryDirectory(prefix="niuone-backtest-worker-") as tmp:
            root = Path(tmp)
            request_path = root / "request.json"
            progress_path = root / "progress.json"
            result_path = root / "result.json"
            error_path = root / "error.json"
            cache_dir = root / "cache"
            write_json_cache(request_path, {"request": {"marker": "request"}})

            def runner(request, *, progress_callback, replay_cache_dir):
                progress_callback.report(
                    75,
                    "scoring",
                    "正在执行策略评分",
                    {
                        "trading_date": "2026-01-05",
                        "day_elapsed_seconds": 1.25,
                        "eta_seconds": 8.0,
                    },
                )
                return {"request": request, "cache_dir": str(replay_cache_dir)}

            with patch(
                "app.backtesting.worker.run_strategy_backtest_request",
                side_effect=runner,
            ), patch("app.backtesting.worker._lower_process_priority") as lower:
                exit_code = run_worker(
                    request_path=request_path,
                    progress_path=progress_path,
                    result_path=result_path,
                    error_path=error_path,
                    cache_dir=cache_dir,
                )

            self.assertEqual(exit_code, 0)
            lower.assert_called_once_with()
            progress = read_json_cache(progress_path)
            self.assertEqual(progress["phase"], "scoring")
            self.assertEqual(progress["details"]["eta_seconds"], 8.0)
            result = read_json_cache(result_path)["result"]
            self.assertEqual(result["request"], {"marker": "request"})
            self.assertEqual(result["cache_dir"], str(cache_dir))
            self.assertFalse(error_path.exists())

    def test_worker_sanitizes_failure(self):
        with tempfile.TemporaryDirectory(prefix="niuone-backtest-worker-") as tmp:
            root = Path(tmp)
            request_path = root / "request.json"
            write_json_cache(request_path, {"request": {"marker": "request"}})
            with patch(
                "app.backtesting.worker.run_strategy_backtest_request",
                side_effect=RuntimeError("failed https://secret.example/token"),
            ):
                exit_code = run_worker(
                    request_path=request_path,
                    progress_path=root / "progress.json",
                    result_path=root / "result.json",
                    error_path=root / "error.json",
                    cache_dir=root / "cache",
                )
            error = read_json_cache(root / "error.json")["error"]
            self.assertEqual(exit_code, 1)
            self.assertIn("<url>", error)
            self.assertNotIn("secret.example", error)


if __name__ == "__main__":
    unittest.main()
