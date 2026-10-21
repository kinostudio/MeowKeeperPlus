from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from meowkeeper.app.web import CoreStateFacade, create_app
from meowkeeper.runtime import bundled_binary
from meowkeeper.storage.database import SQLiteStore

TOKEN = "isolated-test-token-only"


class ReviewPlaybackHelpTests(unittest.TestCase):
    def test_copied_event_video_is_found_when_database_has_old_computer_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            events = root / "events"
            events.mkdir()
            event_id = str(uuid4())
            copied_clip = events / f"event_{event_id}.mp4"
            copied_clip.write_bytes(b"copied event video")
            store = SQLiteStore(root / "meowkeeper.db")
            try:
                store.create_media_event(
                    event_id=event_id,
                    event_type="cat_detected",
                    trigger_at=datetime.now(timezone.utc),
                    source_id="test-source",
                    track_id=None,
                    pet_id=None,
                    confidence=0.8,
                    requested_pre_seconds=5,
                    requested_post_seconds=10,
                )
                store.update_media_event(
                    event_id,
                    clip_status="completed",
                    clip_path=rf"C:\\Users\\old-pc\\Desktop\\MeowKeeperPlus\\data\\events\\event_{event_id}.mp4",
                )
                core = CoreStateFacade(store=store, event_directory=events)
                listed = core.list_events(limit=10, offset=0)
                self.assertTrue(listed[0]["clip_exists"])
                self.assertEqual(listed[0]["clip_location"], "relocated_by_event_id")
                self.assertEqual(core.event_file(event_id), copied_clip.resolve())
            finally:
                store.close()

    def test_event_clip_fallback_never_serves_an_unrelated_external_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            events = root / "events"
            events.mkdir()
            core = CoreStateFacade(event_directory=events)
            self.assertEqual(
                core._resolve_event_clip({"event_id": "../outside", "clip_path": str(root / "outside.mp4")}),
                (None, "missing"),
            )

    def test_development_asset_changes_invalidate_browser_cache_without_a_release(self):
        from meowkeeper.app.web.release_info import release_information

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            static = root / "src" / "meowkeeper" / "app" / "web" / "static"
            static.mkdir(parents=True)
            style = static / "style.css"
            style.write_text("body{color:red}", encoding="utf-8")
            before = release_information(root)
            style.write_text("body{color:blue}", encoding="utf-8")
            after = release_information(root)
            self.assertEqual(after["version"], "開發版")
            self.assertNotEqual(before["cache_key"], after["cache_key"])

    def test_event_list_and_review_use_playable_video_before_feedback(self):
        root = Path(__file__).resolve().parents[1] / "src" / "meowkeeper" / "app" / "web"
        event_list = (root / "static" / "js" / "event-list.js").read_text(encoding="utf-8")
        event_review = (root / "static" / "js" / "event-review.js").read_text(encoding="utf-8")
        with TestClient(create_app(token=TOKEN)) as client:
            response = client.get("/events", headers={"Authorization": "Bearer " + TOKEN})
            self.assertEqual(response.status_code, 200)
            template = response.text
        self.assertIn('"playback=true"', event_list)
        self.assertIn("native=true", event_list)
        self.assertIn('id="event-playback-dialog"', template)
        self.assertIn('showModal()', event_list)
        self.assertIn("!videoReady", event_review)
        self.assertIn('addEventListener("canplay"', event_review)
        self.assertIn("native=true", event_review)
        self.assertIn('id="event-review-timeline"', template)
        self.assertEqual(template.count('id="event-review-range-start"'), 1)
        self.assertEqual(template.count('id="event-review-range-end"'), 1)
        self.assertIn('id="event-review-video-retry"', template)
        self.assertEqual(template.count('id="event-review-video-retry"'), 1)
        self.assertIn('id="event-review-segment-start"', template)
        self.assertIn('id="event-review-candidates"', template)

    def test_real_hevc_fixture_converts_to_valid_h264_without_changing_original(self):
        from meowkeeper.core.media.media_validation import validate_media

        ffmpeg = bundled_binary("ffmpeg")
        if not ffmpeg or not bundled_binary("ffprobe"):
            self.skipTest("需要內附 FFmpeg／FFprobe")
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "hevc.mp4"
            encoded = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "testsrc2=size=320x180:rate=10", "-t", "1", "-c:v", "libx265",
                 "-x265-params", "pools=1:frame-threads=1:log-level=error", "-y", str(original)],
                capture_output=True, timeout=30, shell=False, check=False,
            )
            self.assertEqual(encoded.returncode, 0, "合成 HEVC 素材建立失敗")
            before = original.read_bytes()
            core = CoreStateFacade(event_directory=folder)
            with patch.object(core, "event_file", return_value=original), \
                    TestClient(create_app(facade=core, token=TOKEN)) as client:
                response = client.get("/api/v1/events/test/video?playback=true",
                                      headers={"Authorization": "Bearer " + TOKEN})
            self.assertEqual(response.status_code, 200)
            output = Path(folder) / "browser.mp4"
            output.write_bytes(response.content)
            validation = validate_media(output)
            self.assertTrue(validation.telegram_compatible)
            self.assertEqual(validation.video_codec, "h264")
            self.assertEqual(original.read_bytes(), before)

    def test_playback_auth_inline_and_original_download(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "original.mp4"
            original.write_bytes(b"original")
            core = CoreStateFacade(event_directory=folder)
            with patch.object(core, "event_file", return_value=original):
                app = create_app(facade=core, token=TOKEN)
                with TestClient(app) as client:
                    url = "/api/v1/events/test/video"
                    self.assertEqual(client.get(url + "?playback=true").status_code, 401)
                    headers = {"Authorization": "Bearer " + TOKEN}
                    with patch("meowkeeper.core.media.media_validation.validate_media",
                               side_effect=AssertionError("native playback must not probe or transcode")):
                        native = client.get(url + "?native=true", headers=headers)
                    self.assertEqual(native.content, b"original")
                    self.assertIn("inline", native.headers["content-disposition"])
                    self.assertEqual(native.headers["x-meowkeeper-playback"], "original")
                    with patch("meowkeeper.core.media.media_validation.validate_media",
                               return_value=SimpleNamespace(valid=True, telegram_compatible=True)):
                        result = client.get(url + "?playback=true", headers=headers)
                    self.assertEqual(result.content, b"original")
                    self.assertIn("inline", result.headers["content-disposition"])
                    self.assertIn("attachment", client.get(url, headers=headers).headers["content-disposition"])

    def test_incompatible_playback_is_cached_and_reused_without_retranscoding(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "original.mp4"
            original.write_bytes(b"original-hevc")
            core = CoreStateFacade(event_directory=folder)
            targets = []
            conversions = []

            def convert(_self, source, target):
                self.assertEqual(source, original)
                conversions.append(source)
                target.write_bytes(b"compatible-h264")
                targets.append(target)
                return SimpleNamespace(path=target)

            with patch.object(core, "event_file", return_value=original), patch(
                "meowkeeper.core.media.media_validation.validate_media",
                return_value=SimpleNamespace(valid=True, telegram_compatible=False)
            ), patch("meowkeeper.core.media.clip_assembler.ClipAssembler.transcode_for_telegram", convert), \
                    TestClient(create_app(facade=core, token=TOKEN)) as client:
                response = client.get("/api/v1/events/test/video?playback=true",
                                      headers={"Authorization": "Bearer " + TOKEN})
                self.assertEqual(response.content, b"compatible-h264")
                self.assertEqual(response.headers["x-meowkeeper-playback"], "prepared")
                cached = client.get("/api/v1/events/test/video?playback=true",
                                    headers={"Authorization": "Bearer " + TOKEN})
                self.assertEqual(cached.content, b"compatible-h264")
                self.assertEqual(cached.headers["x-meowkeeper-playback"], "local-cache")
            self.assertEqual(len(conversions), 1)
            self.assertEqual(original.read_bytes(), b"original-hevc")
            self.assertFalse(targets[0].exists())
            self.assertTrue((Path(folder).parent / "cache" / "review-playback").is_dir())

    def test_help_link_and_navigation_icons(self):
        with TestClient(create_app(token=TOKEN)) as client:
            headers = {"Authorization": "Bearer " + TOKEN}
            self.assertIn(client.get("/help", follow_redirects=False).status_code, (307, 401))
            response = client.get("/help", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertIn('href="/help">使用教學', response.text)
            self.assertIn("人在外面用手機看家", response.text)
            self.assertIn("Apple Watch", response.text)
            self.assertIn("守護日常", response.text)
            self.assertIn("管理 · 設定", response.text)
            self.assertIn("系統 · 維護", response.text)
            self.assertGreaterEqual(response.text.count('viewBox="0 0 24 24"'), 12)


if __name__ == "__main__":
    unittest.main()
