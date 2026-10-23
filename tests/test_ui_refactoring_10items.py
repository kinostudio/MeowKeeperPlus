import sys
sys.path.insert(0, r"C:\Users\user\Desktop\MeowKeeperPlus-Workspace\src")

import unittest
import tempfile
import sqlite3
import json
from pathlib import Path
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from meowkeeper.app.web.preview_rtc import validate_offer
from meowkeeper.integrations.xiaomi.miss_privacy import MissPrivacyAdapter


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


if __name__ == "__main__":
    unittest.main()
