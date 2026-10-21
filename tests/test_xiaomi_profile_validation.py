from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.integrations.xiaomi.device_discovery import Go2RtcXiaomiDiscovery


class ActiveProfileValidationTests(unittest.TestCase):
    def test_metadata_and_real_jpeg_are_both_required(self) -> None:
        stream = io.BytesIO()
        Image.new("RGB", (320, 180)).save(stream, "JPEG")
        probe = SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [{
            "width": 1920, "height": 1080, "codec_name": "hevc", "avg_frame_rate": "20/1",
        }]}).encode())
        frame = SimpleNamespace(returncode=0, stdout=stream.getvalue())
        bridge = Go2RtcXiaomiDiscovery(Go2RtcClient(), rtsp_port=28554)
        with patch("meowkeeper.integrations.xiaomi.device_discovery.bundled_binary", side_effect=["ffprobe", "ffmpeg"]), \
                patch("meowkeeper.integrations.xiaomi.device_discovery.subprocess.run", side_effect=[probe, frame]) as run:
            result = bridge.verify_active_profile("camera_1", "subtype=3")
        self.assertTrue(result.stable)
        self.assertEqual((result.width, result.height, result.fps), (1920, 1080, 20))
        for call in run.call_args_list:
            self.assertIn("rtsp://127.0.0.1:28554/camera_1", call.args[0])
            self.assertFalse(call.kwargs["shell"])
            self.assertLessEqual(call.kwargs["timeout"], 12)

    def test_invalid_jpeg_cannot_be_success_and_stderr_is_not_exposed(self) -> None:
        probe = SimpleNamespace(returncode=0, stdout=b'{"streams":[{"width":640,"height":360}]}')
        frame = SimpleNamespace(returncode=0, stdout=b"not a JPEG", stderr=b"private source")
        bridge = Go2RtcXiaomiDiscovery(Go2RtcClient())
        with patch("meowkeeper.integrations.xiaomi.device_discovery.bundled_binary", return_value="binary"), \
                patch("meowkeeper.integrations.xiaomi.device_discovery.subprocess.run", side_effect=[probe, frame]), \
                self.assertRaises(RuntimeError) as error:
            bridge.verify_active_profile("camera_1", "subtype=1")
        self.assertNotIn("private", str(error.exception))

    def test_invalid_stream_name_does_not_start_process(self) -> None:
        bridge = Go2RtcXiaomiDiscovery(Go2RtcClient())
        with patch("meowkeeper.integrations.xiaomi.device_discovery.bundled_binary", return_value="binary"), \
                patch("meowkeeper.integrations.xiaomi.device_discovery.subprocess.run") as run, \
                self.assertRaises(ValueError):
            bridge.verify_active_profile("../secret?token=x", "subtype=1")
        run.assert_not_called()
