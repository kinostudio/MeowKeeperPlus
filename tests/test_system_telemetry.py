import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meowkeeper.core.analytics.resource_history import ResourceHistory
from meowkeeper.core.analytics.system_telemetry import format_system_report


class SystemTelemetryTests(unittest.TestCase):
    def test_raw_peak_missing_sensor_and_counter_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            history = ResourceHistory(Path(directory) / "history.db")
            for stamp, cpu, read in ((100, 10, 1000), (160, 90, 2000), (220, 20, 50), (280, 30, 150)):
                history.append({"system_cpu_percent": cpu, "disk_read_bytes": read}, now=stamp)
            summary = history.hour_summary(now=290)
            self.assertEqual(summary["statistics"]["system_cpu_percent"], {"current": 30, "average": 37.5, "peak": 90})
            self.assertEqual(summary["disk_io"]["read_bytes"], 1100)
            self.assertIsNone(summary["disk_io"]["write_bytes"])
            self.assertIsNone(summary["statistics"]["cpu_temperature_c"]["peak"])
            self.assertIsNone(history.hour_summary(now=410)["statistics"]["system_cpu_percent"]["current"])

    def test_migration_from_v1_preserves_data_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE resource_samples (sampled_at REAL PRIMARY KEY, system_cpu_percent REAL, disk_sampled_at REAL)")
                db.execute("INSERT INTO resource_samples VALUES (100, 15, NULL)")
                db.execute("PRAGMA user_version=1")
            db.close()
            ResourceHistory(path)
            history = ResourceHistory(path)
            self.assertEqual(history.hour_summary(now=110)["statistics"]["system_cpu_percent"]["peak"], 15)
            history.append({}, now=120)

    def test_report_has_hardware_and_explicit_unavailable(self):
        with patch("meowkeeper.core.analytics.system_telemetry.hardware", return_value={
            "computer_name": "test-pc", "cpu_model": "Intel test", "logical_cores": 8,
            "ram_bytes": 16 * 1024**3, "gpu_name": "test-gpu",
        }):
            text = format_system_report({})
            for expected in ("test-pc", "Intel test", "17.2 GB", "test-gpu", "未提供", "磁碟 I/O", "部分資料"):
                self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
