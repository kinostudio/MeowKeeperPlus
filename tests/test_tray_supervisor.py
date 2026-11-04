import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from meowkeeper.app.tray_supervisor import TraySupervisor


class TraySupervisorTests(unittest.TestCase):
    def test_portable_tray_uses_the_same_host_port(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "pythonw.exe").touch()
            guard = TraySupervisor(root, root, Mock(), port=18981)
            with patch("meowkeeper.app.tray_supervisor.sys.executable", str(root / "python.exe")), patch.object(guard, "_interactive", return_value=True), patch("meowkeeper.app.tray_supervisor.subprocess.Popen") as launch:
                guard._launch()
            self.assertEqual(launch.call_args.args[0][-2:], ["--base-url", "http://127.0.0.1:18981"])
            self.assertFalse(launch.call_args.kwargs["shell"])

    def test_installed_tray_reads_configured_port_and_rejects_invalid_data(self):
        from meowkeeper.tray.main import _configured_base_url

        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            with patch("meowkeeper.tray.main.default_dashboard_config_path", return_value=config):
                config.write_text('{"dashboard":{"port":18981}}', encoding="utf-8")
                self.assertEqual(_configured_base_url(), "http://127.0.0.1:18981")
                config.write_text('{"dashboard":{"port":0}}', encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    _configured_base_url()

    def test_existing_tray_never_relaunches(self):
        guard = TraySupervisor(Path.cwd(), Path.cwd(), Mock())
        guard._lock.acquire()
        with patch.object(guard, "_present", return_value=True), patch.object(guard, "_launch") as launch:
            guard._check()
        launch.assert_not_called()
        self.assertEqual(guard.state["status"], "running")
        guard.close()
        self.assertEqual(guard.ensure()["status"], "closed")

    def test_missing_tray_requires_observed_process(self):
        guard = TraySupervisor(Path.cwd(), Path.cwd(), Mock())
        guard._lock.acquire()
        with patch.object(guard, "_present", side_effect=[False, True]), patch.object(guard, "_launch") as launch:
            guard._check()
        launch.assert_called_once()
        self.assertEqual(guard.state["status"], "running")

    def test_task_accepted_without_process_is_unconfirmed(self):
        guard = TraySupervisor(Path.cwd(), Path.cwd(), Mock())
        guard._lock.acquire()
        with patch.object(guard, "_present", return_value=False), patch.object(guard, "_launch"), patch("meowkeeper.app.tray_supervisor.time.sleep"):
            guard._check()
        self.assertEqual(guard.state["status"], "unconfirmed")
        self.assertTrue(guard._lock.acquire(blocking=False))
        guard._lock.release()

    def test_start_and_restart_both_check_tray(self):
        from meowkeeper.app.core_controller import RepositoryCoreController
        from meowkeeper.storage.database import SQLiteStore

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = SQLiteStore(root / "test.db")
            callback = Mock(return_value={"status": "checking"})
            core = RepositoryCoreController(store=store, tray_ensure=callback,
                                            ring_directory=root / "ring", event_directory=root / "events",
                                            preview_directory=root / "preview")
            try:
                core.start_monitor()
                core.restart_monitor(confirm=True)
                self.assertEqual(callback.call_count, 2)
                callback.side_effect = OSError("fixture")
                core.start_monitor()
                core.close()
                callback.reset_mock()
                core.start_monitor()
                callback.assert_not_called()
            finally:
                core.close()
                store.close()
