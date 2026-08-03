#!/usr/bin/env python3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ServiceLauncherTests(unittest.TestCase):
    def test_unix_launcher_exposes_service_mode(self):
        text = (ROOT / "run.sh").read_text(encoding="utf-8")
        self.assertIn("--service", text)
        self.assertIn('"$ROOT/scripts/manage-long-running.sh" install', text)
        self.assertIn('"$ROOT/scripts/manage-long-running.sh" is-installed', text)
        self.assertIn('"$ROOT/scripts/manage-long-running.sh" restart', text)
        self.assertLess(text.index('if [[ "$SERVICE_MODE" == "1" ]]'), text.index('exec "$ROOT/run-dashboard.sh"'))

    def test_dashboard_launcher_refuses_to_rebuild_over_a_running_backend(self):
        text = (ROOT / "run-dashboard.sh").read_text(encoding="utf-8")
        port_check = text.index("socket.create_connection")
        frontend_build = text.index('"$BASE/scripts/build-frontend.sh"')
        self.assertLess(port_check, frontend_build)
        self.assertIn("The frontend was not rebuilt", text)

    def test_dashboard_launcher_keeps_public_and_admin_routes_on_one_port(self):
        text = (ROOT / "run-dashboard.sh").read_text(encoding="utf-8")
        self.assertIn('DASHBOARD_PUBLIC_PROJECTION_ENABLED="${DASHBOARD_PUBLIC_PROJECTION_ENABLED:-1}"', text)
        self.assertIn('"$BASE/scripts/build-frontend.sh"', text)
        self.assertNotIn('V2_FRONTEND_DIR/index.html', text)
        self.assertNotIn('exec "$BASE/run-dashboard-v2.sh"', text)
        self.assertIn('exec "$PYTHON_BIN" "$BASE/app/entrypoints/niuone_dashboard.py"', text)

    def test_frontend_builders_cover_unix_and_windows_launchers(self):
        unix_builder = (ROOT / "scripts" / "build-frontend.sh").read_text(encoding="utf-8")
        windows_builder = (ROOT / "scripts" / "build-frontend.ps1").read_text(encoding="utf-8")
        windows_launcher = (ROOT / "run.bat").read_text(encoding="utf-8")
        for text in (unix_builder, windows_builder):
            self.assertIn("pnpm", text)
            self.assertIn("frozen-lockfile", text)
            self.assertIn("existing locked frontend dependencies", text)
            self.assertIn("node_modules", text)
            self.assertIn("web", text.lower())
        self.assertIn("build-frontend.ps1", windows_launcher)

    def test_windows_launcher_resolves_python_before_creating_venv(self):
        launcher = (ROOT / "run.bat").read_text(encoding="utf-8")
        start = launcher.index('set "VENV_CREATED=0"')
        end = launcher.index(":venv_ready", start)
        bootstrap = launcher[start:end]

        self.assertIn('if exist "%PYTHON_BIN%" goto venv_ready', bootstrap)
        self.assertNotIn('if not exist "%PYTHON_BIN%" (', bootstrap)
        self.assertLess(
            bootstrap.index("call :find_python_launcher"),
            bootstrap.index('call %PYTHON_LAUNCHER% -m venv "%VENV_DIR%"'),
        )
        self.assertIn("DisableDelayedExpansion", launcher)

    def test_unix_manager_covers_macos_and_linux_processes(self):
        text = (ROOT / "scripts" / "manage-long-running.sh").read_text(encoding="utf-8")
        for value in (
            "ai.niuone.dashboard",
            "ai.niuone.cron-scheduler",
            "ai.niuone.x-watchlist",
            "niuone-dashboard.service",
            "niuone-cron-scheduler.service",
            "niuone-x-watchlist.service",
            "NIUONE_LOCAL_DATA_DIR",
            "DASHBOARD_ENV_FILE",
            "is-installed",
        ):
            self.assertIn(value, text)

    def test_windows_launcher_and_manager_cover_all_processes(self):
        launcher = (ROOT / "run.bat").read_text(encoding="utf-8")
        manager = (ROOT / "scripts" / "manage-long-running.ps1").read_text(encoding="utf-8")
        runner = (ROOT / "scripts" / "run-windows-service.ps1").read_text(encoding="utf-8")
        self.assertIn("--service", launcher)
        self.assertIn("manage-long-running.ps1", launcher)
        self.assertIn("-Action IsInstalled", launcher)
        self.assertIn("NIUONE_MANAGED_SERVICE_CHILD", runner)
        self.assertIn('"IsInstalled"', manager)
        for task_name in ("NiuOne Dashboard", "NiuOne Cron Scheduler", "NiuOne X Watchlist"):
            self.assertIn(task_name, manager)
        for service_name in ("dashboard", "cron-scheduler", "x-watchlist"):
            self.assertIn(service_name, runner)
        self.assertIn("NIUONE_LOCAL_DATA_DIR", runner)
        self.assertIn("DASHBOARD_ENV_FILE", runner)


if __name__ == "__main__":
    unittest.main()
