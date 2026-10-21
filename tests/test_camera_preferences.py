from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from meowkeeper.app.web import CoreStateFacade, create_app
from meowkeeper.integrations.xiaomi.camera_preferences import CameraPreferences
from meowkeeper.integrations.xiaomi.setup_service import XiaomiSetupService


class CameraPreferencesTests(unittest.TestCase):
    def test_verified_dimensions_survive_restart_and_do_not_follow_new_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            store = CameraPreferences(path)
            store.remember("a", "subtype=3", "camera_a", metadata={
                "width": 3840, "height": 2160, "fps": 20, "video_codec": "hevc", "secret": "ignored"})
            restored = CameraPreferences(path).get("a")
            self.assertEqual(restored["metadata"]["width"], 3840)
            self.assertIn("verified_at", restored["metadata"])
            self.assertNotIn("ignored", path.read_text(encoding="utf-8"))
            store.remember("a", "subtype=3", "camera_a")
            self.assertEqual(store.get("a")["metadata"]["height"], 2160)
            store.remember("a", "subtype=1", "camera_a")
            self.assertNotIn("metadata", store.get("a"))

    def test_preview_status_exposes_saved_original_source_after_restart(self):
        token = "t" * 32
        core = CoreStateFacade()
        with patch.object(core, "preview_status", return_value={
            "source_is_xiaomi": True, "stream_name": "camera_a", "source_status": "running",
        }):
            integration = SimpleNamespace(devices=lambda: [{
                "selected": True,
                "stream_name": "camera_a",
                "quality": {
                    "name": "subtype=3", "width": 3840, "height": 2160, "fps": 20,
                    "video_codec": "hevc", "verified_at": "2026-10-20T00:00:00+00:00",
                    "historical": True,
                },
            }])
            with TestClient(create_app(facade=core, token=token, integrations=integration)) as client:
                response = client.get("/api/v1/preview/status", headers={"Authorization": "Bearer " + token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["original_source"], {
            "width": 3840, "height": 2160, "fps": 20, "video_codec": "hevc",
            "verified_at": "2026-10-20T00:00:00+00:00", "historical": True, "name": "subtype=3",
        })

    def test_concurrent_activation_and_profile_are_rejected_without_source_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = XiaomiSetupService(directory, state_path=Path(directory) / "state.json")
            service._camera_control_lock.acquire()
            try:
                self.assertEqual(service.activate("a")["action_status"], "busy")
                self.assertEqual(service.set_live_profile("a", "subtype=3")["status"], "busy")
                self.assertEqual(service.activate_remote("a")["status"], "busy")
                self.assertEqual(service._profiles, {})
            finally:
                service._camera_control_lock.release()

    def test_independent_cameras_survive_restart_without_private_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            first = CameraPreferences(path)
            first.remember("opaque-camera-a", "subtype=3", "camera_a")
            first.remember("opaque-camera-b", "subtype=1", "camera_b")
            second = CameraPreferences(path)
            self.assertEqual(second.get("opaque-camera-a")["profile"], "subtype=3")
            self.assertEqual(second.get("opaque-camera-b")["stream_name"], "camera_b")
            self.assertNotIn("opaque-camera", path.read_text(encoding="utf-8"))
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_invalid_values_do_not_overwrite_previous_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            store = CameraPreferences(path)
            store.remember("a", "subtype=1", "camera_a")
            original = path.read_bytes()
            for profile, stream in (("invalid", "a"), ("subtype=1", "xiaomi://private")):
                with self.assertRaises(ValueError):
                    store.remember("a", profile, stream)
                self.assertEqual(path.read_bytes(), original)

    def test_corrupt_preferences_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            path.write_text("not JSON", encoding="utf-8")
            self.assertIsNone(CameraPreferences(path).get("a"))
