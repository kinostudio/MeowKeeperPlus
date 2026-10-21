import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from meowkeeper.app.web.api import create_app
from meowkeeper.app.web.state import CoreStateFacade
from meowkeeper.core.analytics.resource_history import (
    ResourceHistory,
    ResourceRecorder,
    directory_bytes,
)
from meowkeeper.storage.database import SQLiteStore


class ResourceHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.history = ResourceHistory(self.root / "resources.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_retention_nulls_and_restart(self):
        self.history.append({"system_cpu_percent": 0}, now=1)
        self.history.append({"system_cpu_percent": 12, "app_cpu_percent": None,
                             "url": "private-token-never-save"}, now=10000000)
        reopened = ResourceHistory(self.history.path)
        result = reopened.query(24, now=10000001)
        self.assertEqual(len(result["points"]), 1)
        self.assertIsNone(result["points"][0]["app_cpu_percent"])
        self.assertNotIn("private-token", json.dumps(result))
        with self.history.connect() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM resource_samples").fetchone()[0], 1)

    def test_bounded_query_and_validation(self):
        for i in range(1000):
            self.history.append({"system_cpu_percent": i % 100}, now=10000000 + i * 60)
        result = self.history.query(2160, now=10060000)
        self.assertLessEqual(len(result["points"]), 601)
        with self.assertRaises(ValueError):
            self.history.query(0)

    def test_disk_exclusions_and_no_double_count(self):
        app = self.root / "app"
        data = app / "data"
        data.mkdir(parents=True)
        (app / "model.bin").write_bytes(b"abc")
        (data / "event.mp4").write_bytes(b"12345")
        (app / "dist").mkdir()
        (app / "dist" / "old.zip").write_bytes(b"x" * 10)
        recorder = ResourceRecorder(self.history, Mock(), app, data)
        recorder._scan_disk()
        self.assertEqual(recorder.disk["app_disk_bytes"], 3)
        self.assertEqual(recorder.disk["data_disk_bytes"], 5)
        self.assertEqual(recorder.disk["total_disk_bytes"], 8)
        stop = threading.Event()
        stop.set()
        with self.assertRaises(InterruptedError):
            directory_bytes(app, excluded=set(), stop=stop)

    def test_sampling_memory_and_disk_scan_throttling(self):
        facade = Mock()
        facade._collect_application_resources.return_value = {"cpu_percent": 5, "rss_bytes": 50, "process_count": 3}
        recorder = ResourceRecorder(self.history, facade, self.root, self.root)
        with patch("psutil.virtual_memory", return_value=Mock(total=1000, percent=40)), \
             patch("psutil.cpu_percent", return_value=30), patch.object(recorder, "_scan_disk") as scan:
            recorder.sample()
            recorder.disk_thread.join()
            recorder.sample()
        self.assertEqual(scan.call_count, 1)
        latest = self.history.query(24)["latest"]
        self.assertEqual(latest["app_ram_percent"], 5)
        self.assertEqual(latest["system_cpu_percent"], 30)
        self.assertIsNone(latest["total_disk_bytes"])
        recorder.stop()

    def test_database_lock_is_bounded(self):
        with self.history.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):
                self.history.append({})

    def test_authenticated_api_and_lifecycle(self):
        store = SQLiteStore(self.root / "events.db")
        store.initialize()
        facade = CoreStateFacade(store=store)
        # This test verifies auth/storage/lifecycle, not Windows process inventory.
        # WMI/psutil enumeration can outlive the bounded telemetry shutdown wait.
        facade._collect_application_resources = Mock(return_value={"rss_bytes": 0, "process_count": 1})
        app = create_app(facade=facade, token="a" * 32)
        with TestClient(app) as client:
            recorder = app.state.resource_recorder
            self.assertTrue(recorder.thread.is_alive())
            self.assertEqual(client.get("/api/v1/analytics/resources").status_code, 401)
            recorder.history.append({"system_cpu_percent": 22, "system_ram_percent": 48,
                                     "system_gpu_percent": 17, "cpu_temperature_c": 52,
                                     "gpu_temperature_c": 61})
            headers = {"Authorization": "Bearer " + "a" * 32}
            response = client.get("/api/v1/analytics/resources", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["latest"]["system_cpu_percent"], 22)
            self.assertEqual(response.json()["latest"]["system_gpu_percent"], 17)
            self.assertEqual(response.json()["latest"]["cpu_temperature_c"], 52)
            self.assertEqual(response.json()["latest"]["gpu_temperature_c"], 61)
            self.assertEqual(client.get("/api/v1/analytics/resources?hours=-1", headers=headers).status_code, 422)
        self.assertFalse(recorder.thread.is_alive())
        store.close()


if __name__ == "__main__":
    unittest.main()
