import io
import json
import queue
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from meowkeeper.app.web.api import create_app
from meowkeeper.app.web.state import CoreStateFacade
from meowkeeper.core.analytics.cpu_temperature import (
    CpuTemperatureMonitor,
    HelperCpuProvider,
    PsutilCpuProvider,
    unavailable,
)
from meowkeeper.core.analytics.resource_history import ResourceHistory, ResourceRecorder
from meowkeeper.storage.database import SQLiteStore


def value():
    return {**unavailable(), "status": "available", "temperature_c": 62.0,
            "observed_at": datetime.now(timezone.utc).isoformat(), "confidence": "direct_sensor"}


class CpuTemperatureMonitorTests(unittest.TestCase):
    def test_lifecycle_check_does_not_collect_status_metrics(self):
        status = Mock(side_effect=AssertionError("full metrics must not be collected"))
        facade = CoreStateFacade(callbacks={"status": status, "monitoring_active": lambda: True})
        self.assertTrue(facade.monitoring_active())
        status.assert_not_called()

    def test_shared_cache_no_extra_reads_and_stale_hides_temperature(self):
        provider = Mock()
        provider.read.return_value = value()
        monitor = CpuTemperatureMonitor([provider])
        monitor.sample()
        for _ in range(20):
            self.assertEqual(monitor.snapshot()["temperature_c"], 62)
        self.assertEqual(provider.read.call_count, 1)
        monitor.value["observed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=16)).isoformat()
        self.assertEqual(monitor.snapshot()["status"], "stale")
        self.assertIsNone(monitor.snapshot()["temperature_c"])

    def test_backoff_and_monitoring_stop_release_owned_provider(self):
        provider = Mock()
        provider.read.return_value = unavailable("permission_required", "driver_unavailable")
        active = Mock(return_value=True)
        monitor = CpuTemperatureMonitor([provider], active=active)
        monitor.sample()
        monitor.sample()
        self.assertEqual(provider.read.call_count, 1)
        self.assertEqual(monitor.snapshot()["status"], "permission_required")
        active.return_value = False
        monitor.sample()
        self.assertEqual(monitor.snapshot()["error_reason"], "monitoring_paused")
        self.assertGreaterEqual(provider.close.call_count, 2)

    def test_first_trusted_source_wins_and_failures_are_isolated(self):
        first, second = Mock(), Mock()
        first.read.return_value = value()
        monitor = CpuTemperatureMonitor([first, second])
        monitor.sample()
        second.read.assert_not_called()
        first.read.side_effect = RuntimeError("private exception")
        second.read.return_value = unavailable("unsupported", "helper_missing")
        monitor.sample()
        self.assertEqual(monitor.snapshot()["status"], "unsupported")
        self.assertNotIn("private", json.dumps(monitor.snapshot()))
        first.close.side_effect = OSError("private cleanup")
        monitor.stop()  # cleanup failure cannot break Host shutdown

    def test_disabled_and_lifecycle_do_not_spawn(self):
        provider = Mock()
        monitor = CpuTemperatureMonitor([provider], enabled=False, interval=2)
        monitor.start()
        monitor.start()
        monitor.stop()
        self.assertFalse(monitor.thread.is_alive())
        provider.read.assert_not_called()
        for interval in (0, 1, 31, True):
            with self.assertRaises(ValueError):
                CpuTemperatureMonitor([], interval=interval)

    def test_psutil_rejects_acpi_and_tctl_and_uses_named_package(self):
        with patch("psutil.sensors_temperatures", create=True, return_value={
            "acpitz": [SimpleNamespace(label="ACPI", current=33)],
            "coretemp": [SimpleNamespace(label="Core 0", current=55), SimpleNamespace(label="Package id 0", current=62)]}):
            result = PsutilCpuProvider().read()
            self.assertEqual(result["temperature_c"], 62)
            self.assertEqual(result["provider"], "psutil_cpu")
        with patch("psutil.sensors_temperatures", create=True, return_value={
            "k10temp": [SimpleNamespace(label="Tctl", current=90)]}):
            self.assertIsNone(PsutilCpuProvider().read()["temperature_c"])

    def test_helper_missing_and_manifest_mismatch_never_spawn(self):
        with tempfile.TemporaryDirectory() as directory, patch("subprocess.Popen") as spawn:
            folder = Path(directory)
            self.assertEqual(HelperCpuProvider(folder).read()["status"], "unsupported")
            (folder / "manifest.json").write_text('{"schema_version":1,"files":{}}', encoding="utf-8")
            self.assertEqual(HelperCpuProvider(folder).read()["status"], "error")
            spawn.assert_not_called()

    def test_bounded_pipe_and_response_nonce(self):
        provider = HelperCpuProvider(Path("fixture"))
        failed = threading.Event()
        provider._drain(SimpleNamespace(stdout=io.BytesIO(b"x" * 65537)), queue.Queue(maxsize=1), failed)
        self.assertTrue(failed.is_set())
        process = Mock()
        process.poll.return_value = None
        provider.process = process
        provider.lines.put(json.dumps({"request_id": "wrong"}).encode())
        self.assertEqual(provider.read()["error_reason"], "helper_invalid")
        process.stdin.write.assert_any_call(b"stop\n")

    def test_api_auth_and_many_clients_only_read_cached_sensor(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "events.db")
            store.initialize()
            app = create_app(facade=CoreStateFacade(store=store), token="a" * 32)
            provider = Mock()
            provider.read.return_value = value()
            monitor = CpuTemperatureMonitor([provider])
            monitor.sample()
            app.state.resource_recorder.cpu_monitor = monitor
            client = TestClient(app)
            self.assertEqual(client.get("/api/v1/telemetry/cpu-temperature").status_code, 401)
            for _ in range(10):
                response = client.get("/api/v1/telemetry/cpu-temperature", headers={"Authorization": "Bearer " + "a" * 32})
                self.assertEqual(response.json()["temperature_c"], 62)
            self.assertEqual(provider.read.call_count, 1)
            store.close()

    def test_stale_not_written_to_existing_history_and_gpu_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = ResourceHistory(root / "history.db")
            provider = Mock()
            provider.read.return_value = value()
            monitor = CpuTemperatureMonitor([provider])
            monitor.sample()
            monitor.value["observed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
            facade = Mock()
            facade._collect_application_resources.return_value = {}
            recorder = ResourceRecorder(history, facade, root, root, cpu_monitor=monitor)
            recorder.last_disk_attempt = float("inf")
            with patch("meowkeeper.core.analytics.system_telemetry.gpu_sample", return_value={"gpu_temperature_c": 51}):
                recorder.sample()
            latest = history.query(1)["latest"]
            self.assertIsNone(latest["cpu_temperature_c"])
            self.assertEqual(latest["gpu_temperature_c"], 51)
