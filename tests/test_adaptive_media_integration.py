from __future__ import annotations

import hashlib
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from meowkeeper.app.web.api import create_app
from meowkeeper.core.detection.frame_analysis import FrameAnalysis
from meowkeeper.core.inference.process_worker import InferenceWorkerError, IsolatedInferenceWorker
from meowkeeper.core.media import DiskSegmentRingBuffer, FfmpegMediaIngest
from meowkeeper.core.media.latest_frame import LatestFrameReader
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.runtime import bundled_binary


def _blocked_inference_worker(connection, role, config):
    del role, config
    connection.send(("ready", "CPU", "cpu"))
    try:
        while True:
            message = connection.recv()
            if message[0] == "close":
                return
            time.sleep(5)
            connection.send(("ok", FrameAnalysis(False, None, None, "測試"), "CPU", "cpu"))
    except (EOFError, OSError):
        return
    finally:
        connection.close()


class AdaptiveMediaIntegrationTests(unittest.TestCase):
    def test_timed_out_ai_child_does_not_block_single_ffmpeg_ingest(self):
        ffmpeg = bundled_binary("ffmpeg")
        if not ffmpeg:
            self.skipTest("本地 FFmpeg 不可用。")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "blocked-ai.mp4"
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=640x360:rate=5", "-t", "8",
                            "-c:v", "libx264", "-preset", "ultrafast", "-g", "5", str(source)],
                           capture_output=True, timeout=20, check=True, shell=False)
            ring = DiskSegmentRingBuffer(root / "ring", retention_seconds=60)
            ingest = FfmpegMediaIngest(str(source), ring, ffmpeg_path=ffmpeg)
            received = []
            drained = threading.Event()

            def frames():
                try:
                    yield from ingest.iter_frames()
                finally:
                    drained.set()

            reader = LatestFrameReader(frames(), on_frame=received.append, close_source=ingest.close)
            worker = IsolatedInferenceWorker(
                "detect", {}, startup_timeout=5, inference_timeout=0.2,
                worker_target=_blocked_inference_worker,
            )
            try:
                first = next(iter(reader))
                self.assertTrue(first.jpeg_bytes.startswith(b"\xff\xd8"))
                with self.assertRaisesRegex(InferenceWorkerError, "逾時"):
                    worker.analyze(FramePacket(first.sequence, first.captured_at,
                                               "shared-frame-only", first.jpeg_bytes))
                self.assertTrue(drained.wait(15), "AI 子程序失效時，單一 FFmpeg ingest 應繼續排空來源。")
                self.assertGreaterEqual(len(received), 35)
                self.assertGreaterEqual(ring.status().duration_seconds, 7)
                self.assertFalse(worker.alive)
                self.assertEqual(ingest.restart_count, 0)
            finally:
                worker.close()
                reader.close()
                ingest.close()
            self.assertFalse(reader._thread.is_alive())

    def test_real_ffmpeg_drains_while_ai_consumer_waits(self):
        ffmpeg = bundled_binary("ffmpeg")
        if not ffmpeg:
            self.skipTest("本地 FFmpeg 不可用。")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "generated.mp4"
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=640x360:rate=5", "-t", "8",
                            "-c:v", "libx264", "-preset", "ultrafast", "-g", "5", str(source)],
                           capture_output=True, timeout=20, check=True, shell=False)
            ring = DiskSegmentRingBuffer(root / "ring", retention_seconds=60)
            ingest = FfmpegMediaIngest(str(source), ring, ffmpeg_path=ffmpeg)
            received = []
            drained = threading.Event()

            def frames():
                try:
                    yield from ingest.iter_frames()
                finally:
                    drained.set()

            reader = LatestFrameReader(frames(), on_frame=received.append, close_source=ingest.close)
            try:
                iterator = iter(reader)
                first = next(iterator)
                self.assertTrue(first.jpeg_bytes.startswith(b"\xff\xd8"))
                # Do not ask for another AI frame until all real media is drained.
                self.assertTrue(drained.wait(15), "AI 停等時媒體 reader 未完成。")
                self.assertGreaterEqual(len(received), 35)
                self.assertGreater(reader.dropped, 0)
                self.assertGreaterEqual(ring.status().duration_seconds, 7)
                self.assertGreaterEqual(ring.status().segment_count, 7)
                self.assertIs(next(iterator), received[-1])
                self.assertIsNone(ingest._process)
            finally:
                reader.close()
            self.assertFalse(reader._thread.is_alive())

    def test_retry_requires_authentication_and_csrf(self):
        token = "a" * 32
        with TestClient(create_app(token=token)) as client:
            path = "/api/v1/inference/retry"
            self.assertEqual(client.post(path).status_code, 401)
            headers = {"Authorization": "Bearer " + token}
            self.assertEqual(client.post(path, headers=headers).status_code, 403)
            headers["X-MeowKeeper-CSRF"] = hashlib.sha256((token + ":csrf").encode()).hexdigest()
            response = client.post(path, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["success"])
