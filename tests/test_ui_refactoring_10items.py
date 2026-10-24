import sys
sys.path.insert(0, r"C:\Users\user\Desktop\MeowKeeperPlus-Workspace\src")

import unittest
import tempfile
import sqlite3
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from meowkeeper.app.web.preview_rtc import validate_offer
from meowkeeper.integrations.xiaomi.miss_privacy import MissPrivacyAdapter
from meowkeeper.core.analytics.system_telemetry import gpu_sample


class TestUiRefactoring10Items(unittest.TestCase):
    def test_validate_offer_allows_audio(self):
        sdp_with_audio = (
            "v=0\r\n"
            "o=- 0 0 IN IP4 127.0.0.1\r\n"
            "s=-\r\n"
            "t=0 0\r\n"
            "a=recvonly\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 97\r\n"
        )
        validated = validate_offer(sdp_with_audio)
        self.assertIn("m=audio", validated)
        self.assertIn("m=video", validated)

    def test_miss_privacy_show_powers_on_external_standby(self):
        power_mock = MagicMock(return_value={"success": True, "on": True})
        privacy = MissPrivacyAdapter(
            Path(tempfile.mkdtemp()) / "privacy.json",
            device=lambda: "dev1",
            read=lambda dev: {"x": 50, "y": 50},
            move=lambda dev, **kwargs: {"success": True},
            power=power_mock,
        )
        result = privacy.show()
        self.assertTrue(result.get("success"))
        power_mock.assert_called_with("dev1", True)

    def test_dashboard_js_fullscreen_null_guard(self):
        root = Path(__file__).resolve().parents[1]
        dashboard_js = (root / "src/meowkeeper/app/web/static/js/dashboard.js").read_text(encoding="utf-8")
        self.assertNotIn('byId("overview-fullscreen").disabled=false;', dashboard_js)
        self.assertIn('const fsBtn = byId("overview-fullscreen");', dashboard_js)

    def test_gpu_sample_optimus_p8_spike_filtering(self):
        # When Optimus dGPU is waking from P8 idle with clock=139MHz, 99% load is filtered to 0%
        mock_output = "NVIDIA GeForce MX250, 99, 55, P8, 139\n"
        with (patch("meowkeeper.core.analytics.system_telemetry.shutil.which", return_value="fixture-nvidia-smi"),
              patch("meowkeeper.core.analytics.system_telemetry.subprocess.run", return_value=
                    subprocess.CompletedProcess([], 0, mock_output, ""))):
            sample = gpu_sample()
            self.assertEqual(sample["system_gpu_percent"], 0.0)
            self.assertEqual(sample["gpu_temperature_c"], 55.0)
            self.assertEqual(sample["gpu_name"], "NVIDIA GeForce MX250")


if __name__ == "__main__":
    unittest.main()
