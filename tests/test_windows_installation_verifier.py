from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.verify_windows_installation import _dashboard_port, verify_installation


class WindowsInstallationVerifierTests(unittest.TestCase):
    def test_dashboard_port_uses_safe_default_and_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(_dashboard_port(root), 8000)
            (root / "config.json").write_text(
                json.dumps({"dashboard": {"port": 8123}}), encoding="utf-8"
            )
            self.assertEqual(_dashboard_port(root), 8123)
            (root / "config.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(_dashboard_port(root), 8000)

    def test_installation_passes_only_with_tasks_health_and_single_processes(self) -> None:
        processes = {
            "host_count": 1,
            "tray_count": 1,
            "go2rtc_count": 0,
            "all_python_from_embedded_runtime": True,
            "pids": {"host": [101], "tray": [102], "go2rtc": []},
            "status": "checked",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("tools.verify_windows_installation._task_exists", return_value=True),
            patch(
                "tools.verify_windows_installation._health",
                return_value={"status": "passed", "http_status": 200, "url": "loopback"},
            ),
            patch("tools.verify_windows_installation._process_snapshot", return_value=processes),
        ):
            root = Path(directory)
            result = verify_installation(root, root / "Data")
        self.assertEqual(result["status"], "passed")

    def test_duplicate_or_missing_formal_process_fails(self) -> None:
        processes = {
            "host_count": 2,
            "tray_count": 0,
            "go2rtc_count": 0,
            "all_python_from_embedded_runtime": False,
            "pids": {"host": [101, 103], "tray": [], "go2rtc": []},
            "status": "checked",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("tools.verify_windows_installation._task_exists", return_value=True),
            patch(
                "tools.verify_windows_installation._health",
                return_value={"status": "passed", "http_status": 200, "url": "loopback"},
            ),
            patch("tools.verify_windows_installation._process_snapshot", return_value=processes),
        ):
            root = Path(directory)
            result = verify_installation(root, root / "Data")
        self.assertEqual(result["status"], "failed")

    def test_current_user_mode_accepts_startup_links_instead_of_tasks(self) -> None:
        processes = {
            "host_count": 1,
            "tray_count": 1,
            "go2rtc_count": 0,
            "all_python_from_embedded_runtime": True,
            "pids": {"host": [201], "tray": [202], "go2rtc": []},
            "status": "checked",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("tools.verify_windows_installation._task_exists", return_value=False),
            patch(
                "tools.verify_windows_installation._health",
                return_value={"status": "passed", "http_status": 200, "url": "loopback"},
            ),
            patch("tools.verify_windows_installation._process_snapshot", return_value=processes),
        ):
            root = Path(directory)
            core_link = root / "Core.lnk"
            tray_link = root / "Tray.lnk"
            core_link.touch()
            tray_link.touch()
            result = verify_installation(
                root,
                root / "Data",
                startup_links=(core_link, tray_link),
            )
        self.assertEqual(result["status"], "passed")
        self.assertTrue(all(result["startup_links"].values()))


if __name__ == "__main__":
    unittest.main()
