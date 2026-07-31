#!/usr/bin/env python3
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "requires Windows cmd.exe")
class WindowsBatchLauncherTests(unittest.TestCase):
    def test_first_run_creates_venv_and_second_run_reuses_it(self):
        with tempfile.TemporaryDirectory(prefix="niuone bat ") as temp_dir:
            harness_root = Path(temp_dir) / "checkout with spaces"
            local_data = Path(temp_dir) / "local data"
            scripts_dir = harness_root / "scripts"
            entrypoints_dir = harness_root / "app" / "entrypoints"
            scripts_dir.mkdir(parents=True)
            entrypoints_dir.mkdir(parents=True)

            shutil.copy2(ROOT / "run.bat", harness_root / "run.bat")
            (scripts_dir / "build-frontend.ps1").write_text(
                'param([string]$Root)\nWrite-Output "stub frontend build"\nexit 0\n',
                encoding="utf-8",
            )
            (entrypoints_dir / "niuone_dashboard.py").write_text(
                'print("stub dashboard")\n',
                encoding="utf-8",
            )

            env = os.environ.copy()
            for name in (
                "DASHBOARD_CONFIG",
                "DASHBOARD_ENV_FILE",
                "DASHBOARD_HOME",
                "DASHBOARD_PORTFOLIO_STATE",
                "DASHBOARD_PUSH_HISTORY_DB",
                "DASHBOARD_TRADER_SCRIPT",
                "NIUONE_VENV_DIR",
                "PYTHON_BIN",
            ):
                env.pop(name, None)
            env["NIUONE_LOCAL_DATA_DIR"] = str(local_data)
            env["DASHBOARD_ADMIN_PASSWORD"] = "regression!secret"

            first_run = self._run_launcher(harness_root, env)
            self.assertEqual(
                first_run.returncode,
                0,
                self._failure_output(first_run),
            )
            self.assertIn("Creating Python virtual environment", first_run.stdout)
            self.assertTrue((local_data / ".venv" / "Scripts" / "python.exe").is_file())
            self.assertIn(
                "DASHBOARD_ADMIN_PASSWORD=regression!secret",
                (local_data / "dashboard.env").read_text(encoding="utf-8"),
            )

            second_run = self._run_launcher(harness_root, env)
            self.assertEqual(
                second_run.returncode,
                0,
                self._failure_output(second_run),
            )
            self.assertNotIn("Creating Python virtual environment", second_run.stdout)

    @staticmethod
    def _run_launcher(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["cmd.exe", "/d", "/c", "run.bat", "--no-browser", "--skip-install"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    @staticmethod
    def _failure_output(result: subprocess.CompletedProcess[str]) -> str:
        return f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


if __name__ == "__main__":
    unittest.main()
