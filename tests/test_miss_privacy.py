"""MISS position privacy, with simulated camera responses only."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from meowkeeper.app.web.main import create_app
from meowkeeper.core.notifications.privacy import PrivacyService
from meowkeeper.integrations.xiaomi.miss_privacy import MissPrivacyAdapter


class MissPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "pose.json"
        self.current = {"angle": 10, "elevation": 20}
        self.device = "opaque-camera"
        self.calls = []
        self.connected = True
        self.move_effective = True
        self.adapter = self.new_adapter()
        self.service = PrivacyService(self.adapter)

    def new_adapter(self):
        return MissPrivacyAdapter(self.path, device=lambda: self.device,
                                  read=self.read, move=self.move)

    def read(self, _device):
        return {"success": True, "state": {"connected": self.connected,
                "position": {**self.current, "ret": 0},
                "capabilities": {"absolute_move": True}}}

    def move(self, device, angle, elevation):
        self.calls.append((device, angle, elevation))
        if self.move_effective:
            self.current = {"angle": angle, "elevation": elevation}
        return {"success": True}

    def configure(self):
        self.adapter.capture_private_pose()
        self.current = {"angle": 50, "elevation": 60}

    def test_reversible_hide_show_and_idempotence(self):
        self.configure()
        self.assertTrue(self.service.hide()["success"])
        self.assertEqual(self.current, {"angle": 10, "elevation": 20})
        self.assertEqual(self.service.hide()["status"], "unchanged")
        self.assertTrue(self.service.show()["success"])
        self.assertEqual(self.current, {"angle": 50, "elevation": 60})
        self.assertEqual(self.service.show()["status"], "unchanged")
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn(self.device, self.path.read_text())

    def test_restore_survives_restart(self):
        self.configure()
        self.assertTrue(self.service.hide()["success"])
        restarted = PrivacyService(self.new_adapter())
        self.assertTrue(restarted.show()["success"])
        self.assertEqual(self.current["angle"], 50)

    def test_unconfigured_does_not_move(self):
        self.assertFalse(self.service.hide()["success"])
        self.assertEqual(self.calls, [])

    def test_capture_pose_requires_return_to_normal_before_hide(self):
        self.adapter.capture_private_pose()
        self.assertFalse(self.service.hide()["success"])
        self.assertEqual(self.calls, [])

    def test_position_privacy_is_not_enabled_by_position_readback_alone(self):
        adapter = MissPrivacyAdapter(
            self.path, device=lambda: self.device,
            read=lambda _device: {"success": True, "state": {
                "connected": True,
                "position": {**self.current, "ret": 0},
                "capabilities": {"position": True, "absolute_move": False},
            }},
            move=self.move,
        )
        with self.assertRaisesRegex(ValueError, "尚無實機絕對定位"):
            adapter.capture_private_pose()
        adapter.set_mode("standby")
        self.assertFalse(PrivacyService(adapter).hide()["success"])
        self.assertEqual(self.calls, [])

    def test_disconnected_and_wrong_camera_never_move(self):
        self.configure()
        self.connected = False
        self.assertFalse(self.service.hide()["success"])
        self.connected = True
        self.device = "other-camera"
        self.assertFalse(self.service.hide()["success"])
        self.assertFalse(self.service.show()["success"])
        self.assertEqual(self.calls, [])

    def test_movement_accepted_but_no_readback_is_failure(self):
        self.configure()
        self.move_effective = False
        self.assertFalse(self.service.hide()["success"])
        self.assertEqual(self.current["angle"], 50)

    def test_ambiguous_pose_can_still_restore_saved_original(self):
        self.configure()
        self.service.hide()
        self.current = {"angle": 30, "elevation": 40}
        self.assertFalse(self.adapter.read_privacy_state()["verified"])
        self.assertTrue(self.service.show()["success"])
        self.assertEqual(self.current["angle"], 50)

    def test_standby_choice_persists_but_cannot_fake_control(self):
        self.configure()
        result = self.adapter.set_mode("standby")
        self.assertFalse(result["control_available"])
        self.assertEqual(json.loads(self.path.read_text())["mode"], "standby")
        self.assertFalse(self.service.hide()["success"])
        self.assertFalse(self.service.show()["success"])
        self.assertEqual(self.calls, [])

    def test_native_standby_reads_bool_and_restores_after_restart(self):
        state = {"on": True}
        calls = []

        def power(_device, on):
            if on is not None:
                calls.append(on)
                state["on"] = on
            return {"success": True, "on": state["on"]}

        adapter = MissPrivacyAdapter(self.path, device=lambda: self.device,
                                     read=self.read, move=self.move, power=power)
        adapter.set_mode("standby")
        self.assertTrue(PrivacyService(adapter).hide()["success"])
        self.assertFalse(state["on"])
        self.current = {"angle": 80, "elevation": 90}
        with self.assertRaises(ValueError):
            adapter.capture_private_pose()
        with self.assertRaises(ValueError):
            adapter.set_mode("position")
        restarted = MissPrivacyAdapter(self.path, device=lambda: self.device,
                                       read=self.read, move=self.move, power=power)
        self.assertTrue(PrivacyService(restarted).show()["success"])
        self.assertTrue(state["on"])
        self.assertEqual(calls, [False, True])
        self.assertEqual(self.current, {"angle": 10, "elevation": 20})
        self.assertEqual(self.calls, [(self.device, 10, 20)])

    def test_native_standby_rejects_ack_without_bool_readback(self):
        adapter = MissPrivacyAdapter(self.path, device=lambda: self.device,
                                     read=self.read, move=self.move,
                                     power=lambda _device, _on: {"success": True, "on": 0})
        adapter.set_mode("standby")
        self.assertFalse(PrivacyService(adapter).hide()["success"])
        self.assertEqual(self.calls, [])

    def test_standby_readback_failure_keeps_original_for_retry(self):
        state = {"on": True}
        def power(_device, on):
            if on is not None:
                state["on"] = on
            return {"success": True, "on": state["on"]}
        adapter = MissPrivacyAdapter(self.path, device=lambda: self.device,
                                     read=self.read, move=self.move, power=power)
        adapter.set_mode("standby")
        self.assertTrue(PrivacyService(adapter).hide()["success"])
        self.current = {"angle": 80, "elevation": 90}
        self.move_effective = False
        with unittest.mock.patch("meowkeeper.integrations.xiaomi.miss_privacy.time.monotonic", side_effect=[0, 30]):
            self.assertFalse(PrivacyService(adapter).show()["success"])
        self.assertTrue(json.loads(self.path.read_text())["standby_restore"])
        self.assertFalse(adapter.read_privacy_state()["verified"])
        self.move_effective = True
        self.assertTrue(PrivacyService(adapter).show()["success"])
        self.assertEqual(self.current, {"angle": 10, "elevation": 20})

    def test_mode_change_cannot_overwrite_pending_restore(self):
        self.configure()
        self.service.hide()
        with self.assertRaises(ValueError):
            self.adapter.set_mode("standby")
        with self.assertRaises(ValueError):
            self.adapter.capture_private_pose()

    def test_dashboard_requires_auth_csrf_and_visual_confirmation(self):
        integration = Mock()
        integration.privacy_action.return_value = {"success": True}
        integration.privacy_configuration.return_value = {"mode": "standby", "standby_available": False}
        integration.control_diagnostics.return_value = {
            "items": [{"operation_id": "safe-id", "final_status": "readback_failed"}],
            "retained_count": 1, "limit": 50, "persistent": True,
        }
        token = "p" * 32
        app = create_app(token=token, integrations=integration)
        headers = {"Authorization": "Bearer " + token}
        path = "/api/v1/cameras/xiaomi/devices/opaque/privacy"
        diagnostics_path = "/api/v1/cameras/xiaomi/control/diagnostics"
        with TestClient(app) as client:
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.post(path, json={"action": "hide"}).status_code, 401)
            self.assertEqual(client.get(diagnostics_path).status_code, 401)
            diagnostics = client.get(diagnostics_path + "?operation_id=safe-id", headers=headers)
            self.assertEqual(diagnostics.status_code, 200)
            self.assertEqual(diagnostics.json()["items"][0]["final_status"], "readback_failed")
            integration.control_diagnostics.assert_called_once_with(operation_id="safe-id")
            self.assertEqual(client.get(path, headers=headers).json()["mode"], "standby")
            self.assertEqual(client.post(path, headers=headers, json={"action": "hide"}).status_code, 403)
            headers["X-MeowKeeper-CSRF"] = hashlib.sha256((token + ":csrf").encode()).hexdigest()
            self.assertEqual(client.post(path, headers=headers, json={"action": "capture"}).status_code, 400)
            integration.privacy_action.assert_not_called()
            response = client.post(path, headers=headers, json={"action": "capture", "visually_confirmed": True})
            self.assertTrue(response.json()["success"])
            integration.privacy_action.assert_called_once_with("opaque", "capture", mode=None)
