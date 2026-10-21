from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.camera_probe import probe_rtsp, probe_tcp
from tools.probe_common import ProbeStatus, redact_rtsp_uri, sanitize_text
from tools.telegram_probe import probe_bot, send_test_message


class ProbeSafetyTests(unittest.TestCase):
    def test_rtsp_uri_redacts_userinfo(self) -> None:
        test_uri = (
            "rtsp://" + "test-user" + ":" + "test-password" + "@192.0.2.10:554/live?channel=1"
        )
        safe = redact_rtsp_uri(test_uri)
        expected = "rtsp://" + "***:***@" + "192.0.2.10:554/live?channel=1"
        self.assertEqual(safe, expected)
        self.assertNotIn("test-user", safe)
        self.assertNotIn("test-password", safe)

    def test_malformed_rtsp_port_is_safely_redacted(self) -> None:
        malformed = "rtsp://" + "test-user:test-password@" + "example.invalid:notaport/live"
        self.assertEqual(redact_rtsp_uri(malformed), "rtsp://***")

    def test_sanitize_text_removes_rtsp_password(self) -> None:
        cleaned = sanitize_text("error rtsp://alice:secret@example.invalid/live")
        self.assertNotIn("alice", cleaned)
        self.assertNotIn("secret", cleaned)

    def test_missing_camera_values_are_not_checked(self) -> None:
        self.assertEqual(probe_tcp(None, 554, 0.01).status, ProbeStatus.NOT_CHECKED)
        self.assertEqual(probe_rtsp(None, 0.01).status, ProbeStatus.NOT_CHECKED)

    def test_missing_token_does_not_call_network(self) -> None:
        item, result = probe_bot(None)
        self.assertEqual(item.status, ProbeStatus.NOT_CHECKED)
        self.assertIsNone(result)

    def test_test_message_requires_explicit_flag(self) -> None:
        item = send_test_message("not-a-real-token", "123", enabled=False)
        self.assertEqual(item.status, ProbeStatus.NOT_CHECKED)


if __name__ == "__main__":
    unittest.main()
