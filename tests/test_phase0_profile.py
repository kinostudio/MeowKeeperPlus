from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from meowkeeper.domain import MX250_MINIMUM
from tools.ffmpeg_probe import collect_report as collect_ffmpeg_report
from tools.probe_common import ProbeItem, ProbeReport, ProbeStatus
from tools.run_phase0 import collect_phase0_report
from tools.system_probe import assess_gpu, assess_memory


class HardwareProfileAssessmentTests(unittest.TestCase):
    def test_eight_gib_meets_minimum(self) -> None:
        item = assess_memory(8.0, MX250_MINIMUM)
        self.assertEqual(item.status, ProbeStatus.OK)

    def test_below_eight_gib_warns(self) -> None:
        item = assess_memory(7.99, MX250_MINIMUM)
        self.assertEqual(item.status, ProbeStatus.WARNING)

    def test_sixteen_gib_does_not_change_profile(self) -> None:
        item = assess_memory(16.0, MX250_MINIMUM)
        self.assertEqual(item.status, ProbeStatus.OK)
        self.assertEqual(MX250_MINIMUM.analysis_fps, 5)
        self.assertEqual(MX250_MINIMUM.model_batch_size, 1)

    def test_mx250_with_two_gib_meets_resource_floor(self) -> None:
        item = assess_gpu(
            {"name": "NVIDIA GeForce MX250", "memory_total_mib": 2048},
            MX250_MINIMUM,
        )
        self.assertEqual(item.status, ProbeStatus.OK)

    def test_non_reference_gpu_is_not_performance_proof(self) -> None:
        item = assess_gpu(
            {"name": "NVIDIA GeForce RTX 2060", "memory_total_mib": 6144},
            MX250_MINIMUM,
        )
        self.assertEqual(item.status, ProbeStatus.WARNING)
        self.assertFalse(item.details["reference_gpu_match"])

    @patch("tools.ffmpeg_probe.bundled_binary", return_value=None)
    @patch("tools.ffmpeg_probe.shutil.which", return_value=None)
    def test_missing_ffmpeg_is_reported_without_crash(
        self, _mock_which: object, _mock_bundled: object
    ) -> None:
        report = collect_ffmpeg_report()
        self.assertTrue(all(item.status == ProbeStatus.NOT_CHECKED for item in report.items))


class AggregatePhaseZeroTests(unittest.TestCase):
    def _report(self, name: str, status: ProbeStatus) -> ProbeReport:
        return ProbeReport(name, [ProbeItem(name, status, "測試")])

    @patch("tools.run_phase0.collect_telegram_report")
    @patch("tools.run_phase0.collect_camera_report")
    @patch("tools.run_phase0.collect_ffmpeg_report")
    @patch("tools.run_phase0.collect_system_report")
    def test_aggregate_never_requests_telegram_send(
        self,
        system_report: object,
        ffmpeg_report: object,
        camera_report: object,
        telegram_report: object,
    ) -> None:
        system_report.return_value = self._report("system", ProbeStatus.OK)
        ffmpeg_report.return_value = self._report("ffmpeg", ProbeStatus.NOT_CHECKED)
        camera_report.return_value = self._report("camera", ProbeStatus.NOT_CHECKED)
        telegram_report.return_value = self._report("telegram", ProbeStatus.NOT_CHECKED)

        payload = collect_phase0_report(telegram_token="test-token")

        telegram_report.assert_called_once_with("test-token", send_test=False)
        self.assertEqual(payload["overall_status"], ProbeStatus.NOT_CHECKED.value)
        self.assertNotIn("test-token", str(payload))


if __name__ == "__main__":
    unittest.main()
