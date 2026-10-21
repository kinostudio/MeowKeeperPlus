"""The portable Tray should bring up its own bundled Host, never a system Python."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from meowkeeper.tray.main import MENU_LABELS, _active_data_root_for_action, ensure_host


class TrayCoreBindingTests(unittest.TestCase):
    def test_no_standalone_tray_exit_menu(self) -> None:
        self.assertFalse(any("結束 Tray" in label for label in MENU_LABELS))

    def test_portable_tray_starts_bundled_host_when_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module_file = root / "src" / "meowkeeper" / "tray" / "main.py"
            (root / "runtime" / "python").mkdir(parents=True)
            (root / "runtime" / "python" / "python.exe").touch()
            (root / "build_manifest.json").touch()
            client = Mock()
            client.wait_for_health.side_effect = [False, True]
            with (
                patch("meowkeeper.tray.main.__file__", str(module_file)),
                patch("meowkeeper.tray.main._is_system_install", return_value=False),
                patch("meowkeeper.tray.main.subprocess.Popen") as launch,
            ):
                self.assertTrue(ensure_host(client, interactive=False))
            command = launch.call_args.args[0]
            self.assertEqual(command[0], str(root / "runtime" / "python" / "python.exe"))
            self.assertEqual(command[1], str(root / "tools" / "run_host.py"))
            self.assertNotIn(".venv", " ".join(command))

    def test_tray_data_actions_follow_authenticated_host_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install_root = Path(temporary) / "MeowKeeperPlus"
            app = install_root / "App"
            app.mkdir(parents=True)
            (app / "build_manifest.json").write_text("{}", encoding="utf-8")
            client = Mock()
            client.status.return_value = {"metrics": {"disk": {"path": str(app / "data" / "events")}}}

            self.assertEqual(_active_data_root_for_action(client, app), app / "data")
            client.status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
