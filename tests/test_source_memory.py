from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from meowkeeper.app.source_config import SourceConfig, SourceConfigService


class SourceMemoryTests(unittest.TestCase):
    def test_type_round_trip_survives_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"unrelated":{"keep":true}}', encoding="utf-8")
            service = SourceConfigService(path)
            youtube = SourceConfig("youtube", "https://youtu.be/public-test", 1080, True, False)
            rtsp = SourceConfig("rtsp", "rtsp://192.0.2.1:8554/camera")
            video = SourceConfig("video", r"C:\test\cat.mp4")
            for source in (youtube, rtsp, video):
                service.save(source)
                self.assertEqual(SourceConfigService(path).config(), source)
            saved = SourceConfigService(path).snapshot()["remembered_sources"]
            self.assertEqual(saved["youtube"]["url"], youtube.url)
            self.assertEqual(saved["youtube"]["max_height"], 1080)
            self.assertEqual(saved["rtsp"]["url"], rtsp.url)
            self.assertEqual(saved["video"]["url"], video.url)
            service.save(SourceConfig())
            self.assertEqual(service.config().url, "")
            self.assertEqual(len(service.snapshot()["remembered_sources"]), 3)
            self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["unrelated"]["keep"])

    def test_invalid_legacy_secret_url_never_returned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"media": {
                "source_type": "mock", "rtsp": {"url": "rtsp://user:secret@192.0.2.1/live"},
                "youtube": {"url": "https://example.invalid/signed?token=secret"},
            }}), encoding="utf-8")
            snapshot = SourceConfigService(path).snapshot()
            self.assertEqual(snapshot["remembered_sources"], {})
            self.assertNotIn("secret", json.dumps(snapshot))

    def test_invalid_update_does_not_destroy_saved_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            service = SourceConfigService(path)
            source = SourceConfig("rtsp", "rtsp://192.0.2.1:8554/live")
            service.save(source)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                service.save(SourceConfig("youtube", "https://example.invalid/private"))
            self.assertEqual(path.read_bytes(), before)
