import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from meowkeeper.app.web.main import create_app
from meowkeeper.tray.client import TrayApiError
from meowkeeper.tray.main import MENU_LABELS, run_pystray, status_summary
from meowkeeper.tray.shutdown import close_owned_processes, owned_process


class TrayShutdownTests(unittest.TestCase):
    def test_pid_reuse_aborts_before_termination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = Mock(pid=98765)
            executable = str(root / "vendor/ffmpeg/bin/ffmpeg.exe")
            process.as_dict.return_value = {"exe": executable, "cmdline": []}
            process.children.return_value = []
            process.create_time.side_effect = [10.0, 11.0]
            with patch("psutil.process_iter", return_value=[process]), self.assertRaises(TrayApiError):
                close_owned_processes(root)
            process.terminate.assert_not_called()

    def test_only_owned_process_terminated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = Mock(pid=98765)
            executable = str(root / "vendor/ffmpeg/bin/ffmpeg.exe")
            owned.as_dict.return_value = {"exe": executable, "cmdline": []}
            owned.exe.return_value = executable
            owned.cmdline.return_value = []
            owned.children.return_value = []
            owned.create_time.return_value = 10.0
            other = Mock(pid=98766)
            other.as_dict.return_value = {"exe": str(root.parent / "ffmpeg.exe"), "cmdline": []}
            with patch("psutil.process_iter", return_value=[owned, other]), patch("psutil.wait_procs", return_value=([owned], [])):
                self.assertEqual(close_owned_processes(root)["status"], "closed")
            owned.terminate.assert_called_once()
            other.terminate.assert_not_called()

    def test_exact_scope_not_generic_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = str(root / "runtime/python/python.exe")
            self.assertTrue(owned_process({"exe": python, "cmdline": [python, str(root / "tools/run_host.py")]}, root))
            self.assertFalse(owned_process({"exe": python, "cmdline": [python, "other_project.py"]}, root))
            self.assertFalse(owned_process({"exe": str(root.parent / "ffmpeg.exe"), "cmdline": []}, root))
            self.assertTrue(owned_process({"exe": str(root / "vendor/ffmpeg/bin/ffmpeg.exe"), "cmdline": []}, root))
            self.assertIn("完整關閉喵喵守護者+", MENU_LABELS)

    def test_shutdown_api_is_authenticated_confirmed_and_csrf_protected(self):
        token = "s" * 32
        app = create_app(token=token)
        callback = Mock(return_value={"status": "closing"})
        app.state.host_shutdown = callback
        headers = {"Authorization": "Bearer " + token}
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/v1/host/shutdown", json={"confirm": True}).status_code, 401)
            self.assertEqual(client.post("/api/v1/host/shutdown", headers=headers, json={"confirm": True}).status_code, 403)
            headers["X-MeowKeeper-CSRF"] = hashlib.sha256((token + ":csrf").encode()).hexdigest()
            self.assertEqual(client.post("/api/v1/host/shutdown", headers=headers, json={}).status_code, 400)
            self.assertEqual(client.post("/api/v1/host/shutdown", headers=headers, json={"confirm": True}).status_code, 200)
        callback.assert_called_once()

    def test_menu_operation_does_not_block_windows_message_loop(self):
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        client = Mock()
        client.health.return_value = {"status": "ok"}
        client.status.return_value = {"status": "running"}
        def slow_start():
            entered.set()
            release.wait(timeout=2)
            completed.set()
        client.start.side_effect = slow_start
        class Menu:
            SEPARATOR = object()
            def __init__(self, *items):
                self.items = items
        icon = Mock()
        def create_icon(_name, _image, _title, menu):
            def run():
                callback = menu.items[5][1]
                before = time.monotonic()
                callback(icon, None)
                self.assertLess(time.monotonic() - before, 0.1)
                self.assertTrue(entered.wait(1))
                callback(icon, None)  # bounded single action, no second worker
                self.assertEqual(client.start.call_count, 1)
                release.set()
                self.assertTrue(completed.wait(1))
            icon.run.side_effect = run
            return icon
        backend = SimpleNamespace(Menu=Menu, MenuItem=lambda label, callback: (label, callback), Icon=create_icon)
        with patch.dict("sys.modules", {"pystray": backend}), patch("meowkeeper.tray.main._load_image", return_value=None), patch("meowkeeper.tray.main._message_box"):
            self.assertEqual(run_pystray(client, notify=False), 0)
        icon.update_menu.assert_not_called()
        messages = [call.args[0] for call in icon.notify.call_args_list]
        self.assertTrue(any("正在啟動監控" in message for message in messages))
        self.assertTrue(any("正在處理上一項" in message for message in messages))

    def test_status_summary_excludes_secrets_and_distinguishes_health(self):
        result = status_summary({"health": {"status": "ok"}, "core": {
            "status": "running", "url": "secret-url", "metrics": {
                "source": {"status": "reconnecting", "url": "secret-url"},
                "ai": {"backend": "CPU"}, "telegram": {"status": "disabled"},
            }}})
        self.assertIn("重新連線中", result)
        self.assertIn("未啟用", result)
        self.assertIn("CPU", result)
        self.assertNotIn("secret-url", result)
