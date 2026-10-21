"""Privacy contracts only: these tests do not control a real camera."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from meowkeeper.core.notifications.control_journal import CameraControlJournal
from meowkeeper.core.notifications.privacy import PrivacyService


class PrivacyServiceTests(unittest.TestCase):
    def test_hide_and_show_are_idempotent_from_verified_camera_state(self):
        adapter = Mock()
        adapter.read_privacy_state.side_effect = [
            {"verified": True, "privacy_mode": mode}
            for mode in (False, True, True, True, False, False)
        ]
        adapter.hide.return_value = adapter.show.return_value = {"success": True}
        service = PrivacyService(adapter)
        self.assertEqual(service.hide()["status"], "ok")
        self.assertEqual(service.hide()["status"], "unchanged")
        self.assertEqual(service.show()["status"], "ok")
        self.assertEqual(service.show()["status"], "unchanged")
        adapter.hide.assert_called_once_with()
        adapter.show.assert_called_once_with()

    def test_missing_state_reader_never_sends_control(self):
        class Adapter:
            hide = Mock(return_value={"success": True})

        adapter = Adapter()
        self.assertEqual(PrivacyService(adapter).hide()["status"], "unavailable")
        adapter.hide.assert_not_called()

    def test_unverified_and_non_boolean_states_fail_closed(self):
        for state in ({}, {"verified": False, "privacy_mode": False},
                      {"verified": True, "privacy_mode": 0}):
            with self.subTest(state=state):
                adapter = Mock()
                adapter.read_privacy_state.return_value = state
                self.assertFalse(PrivacyService(adapter).hide()["success"])
                adapter.hide.assert_not_called()

    def test_accepted_command_without_state_change_is_not_success(self):
        adapter = Mock()
        adapter.read_privacy_state.side_effect = [
            {"verified": True, "privacy_mode": False},
            {"verified": True, "privacy_mode": False},
        ]
        adapter.hide.return_value = {"success": True, "status": "secret"}
        with tempfile.TemporaryDirectory() as directory:
            journal = CameraControlJournal(Path(directory) / "controls.json")
            result = PrivacyService(adapter, journal=journal).hide(camera_id="private-device-id")
            saved = journal.recent()["items"][0]
        self.assertFalse(result["success"])
        self.assertNotIn("secret", str(result))
        self.assertEqual(result["final_status"], "confirmed_no_change")
        self.assertEqual(saved["final_status"], "confirmed_no_change")
        self.assertTrue(saved["command_sent"])
        self.assertTrue(saved["readback_attempted"])
        self.assertNotIn("private-device-id", str(saved))
        self.assertIn(f"operation_id={result['operation_id']}", result["diagnostic_url"])
        self.assertIn("#xiaomi-control-diagnostics", result["diagnostic_url"])

    def test_success_is_recorded_only_after_verified_readback(self):
        adapter = Mock()
        adapter.read_privacy_state.side_effect = [
            {"verified": True, "privacy_mode": False},
            {"verified": True, "privacy_mode": True},
        ]
        adapter.hide.return_value = {"success": True}
        with tempfile.TemporaryDirectory() as directory:
            journal = CameraControlJournal(Path(directory) / "controls.json")
            result = PrivacyService(adapter, journal=journal).hide(camera_id="device")
            entry = journal.recent()["items"][0]
        self.assertTrue(result["success"])
        self.assertEqual(entry["final_status"], "confirmed_success")
        self.assertTrue(entry["command_sent"])
        self.assertEqual(entry["readback_result"], {"verified": True, "privacy_mode": True})

    def test_error_is_redacted_and_lock_is_released(self):
        adapter = Mock()
        adapter.read_privacy_state.side_effect = [
            RuntimeError("token=private"), {"verified": True, "privacy_mode": True},
        ]
        service = PrivacyService(adapter)
        self.assertNotIn("token", str(service.hide()))
        self.assertEqual(service.hide()["status"], "unchanged")

    def test_overlapping_request_is_rejected_without_duplicate_control(self):
        entered, release = threading.Event(), threading.Event()
        adapter = Mock()
        adapter.read_privacy_state.side_effect = [
            {"verified": True, "privacy_mode": False},
            {"verified": True, "privacy_mode": True},
        ]

        def hide():
            entered.set()
            if not release.wait(2):
                raise TimeoutError()
            return {"success": True}

        adapter.hide.side_effect = hide
        service = PrivacyService(adapter)
        results = []
        worker = threading.Thread(target=lambda: results.append(service.hide()))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(service.show()["status"], "busy")
            adapter.show.assert_not_called()
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(results[0]["success"])
        adapter.hide.assert_called_once_with()
