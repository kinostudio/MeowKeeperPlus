from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from tools.run_host import _wait_for_source_frame


class SourceFrameGateTests(unittest.TestCase):
    def test_valid_current_jpeg_required_and_wrong_source_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.jpg"
            start = time.time() - 1
            Image.new("RGB", (32, 24), "gray").save(path, format="JPEG")
            controller = SimpleNamespace(source_url="rtsp://127.0.0.1:8554/a", latest_jpeg_path=path,
                                         get_status=lambda: {"status": "running"})
            self.assertTrue(_wait_for_source_frame(controller, controller.source_url, start))
            self.assertFalse(_wait_for_source_frame(controller, "rtsp://127.0.0.1:8554/b", start))

    def test_stale_jpeg_and_started_thread_do_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.jpg"
            Image.new("RGB", (32, 24), "gray").save(path, format="JPEG")
            controller = SimpleNamespace(source_url="rtsp://127.0.0.1:8554/a", latest_jpeg_path=path,
                                         get_status=lambda: {"status": "starting"})
            with patch("tools.run_host.time.monotonic", side_effect=[0, 1, 3]), \
                    patch("tools.run_host.time.sleep"):
                self.assertFalse(_wait_for_source_frame(controller, controller.source_url,
                                                       path.stat().st_mtime + 1, timeout=2))
            controller.latest_jpeg_path = None
            controller.get_status = lambda: {"status": "error"}
            self.assertFalse(_wait_for_source_frame(controller, controller.source_url, 0))
