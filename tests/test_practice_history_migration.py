#!/usr/bin/env python3
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PracticeHistoryMigrationTests(unittest.TestCase):
    def run_isolated(
        self,
        directory: str,
        code: str,
        *,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update({
            "DASHBOARD_HOME": directory,
            "DASHBOARD_NIUNIU_DB": str(Path(directory) / "practice.db"),
            "DASHBOARD_PORTFOLIO_STATE": str(Path(directory) / "state.json"),
            "PYTHONPATH": os.pathsep.join((
                str(ROOT / "app" / "compat"),
                str(ROOT / "app"),
                str(ROOT),
            )),
        })
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def test_archive_is_idempotent_and_preserves_revised_history(self):
        with tempfile.TemporaryDirectory(prefix="niuone-history-revision-") as directory:
            state_path = Path(directory) / "state.json"
            original_point = {
                "time": "2026-08-04 10:00:00",
                "equity": 100000.0,
                "cash": 100000.0,
            }
            state_path.write_text(
                json.dumps({
                    "positions": {},
                    "trade_log": [],
                    "decision_log": [],
                    "equity_history": [original_point],
                    "daily_equity_history": [],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            code = """
import niuniu_db

revised = {
    "time": "2026-08-04 10:00:00",
    "equity": 100100.0,
    "cash": 100100.0,
}
assert niuniu_db.archive_account_history({"equity_history": [revised]})
assert niuniu_db.archive_account_history({"equity_history": [revised]})
active = niuniu_db.query_account_history("equity_history")
assert active == [revised], active
assert niuniu_db.query_account_history("equity_history", limit=0) == []
"""
            result = self.run_isolated(directory, code)
            self.assertEqual(result.returncode, 0, result.stderr)

            connection = sqlite3.connect(Path(directory) / "practice.db")
            payloads = [
                json.loads(row[0])
                for row in connection.execute(
                    """
                    SELECT payload_json
                    FROM account_history
                    WHERE history_kind = 'equity_history'
                    ORDER BY id
                    """
                )
            ]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE account_history SET event_time = 'rewritten' WHERE id = 1"
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM account_history WHERE id = 1")
            connection.rollback()
            connection.close()

        self.assertEqual(payloads, [original_point, {
            "time": "2026-08-04 10:00:00",
            "equity": 100100.0,
            "cash": 100100.0,
        }])

    def test_save_archives_full_history_before_compacting_json(self):
        with tempfile.TemporaryDirectory(prefix="niuone-history-compact-") as directory:
            state_path = Path(directory) / "state.json"
            seed_path = Path(directory) / "seed.json"
            state = {
                "created_at": "2026-01-01 09:00:00",
                "updated_at": "2026-08-04 10:00:00",
                "initial_cash": 100000.0,
                "cash": 88000.0,
                "positions": {
                    "600000": {
                        "code": "600000",
                        "qty": 100,
                        "avg_cost": 10.0,
                        "last_price": 10.5,
                    }
                },
                "pending_decisions": [{"id": "pending-1", "status": "pending"}],
                "trade_log": [
                    {
                        "time": f"trade-{index:04d}",
                        "action": "BUY",
                        "code": f"{index:06d}",
                        "shares": 100,
                        "price": 10.0,
                        "amount": 1000.0,
                        "reason": "test",
                    }
                    for index in range(205)
                ],
                "decision_log": [
                    {
                        "time": f"decision-{index:04d}",
                        "trade_allowed": True,
                        "decision": {
                            "model": "test",
                            "summary": str(index),
                            "actions": [],
                        },
                    }
                    for index in range(55)
                ],
                "equity_history": [
                    {
                        "time": f"point-{index:04d}",
                        "equity": 100000.0 + index,
                    }
                    for index in range(505)
                ],
                "daily_equity_history": [
                    {
                        "time": f"d{index:09d} 15:00:00",
                        "equity": 100000.0 + index,
                    }
                    for index in range(505)
                ],
            }
            seed_path.write_text(
                json.dumps(state, ensure_ascii=False),
                encoding="utf-8",
            )
            code = """
import json
import os
from pathlib import Path

import niuniu_practice_trader as trader
import niuniu_db

path = Path(os.environ["DASHBOARD_PORTFOLIO_STATE"])
seed_path = path.with_name("seed.json")
state = json.loads(seed_path.read_text(encoding="utf-8"))
trader.save_state(state)
saved = json.loads(path.read_text(encoding="utf-8"))
assert len(saved["trade_log"]) == 200
assert len(saved["decision_log"]) == 50
assert len(saved["equity_history"]) == 500
assert len(saved["daily_equity_history"]) == 500
assert saved["cash"] == 88000.0
assert saved["positions"]["600000"]["qty"] == 100
assert saved["pending_decisions"] == [{"id": "pending-1", "status": "pending"}]
assert len(niuniu_db.query_account_history("trade_log")) == 205
assert len(niuniu_db.query_account_history("decision_log")) == 55
assert len(niuniu_db.query_account_history("equity_history")) == 505
assert len(niuniu_db.query_account_history("daily_equity_history")) == 505
restored_equity = trader.load_account_history(
    "equity_history",
    saved["equity_history"],
    limit=2000,
)
assert len(restored_equity) == 505
assert restored_equity[0]["time"] == "point-0000"
"""
            result = self.run_isolated(directory, code, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_archive_failure_keeps_full_json_history(self):
        with tempfile.TemporaryDirectory(prefix="niuone-history-fallback-") as directory:
            state_path = Path(directory) / "state.json"
            state = {
                "initial_cash": 100000.0,
                "cash": 100000.0,
                "positions": {},
                "trade_log": [],
                "decision_log": [
                    {"time": f"decision-{index:04d}", "decision": {"summary": str(index)}}
                    for index in range(55)
                ],
                "pending_decisions": [],
                "equity_history": [
                    {"time": f"invalid-{index:04d}", "equity": 100000.0 + index}
                    for index in range(505)
                ],
                "daily_equity_history": [],
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            code = """
import json
import os
import sys
import types
from pathlib import Path

import niuniu_practice_trader as trader

failed_db = types.ModuleType("niuniu_db")
failed_db.archive_account_history = lambda _state: False
sys.modules["niuniu_db"] = failed_db
path = Path(os.environ["DASHBOARD_PORTFOLIO_STATE"])
state = json.loads(path.read_text(encoding="utf-8"))
trader.save_state(state)
saved = json.loads(path.read_text(encoding="utf-8"))
assert len(saved["decision_log"]) == 55
assert len(saved["equity_history"]) == 505
"""
            result = self.run_isolated(directory, code)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
