"""Identical source registration must not orphan the active RTSP consumer."""
import unittest
from unittest.mock import patch

from meowkeeper.integrations.go2rtc.client import Go2RtcClient


class StreamRegistrationTests(unittest.TestCase):
    def test_identical_source_is_not_put_again(self):
        source = "xiaomi://fixture:sg@192.0.2.1?model=test&subtype=1"
        with patch.object(Go2RtcClient, "request_json", return_value={
            "camera": {"producers": [{"url": source}], "consumers": [{}]}
        }) as request:
            self.assertEqual(Go2RtcClient().put_stream("camera", source), {"status": "unchanged"})
        request.assert_called_once_with("/api/streams")

    def test_different_profile_requires_real_put(self):
        with patch.object(Go2RtcClient, "request_json", side_effect=[
            {"camera": {"producers": [{"url": "xiaomi://fixture?subtype=1"}]}}, ""
        ]) as request:
            Go2RtcClient().put_stream("camera", "xiaomi://fixture?subtype=2")
        self.assertEqual(request.call_args.kwargs["method"], "PUT")

    def test_missing_stream_is_registered(self):
        with patch.object(Go2RtcClient, "request_json", side_effect=[{}, ""]) as request:
            Go2RtcClient().put_stream("camera", "xiaomi://fixture")
        self.assertEqual(request.call_count, 2)
