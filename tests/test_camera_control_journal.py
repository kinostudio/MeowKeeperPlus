from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from meowkeeper.core.notifications.control_journal import CameraControlJournal


class CameraControlJournalTests(unittest.TestCase):
    def test_entries_are_bounded_redacted_and_reloadable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            journal = CameraControlJournal(path, limit=2)
            for number in range(3):
                journal.append(
                    operation_type="ptz_move",
                    camera_id="private-camera-id",
                    requested_target={"action": "move", "direction": "up", "token": "secret"},
                    final_status="command_accepted_unverified",
                    failure_reason="token=very-secret",
                )
            data = journal.recent()
            self.assertEqual(data["retained_count"], 2)
            self.assertTrue(data["persistent"])
            self.assertNotIn("private-camera-id", path.read_text(encoding="utf-8"))
            self.assertNotIn("very-secret", path.read_text(encoding="utf-8"))
            self.assertEqual(data["items"][0]["failure_reason"], "token=[已遮罩]")
            self.assertNotIn("token", data["items"][0]["requested_target"])
            self.assertEqual(CameraControlJournal(path, limit=2).recent()["retained_count"], 2)
            selected_id = data["items"][0]["operation_id"]
            selected = CameraControlJournal(path, limit=2).recent(selected_id)
            self.assertEqual([item["operation_id"] for item in selected["items"]], [selected_id])
            self.assertEqual(CameraControlJournal(path).recent("not-a-uuid")["items"], [])

    def test_correlated_camera_readback_verifies_move_and_rejects_other_camera(self):
        journal = CameraControlJournal()
        item = journal.append(
            operation_id="123e4567-e89b-12d3-a456-426614174000",
            operation_type="ptz_move",
            camera_id="camera-one",
            requested_target={"action": "move", "direction": "up"},
            command_sent=True,
            transport_result="response_received",
            final_status="command_accepted_unverified",
            before_position={"angle": 40, "elevation": 50},
        )
        journal.record_readback(
            item["operation_id"], "camera-two", verified=True,
            position={"angle": 40, "elevation": 51},
        )
        self.assertFalse(journal.recent()["items"][0]["readback_attempted"])
        journal.record_readback(
            item["operation_id"], "camera-one", verified=True,
            position={"angle": 40, "elevation": 51},
        )
        updated = journal.recent()["items"][0]
        self.assertTrue(updated["readback_attempted"])
        self.assertTrue(updated["observed_change"])
        self.assertEqual(updated["final_status"], "confirmed_success")


if __name__ == "__main__":
    unittest.main()
