import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from meowkeeper.app.core_controller import RepositoryCoreController
from meowkeeper.app.web import CoreStateFacade, create_app
from meowkeeper.core.media.ring_buffer import DiskSegmentRingBuffer
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.storage.database import SQLiteStore


class DashboardRepairRegressions(unittest.TestCase):
    def test_bitrate_uses_compressed_segments_and_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            ring = DiskSegmentRingBuffer(directory)
            now = datetime.now(timezone.utc)
            self.assertIsNone(ring.estimated_bitrate(now=now))
            for index in range(3):
                path = Path(directory) / f"segment{index}.ts"
                path.write_bytes(b"x" * 1000)
                ring.index.register(path, start_at=now-timedelta(seconds=3-index))
            result = ring.estimated_bitrate(now=now)
            self.assertEqual(result["bps"], 8000)
            self.assertEqual(result["sample_seconds"], 3)
            self.assertIsNone(ring.estimated_bitrate(now=now+timedelta(seconds=10)))
            with self.assertRaises(ValueError):
                ring.estimated_bitrate(now=now, window_seconds=float("inf"))

    @unittest.skipUnless(os.name == "nt", "僅 Windows 可驗證原生檔案分享鎖")
    def test_windows_reader_lock_then_release_recovers_preview(self):
        import ctypes
        from ctypes import wintypes

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = SQLiteStore(":memory:")
        self.addCleanup(store.close)
        root = Path(temporary.name)
        controller = RepositoryCoreController(store=store, preview_directory=root)
        self.addCleanup(controller.close)
        target = root / "latest.jpg"
        target.write_bytes(b"original")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        # Read sharing, deliberately no FILE_SHARE_DELETE: models a slow file response.
        handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        frame = FramePacket(1, datetime.now(timezone.utc), "test", b"updated")
        try:
            with patch.object(controller, "_log") as log:
                controller._save_latest_jpeg(frame)
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(log.call_count, 1)
            self.assertEqual(list(root.glob("*.tmp.jpg")), [])
        finally:
            kernel.CloseHandle(handle)
        controller._save_latest_jpeg(frame)
        self.assertEqual(target.read_bytes(), b"updated")

    def test_nonempty_events_route_uses_actual_review_schema(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = temporary.name
        if directory:
            store = SQLiteStore(Path(directory) / "events.db")
            self.addCleanup(store.close)
            store.create_media_event(
                event_id="test-cat", event_type="cat_detected",
                trigger_at=datetime.now(timezone.utc), source_id="test",
                track_id=None, pet_id=None, confidence=.8,
                requested_pre_seconds=5, requested_post_seconds=10,
            )
            facade = CoreStateFacade(store=store, event_directory=Path(directory))
            self.assertFalse(store.has_event_review("test-cat"))
            with TestClient(create_app(token="t" * 32, facade=facade)) as client:
                self.assertEqual(client.get("/api/v1/events").status_code, 401)
                response = client.get("/api/v1/events", headers={"Authorization": "Bearer " + "t" * 32})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("test-cat", response.text)
                self.assertIn('"has_human_review":false', response.text)

    def test_preview_locked_target_preserved_and_retry_is_bounded(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = temporary.name
        if directory:
            store = SQLiteStore(":memory:")
            controller = RepositoryCoreController(store=store, preview_directory=Path(directory))
            self.addCleanup(store.close)
            self.addCleanup(controller.close)
            target = Path(directory) / "latest.jpg"
            target.write_bytes(b"old-frame")
            frame = FramePacket(1, datetime.now(timezone.utc), "test", b"new-frame")
            with patch.object(Path, "replace", side_effect=PermissionError(13, "locked")) as replace, \
                    patch("meowkeeper.app.core_controller.time.sleep"), patch.object(controller, "_log") as log:
                controller._save_latest_jpeg(frame)
                controller._save_latest_jpeg(frame)
                self.assertEqual(replace.call_count, 6)
                self.assertEqual(log.call_count, 1)
            self.assertEqual(target.read_bytes(), b"old-frame")
            self.assertEqual(list(Path(directory).glob("*.tmp.jpg")), [])
            self.assertIsNone(controller.latest_jpeg_path)
            controller._save_latest_jpeg(frame)
            self.assertEqual(target.read_bytes(), b"new-frame")
            self.assertEqual(controller.latest_jpeg_path, target)
            self.assertEqual(controller.get_status()["metrics"]["source"]["preview_saved_at"], frame.captured_at.isoformat())
