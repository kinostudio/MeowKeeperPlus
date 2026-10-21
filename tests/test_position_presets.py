from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from meowkeeper.app.web import create_app
from meowkeeper.core.notifications.position_presets import PositionPresetService


class PositionPresetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "positions.json"
        self.device = "test-camera-one"
        self.pose = {"angle": 50, "elevation": 60}
        self.adapter = SimpleNamespace(device=lambda: self.device, read=lambda _: self.response(),
                                       move=Mock(side_effect=self.move))
        self.service = PositionPresetService(self.adapter, self.path)

    def response(self):
        return {"success": True, "state": {"connected": True, "position": {
            **self.pose, "ret": 0, "updated_at": datetime.now(timezone.utc).isoformat()}}}

    def move(self, device, **pose):
        self.assertEqual(device, self.device)
        self.pose = pose
        return {"success": True}

    def test_two_slots_cycle_persist_and_are_camera_scoped(self):
        self.assertEqual(self.service.save_next()["slot"], 1)
        self.pose = {"angle": 20, "elevation": 30}
        self.assertEqual(self.service.save_next()["slot"], 2)
        self.pose = {"angle": 70, "elevation": 80}
        result = self.service.save_next()
        self.assertEqual(result["slot"], 1)
        self.assertTrue(result["overwritten"])
        self.service = PositionPresetService(self.adapter, self.path)
        self.assertTrue(self.service.go_to(2)["success"])
        self.assertEqual(self.pose, {"angle": 20, "elevation": 30})
        self.device = "test-camera-two"
        with self.assertRaises(ValueError):
            self.service.go_to(1)
        self.assertEqual(self.service.save_next()["slot"], 1)
        self.assertNotIn("test-camera", self.path.read_text(encoding="utf-8"))

    def test_unverified_read_never_saves_or_moves(self):
        self.adapter.read = lambda _: {"success": True, "state": {"connected": True}}
        with self.assertRaises(ValueError):
            self.service.save_next()
        self.assertFalse(self.path.exists())
        self.adapter.move.assert_not_called()

    def test_mismatched_position_cannot_report_success(self):
        self.service.save_next()
        self.pose = {"angle": 10, "elevation": 10}
        self.adapter.move = Mock(return_value={"success": True})
        with patch("meowkeeper.core.notifications.position_presets.time.monotonic", side_effect=[0, 100]), \
                self.assertRaises(ValueError):
            self.service.go_to(1)

    def test_corrupt_file_not_overwritten(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service.save_next()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_current_zero_endstop_does_not_block_legal_saved_target(self):
        self.service.save_next()
        self.pose = {"angle": 48, "elevation": 0}
        result = self.service.go_to(1)
        self.assertTrue(result["success"])
        self.assertEqual(self.pose, {"angle": 50, "elevation": 60})

    def test_zero_target_never_saved_or_silently_clamped(self):
        self.service.save_next()
        original = self.path.read_bytes()
        self.pose = {"angle": 48, "elevation": 0}
        with self.assertRaisesRegex(ValueError, "邊界"):
            self.service.save_next()
        self.assertEqual(self.path.read_bytes(), original)
        self.adapter.move.assert_not_called()

    def test_unsupported_relay_reports_specific_reason_without_move(self):
        self.service.save_next()
        self.adapter.read = lambda _: {"success": False, "status": "unsupported"}
        with self.assertRaisesRegex(ValueError, "遠端中繼未支援"):
            self.service.go_to(1)
        self.adapter.move.assert_not_called()

    def test_cached_matching_coordinate_is_not_position_confirmation(self):
        self.service.save_next()
        cached = self.response()
        self.adapter.read = lambda _: cached
        with patch("meowkeeper.core.notifications.position_presets.time.monotonic", side_effect=[0, 100]), \
                self.assertRaisesRegex(ValueError, "新鮮"):
            self.service.go_to(1)

    def test_diagnostic_distinguishes_saved_other_camera_without_moving(self):
        self.service.save_next()
        self.assertTrue(self.service.diagnostic()["current_camera_saved"])
        self.assertTrue(self.service.diagnostic()["slots"]["1"]["target_valid"])
        self.device = "another-camera"
        result = self.service.diagnostic()
        self.assertEqual(result["status"], "different_or_unsaved_device")
        self.assertEqual(result["stored_camera_count"], 1)
        self.assertFalse(result["slots"]["1"]["present"])
        self.assertNotIn("test-camera", str(result))
        self.assertNotIn("angle", str(result))
        self.adapter.move.assert_not_called()

    def test_diagnostic_api_requires_auth_and_never_moves(self):
        self.adapter.path = self.path.with_name("xiaomi-private-pose.json")
        self.service.path = self.path.with_name("telegram-position-presets.json")
        self.service.save_next()
        app = create_app(token="t" * 32, integrations=SimpleNamespace(privacy_adapter=self.adapter))
        path = "/api/v1/cameras/xiaomi/positions/diagnostic"
        with TestClient(app) as client:
            self.assertEqual(client.get(path).status_code, 401)
            result = client.get(path, headers={"Authorization": "Bearer " + "t" * 32})
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.json()["current_camera_saved"])
            self.assertNotIn(str(self.path.parent), result.text)
            self.assertNotIn(self.device, result.text)
        self.adapter.move.assert_not_called()
