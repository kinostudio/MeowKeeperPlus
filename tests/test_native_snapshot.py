from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from meowkeeper.app.core_controller import RepositoryCoreController
from meowkeeper.app.web.state import CoreStateFacade
from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.integrations.xiaomi.setup_service import XiaomiSetupService
from meowkeeper.storage.database import SQLiteStore


def _jpeg(width: int = 1920, height: int = 1080) -> bytes:
    # Minimal SOF0 marker sufficient for the controller's bounded dimension
    # parser; the go2rtc client additionally requires the JPEG EOI marker.
    return (
        b"\xff\xd8\xff\xc0\x00\x0b\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00\xff\xd9"
    )


class _Response:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.headers = {"Content-Type": "image/jpeg"}

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self, _limit: int = -1) -> bytes:
        return self.payload


class NativeSnapshotTests(unittest.TestCase):
    def test_real_ring_snapshot_preserves_4k_and_releases_reference(self) -> None:
        from meowkeeper.core.media.native_snapshot import snapshot_from_ring
        from meowkeeper.core.media.ring_buffer import DiskSegmentRingBuffer
        from meowkeeper.runtime import bundled_binary
        with tempfile.TemporaryDirectory() as directory:
            ring = DiskSegmentRingBuffer(directory)
            segment = Path(directory) / "frame.ts"
            result = subprocess.run([
                bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=tan:size=3840x2160:rate=1",
                "-frames:v", "1", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2",
                "-pix_fmt", "yuv420p", "-f", "mpegts", str(segment),
            ], capture_output=True, shell=False, timeout=30, check=False)
            self.assertEqual(result.returncode, 0)
            ring.register_file(segment, captured_at=datetime.now(timezone.utc))
            jpeg = snapshot_from_ring(ring)
            self.assertEqual(RepositoryCoreController._jpeg_dimensions(jpeg), (3840, 2160))
            self.assertTrue(all(s.reference_count == 0 for s in ring.index.all()))

    def test_ring_decode_failure_releases_reference(self) -> None:
        from meowkeeper.core.media.native_snapshot import snapshot_from_ring
        from meowkeeper.core.media.ring_buffer import DiskSegmentRingBuffer
        with tempfile.TemporaryDirectory() as directory:
            ring = DiskSegmentRingBuffer(directory)
            segment = Path(directory) / "bad.ts"
            segment.write_bytes(b"invalid-video")
            ring.register_file(segment, captured_at=datetime.now(timezone.utc))
            self.assertIsNone(snapshot_from_ring(ring))
            self.assertTrue(all(s.reference_count == 0 for s in ring.index.all()))

    def test_go2rtc_frame_jpeg_returns_bounded_valid_bytes(self) -> None:
        payload = _jpeg()
        client = Go2RtcClient()
        with patch("urllib.request.urlopen", return_value=_Response(payload)) as open_url:
            actual = client.get_frame_jpeg("xiaomi_camera_1")
        self.assertEqual(actual, payload)
        request = open_url.call_args.args[0]
        self.assertIn("/api/frame.jpeg?src=xiaomi_camera_1", request.full_url)
        self.assertNotIn("xiaomi://", request.full_url)

    def test_controller_explicit_snapshot_uses_native_provider_and_preview_stays_separate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-native-snapshot-") as directory:
            root = Path(directory)
            store = SQLiteStore(root / "events.sqlite3")
            try:
                controller = RepositoryCoreController(
                    store=store,
                    source_url="rtsp://127.0.0.1:8554/xiaomi_camera_1",
                    preview_directory=root / "preview",
                    native_snapshot_provider=lambda: _jpeg(2560, 1440),
                )
                preview = root / "preview" / "latest.jpg"
                preview.parent.mkdir(parents=True)
                preview.write_bytes(_jpeg(640, 360))
                controller._latest_jpeg = preview
                explicit = controller.create_snapshot()
                self.assertIsNotNone(explicit)
                assert explicit is not None
                self.assertEqual(controller._jpeg_dimensions(explicit.read_bytes()), (2560, 1440))

                facade = CoreStateFacade(callbacks=controller.callbacks())
                self.assertEqual(facade.request_snapshot(high_quality=False), preview.resolve())
                self.assertEqual(facade.request_snapshot(high_quality=True), explicit.resolve())
            finally:
                store.close()

    def test_xiaomi_snapshot_provider_uses_only_selected_local_stream(self) -> None:
        payload = _jpeg(1280, 720)

        class _Client:
            def get_frame_jpeg(self, stream_name: str, *, timeout_seconds: float) -> bytes:
                self.last_call = (stream_name, timeout_seconds)
                return payload

        client = _Client()
        go2rtc = type("Go2Rtc", (), {"rtsp_port": 8554})()
        with tempfile.TemporaryDirectory(prefix="meowkeeper-xiaomi-snapshot-") as directory:
            service = XiaomiSetupService(
                directory,
                go2rtc=go2rtc,
                active_source_url=lambda: "rtsp://127.0.0.1:8554/xiaomi_camera_1",
                ptz_client=client,
            )
            service._state["selected_stream_name"] = "xiaomi_camera_1"
            self.assertEqual(service.native_snapshot_jpeg(), payload)
            self.assertEqual(client.last_call[0], "xiaomi_camera_1")

            service._state["selected_stream_name"] = "other_stream"
            self.assertIsNone(service.native_snapshot_jpeg())


if __name__ == "__main__":
    unittest.main()
