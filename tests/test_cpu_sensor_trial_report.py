import unittest

from tools.report_cpu_sensor_trial import summarize


class CpuSensorTrialReportTests(unittest.TestCase):
    def fixture(self, *, status="available"):
        return {"request_id": "a" * 32, "finished_at": "2026-10-17T00:00:01.1234567+00:00",
                "elapsed_ms": 1000, "cpu_ms": 20, "peak_working_set_bytes": 25000000,
                "samples": [{"schema_version": 1, "provider": "libre_hardware_monitor", "status": status,
                             "observed_at": "2026-10-17T00:00:00.1234567+00:00",
                             "sensors": [{"hardware_name": "測試CPU", "sensor_name": "CPU Package",
                                          "sensor_id": "/cpu/temperature/1", "temperature_c": 60}]}]}

    def test_historical_trial_not_live_or_long_term_budget_proof(self):
        result = summarize(self.fixture(), "a" * 32, vendor="intel")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["temperatures_c"], [60])
        self.assertFalse(result["live_reading"])
        self.assertFalse(result["host_integrated"])
        self.assertIsNone(result["steady_state_budget_passed"])

    def test_missing_and_stale_samples_cannot_pass(self):
        report = self.fixture(status="permission_required")
        self.assertEqual(summarize(report, "a" * 32, vendor="intel")["status"], "failed")
        report = self.fixture()
        report["finished_at"] = "2026-10-17T00:01:00+00:00"
        self.assertEqual(summarize(report, "a" * 32, vendor="intel")["status"], "failed")

    def test_identity_vendor_and_performance_guard(self):
        with self.assertRaises(ValueError):
            summarize(self.fixture(), "b" * 32, vendor="intel")
        with self.assertRaises(ValueError):
            summarize(self.fixture(), "a" * 32, vendor="unknown")
        for value in (0, float("nan"), -1, True):
            report = self.fixture()
            report["elapsed_ms"] = value
            with self.assertRaises(ValueError):
                summarize(report, "a" * 32, vendor="intel")
