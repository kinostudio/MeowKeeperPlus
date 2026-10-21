from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from meowkeeper.app.cpu_temperature_setup import CpuTemperatureSetup
from meowkeeper.app.web import create_app


class CpuTemperatureSetupTests(unittest.TestCase):
    def package(self, root):
        files = []
        for name in ("Manage-CpuTemperature.ps1", "Manage-CpuSensorTask.ps1"):
            path = root / "scripts" / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"# isolated fixture only\n")
            files.append({"path": "scripts/" + name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        (root / "build_manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")

    def test_status_never_launches_and_changed_script_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.package(root)
            setup = CpuTemperatureSetup(root)
            with patch.object(setup, "_interactive_windows", return_value=True), patch("subprocess.run") as run:
                self.assertTrue(setup.status()["launcher_available"])
                run.assert_not_called()
                (root / "scripts/Manage-CpuTemperature.ps1").write_text("changed", encoding="utf-8")
                self.assertFalse(setup.status()["launcher_available"])
                with self.assertRaises(ValueError):
                    setup.launch()
                run.assert_not_called()

    def test_launch_is_explicit_interactive_deduplicated_and_not_sensor_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.package(root)
            setup = CpuTemperatureSetup(root)
            with patch.object(setup, "_interactive_windows", return_value=False), self.assertRaises(ValueError):
                setup.launch()
            with patch.object(setup, "_interactive_windows", return_value=True), patch("threading.Thread") as thread:
                self.assertEqual(setup.launch()["state"], "running")
                self.assertEqual(setup.launch()["state"], "running")
                thread.assert_called_once()
            with patch.dict("os.environ", {"SystemRoot": "C:/Windows"}), patch("subprocess.run", return_value=Mock(returncode=0)) as run:
                setup._run(root / "scripts/Manage-CpuTemperature.ps1")
                self.assertEqual(setup._state, "wizard_finished")
                self.assertFalse(run.call_args.kwargs["shell"])
                self.assertEqual(run.call_args.kwargs["timeout"], 420)
                self.assertNotIn("-Consent", run.call_args.args[0])

    def test_api_auth_csrf_confirmation_remote_and_real_page_link(self):
        token = "c" * 32
        headers = {"Authorization": "Bearer " + token, "X-MeowKeeper-CSRF": hashlib.sha256((token + ":csrf").encode()).hexdigest()}
        url = "/api/v1/telemetry/cpu-temperature/setup"
        with patch.object(CpuTemperatureSetup, "launch", return_value={"state": "running"}) as launch:
            with TestClient(create_app(token=token)) as client:
                self.assertEqual(client.post(url, json={"confirm": True}).status_code, 401)
                self.assertEqual(client.post(url, headers={"Authorization": "Bearer " + token}, json={"confirm": True}).status_code, 403)
                self.assertEqual(client.post(url, headers=headers, json={}).status_code, 428)
                self.assertEqual(client.get(url, headers=headers).status_code, 200)
                launch.assert_not_called()
                self.assertEqual(client.post(url, headers=headers, json={"confirm": True}).status_code, 200)
                launch.assert_called_once()
                page = client.get("/analytics", headers=headers)
                self.assertIn('id="cpu-temperature-setup"', page.text)
                self.assertIn("cpu-temperature-setup.js", page.text)
                self.assertIn("Enable-CPU-Temperature.cmd", page.text)
                monitor = client.get("/static/js/monitor-ui.js").text
                self.assertIn("/analytics#cpu-temperature-setup", monitor)
            with TestClient(create_app(token=token), client=("100.64.0.2", 12345)) as client:
                remote_headers = {**headers, "X-Forwarded-For": "127.0.0.1"}
                self.assertFalse(client.get(url, headers=remote_headers).json()["local_request"])
                self.assertEqual(client.post(url, headers=remote_headers, json={"confirm": True}).status_code, 403)
                launch.assert_called_once()
