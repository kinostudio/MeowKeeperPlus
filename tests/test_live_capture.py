"""本機保存沿用既有影格／組片，包含權限與錯誤狀態。"""
import hashlib
import io
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image

from meowkeeper.app.core_controller import RepositoryCoreController
from meowkeeper.app.web.main import create_app
from meowkeeper.core.media import (
    ClipAssembler,
    DiskSegmentRingBuffer,
    EventClipService,
    FfmpegMediaIngest,
    validate_media,
)
from meowkeeper.core.media.event_clip import LiveClipResult


class LiveCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.controller = RepositoryCoreController.__new__(RepositoryCoreController)
        self.controller.event_directory = self.root / "events"
        self.controller._capture_lock = threading.Lock()
        self.controller.retention_seconds = 60
        image = io.BytesIO()
        Image.new("RGB", (80, 60)).save(image, format="JPEG")
        self.jpeg = self.root / "preview.jpg"
        self.jpeg.write_bytes(image.getvalue())
        self.controller.create_preview_snapshot = Mock(return_value=self.jpeg)
        self.controller.create_snapshot = self.controller.create_preview_snapshot
        self.controller._lock = threading.RLock()
        self.controller._metrics = {"snapshot": {"status": "preview_fallback"}}
        self.controller.get_recent_clip = Mock(return_value=LiveClipResult(None, "not_available", None))

    def test_photo_saved_unique_and_preview_preserved(self):
        first = self.controller.save_capture("photo")
        second = self.controller.save_capture("photo")
        self.assertEqual(first["status"], "saved")
        self.assertEqual(Path(first["path"]).parent, self.root / "photo")
        self.assertEqual(Path(first["path"]).read_bytes(), self.jpeg.read_bytes())
        self.assertNotEqual(first["path"], second["path"])
        self.controller.get_recent_clip.assert_not_called()

    def test_video_uses_pre15_post0_and_keeps_original(self):
        clip = self.root / "events" / "live.mp4"
        clip.parent.mkdir()
        clip.write_bytes(b"assembly-output")
        self.controller.get_recent_clip.return_value = LiveClipResult(clip, "completed", 15.6)
        result = self.controller.save_capture("video")
        self.assertEqual(result["status"], "saved")
        self.assertEqual(Path(result["path"]).parent, self.root / "video")
        self.assertEqual(Path(result["path"]).read_bytes(), clip.read_bytes())
        self.controller.get_recent_clip.assert_called_once_with(15.0, 0.0)
        self.assertEqual(result["actual_duration_seconds"], 15.6)

    def test_native_photo_and_1080p_preview_fallback_notice(self):
        image = io.BytesIO()
        Image.new("RGB", (3840, 2160)).save(image, format="JPEG")
        native = self.root / "native.jpg"
        native.write_bytes(image.getvalue())
        self.controller.create_snapshot = Mock(return_value=native)
        self.controller._metrics["snapshot"] = {"status": "native"}
        result = self.controller.save_capture("photo")
        self.assertIn("3840×2160", result["message_zh_tw"])
        with Image.open(result["path"]) as saved:
            self.assertEqual(saved.size, (3840, 2160))
        image = io.BytesIO()
        Image.new("RGB", (1920, 1080)).save(image, format="JPEG")
        self.jpeg.write_bytes(image.getvalue())
        self.controller.create_snapshot.return_value = self.jpeg
        self.controller._metrics["snapshot"] = {"status": "preview_fallback"}
        result = self.controller.save_capture("photo")
        self.assertIn("已使用 1080p 預覽截圖", result["message_zh_tw"])

    def test_unavailable_and_invalid_kind(self):
        self.controller.create_preview_snapshot.return_value = None
        self.assertEqual(self.controller.save_capture("photo")["status"], "not_available")
        self.assertEqual(self.controller.save_capture("video")["status"], "not_available")
        self.controller.retention_seconds = 10
        self.assertIn("15 秒", self.controller.save_capture("video")["message_zh_tw"])
        with self.assertRaises(ValueError):
            self.controller.save_capture("arbitrary-path")

    def test_busy_and_disk_low_do_not_assemble(self):
        self.controller._capture_lock.acquire()
        self.assertEqual(self.controller.save_capture("video")["status"], "busy")
        self.controller._capture_lock.release()
        with patch("meowkeeper.app.core_controller.shutil.disk_usage", return_value=Mock(free=0)):
            self.assertEqual(self.controller.save_capture("video")["status"], "not_available")
        self.controller.get_recent_clip.assert_not_called()

    def test_api_auth_csrf_validation_and_callback(self):
        token = "c" * 32
        app = create_app(token=token)
        app.state.core._callbacks["save_capture"] = self.controller.save_capture
        headers = {"Authorization": "Bearer " + token}
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/v1/live/save", json={"kind": "photo"}).status_code, 401)
            self.assertEqual(client.post("/api/v1/live/save", headers=headers, json={"kind": "photo"}).status_code, 403)
            headers["X-MeowKeeper-CSRF"] = hashlib.sha256((token + ":csrf").encode()).hexdigest()
            self.assertEqual(client.post("/api/v1/live/save", headers=headers, json={"kind": "bad"}).status_code, 400)
            response = client.post("/api/v1/live/save", headers=headers, json={"kind": "photo"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(Path(response.json()["path"]).is_file())

    def test_real_local_video_saved_from_compressed_ring(self):
        ffmpeg = Path(__file__).resolve().parents[1] / "vendor/ffmpeg/bin/ffmpeg.exe"
        source = self.root / "source.mp4"
        generated = subprocess.run([
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
            "-t", "18", "-c:v", "libx264", "-g", "10", "-sc_threshold", "0",
            "-pix_fmt", "yuv420p", str(source),
        ], capture_output=True, timeout=60, shell=False, check=False)
        self.assertEqual(generated.returncode, 0)
        ring = DiskSegmentRingBuffer(self.root / "ring", retention_seconds=60)
        ingest = FfmpegMediaIngest(str(source), ring, fps=5, width=320, prefer_stream_copy=True)
        self.assertGreater(len(list(ingest.iter_frames(max_frames=90))), 20)
        ring.scan()
        # Local ingest runs faster than wall time; anchor at its last media segment.
        service = EventClipService(
            ring, ClipAssembler(), output_directory=self.root / "events", telegram_enabled=False,
            clock=lambda: max(item.end_at for item in ring.index.all()),
        )
        try:
            self.controller.get_recent_clip = service.get_recent_clip
            result = self.controller.save_capture("video")
        finally:
            service.close()
        self.assertEqual(result["status"], "saved")
        self.assertTrue(validate_media(Path(result["path"])).valid)
        self.assertGreater(result["actual_duration_seconds"], 12)
        self.assertLess(result["actual_duration_seconds"], 18)
