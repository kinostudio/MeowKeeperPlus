import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from meowkeeper.core.analytics.system_telemetry import gpu_sample
from tools.cpu_sensor_contract import select_temperature
from tools.prepare_cpu_sensor_dependencies import LIBRARIES
from tools.probe_cpu_sensor import probe


class CpuSensorPrototypeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 17, tzinfo=timezone.utc)

    def payload(self, *sensors):
        return {"schema_version": 1, "provider": "libre_hardware_monitor", "status": "available",
                "observed_at": self.now.isoformat(), "sensors": [
                    {"sensor_name": name, "hardware_name": "測試CPU（非實機）", "sensor_id": f"/cpu/temp/{i}",
                     "temperature_c": value} for i, (name, value) in enumerate(sensors)]}

    def select(self, payload, vendor="intel"):
        return select_temperature(payload, vendor=vendor, now=self.now)

    def test_intel_package_preferred_and_multiple_sources_preserved(self):
        data = self.select(self.payload(("CPU Core #1", 80), ("CPU Package", 62), ("CPU Package", 65)))
        self.assertEqual(data["temperature_c"], 65)
        self.assertEqual(data["sensor_type"], "cpu_package")
        self.assertEqual(len(data["candidates"]), 3)

    def test_single_core_not_misrepresented_as_package(self):
        data = self.select(self.payload(("CPU Core #1", 57)))
        self.assertEqual(data["sensor_type"], "cpu_core")
        self.assertEqual(data["selection_reason"], "hottest_core_not_package")

    def test_amd_tdie_and_tctl_are_not_equivalent(self):
        data = self.select(self.payload(("Core (Tctl)", 90), ("Core (Tdie)", 61)), "amd")
        self.assertEqual(data["temperature_c"], 61)
        self.assertIsNone(self.select(self.payload(("Core (Tctl)", 90)), "amd")["temperature_c"])

    def test_null_nonfinite_unknown_and_acpi_are_unavailable(self):
        for name, value in (("CPU Package", None), ("CPU Package", float("nan")),
                            ("CPU Package", True), ("CPU Package", 999), ("ACPI Thermal Zone", 60)):
            with self.subTest(name=name, value=value):
                result = self.select(self.payload((name, value)))
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["temperature_c"])

    def test_stale_future_naive_and_invalid_payload(self):
        data = self.payload(("CPU Package", 62))
        for age, state in ((16, "stale"), (-10, "error")):
            data["observed_at"] = (self.now - timedelta(seconds=age)).isoformat()
            result = self.select(data)
            self.assertEqual(result["status"], state)
            self.assertIsNone(result["temperature_c"])
        data["observed_at"] = "2026-10-17T00:00:00"
        self.assertEqual(self.select(data)["status"], "error")
        self.assertEqual(self.select(None)["status"], "error")

    def test_dotnet_roundtrip_timestamp_precision(self):
        for stamp in ("2026-10-17T00:00:00.1234567+00:00", "2026-10-17T00:00:00.1234567Z"):
            data = self.payload(("CPU Package", 62))
            data["observed_at"] = stamp
            self.assertEqual(self.select(data)["temperature_c"], 62)

    def test_failure_status_and_reason_whitelist(self):
        for status in ("permission_required", "unsupported", "error", "unavailable"):
            data = {**self.payload(), "status": status, "reason": "driver_missing"}
            self.assertEqual(self.select(data)["status"], status)
            self.assertIsNone(self.select(data)["temperature_c"])
            data["reason"] = "private exception path"
            self.assertNotIn("private", str(self.select(data)))

    def test_helper_timeout_crash_invalid_and_large_output(self):
        for completed in (subprocess.CompletedProcess([], 2, "", "private"),
                          subprocess.CompletedProcess([], 0, "x" * 65537, ""),
                          subprocess.CompletedProcess([], 0, '{"request_id":"wrong"}', "")):
            with patch("tools.probe_cpu_sensor.subprocess.run", return_value=completed), self.assertRaises(ValueError):
                probe(Path("fixture"))
        with (patch("tools.probe_cpu_sensor.subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 10)),
              self.assertRaises(subprocess.TimeoutExpired)):
            probe(Path("fixture"))

    def test_gpu_contract_unchanged_when_cpu_unavailable(self):
        self.assertEqual(self.select(self.payload())["status"], "unavailable")
        with (patch("meowkeeper.core.analytics.system_telemetry.shutil.which", return_value="fixture-nvidia-smi"),
              patch("meowkeeper.core.analytics.system_telemetry.subprocess.run", return_value=
                    subprocess.CompletedProcess([], 0, "測試GPU, 25, 62\n", ""))):
            self.assertEqual(gpu_sample(), {"gpu_name": "測試GPU", "system_gpu_percent": 25.0, "gpu_temperature_c": 62.0})

    def test_gpu_failure_does_not_change_cpu_contract(self):
        with patch("meowkeeper.core.analytics.system_telemetry.shutil.which", return_value=None):
            self.assertEqual(gpu_sample(), {})
            self.assertEqual(self.select(self.payload(("CPU Package", 62)))["temperature_c"], 62)

    def test_no_production_imports_or_gpu_changes(self):
        root = Path(__file__).resolve().parents[1]
        program = (root / "tools/cpu_sensor_helper/Program.cs").read_text(encoding="utf-8")
        self.assertLess(program.index("if (!DriverPresent())"), program.index('GetMethod("Open")'))
        self.assertLess(program.index("handle.IsInvalid"), program.index('GetMethod("Open")'))
        for forbidden in ("Process.Start", "Install(", "IsGpuEnabled", "TcpListener", "HttpListener"):
            self.assertNotIn(forbidden, program)
        telemetry = (root / "src/meowkeeper/core/analytics/system_telemetry.py").read_text(encoding="utf-8")
        self.assertIn('"--query-gpu=name,utilization.gpu,temperature.gpu"', telemetry)
        self.assertNotIn("cpu_sensor_contract", telemetry)

    def test_dependency_whitelist_has_no_gui_driver_or_installer(self):
        self.assertIn("BlackSharp.Core.dll", LIBRARIES)
        self.assertTrue(all(name.endswith(".dll") and "/" not in name and "\\" not in name for name in LIBRARIES))
        for name in ("PawnIO_setup.exe", "Aga.Controls.dll", "OxyPlot.dll", "OxyPlot.WindowsForms.dll"):
            self.assertNotIn(name, LIBRARIES)
