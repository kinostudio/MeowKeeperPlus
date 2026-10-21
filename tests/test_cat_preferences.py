from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meowkeeper.core.notifications.cat_preferences import CatNotificationPreferences


class CatNotificationPreferencesTests(unittest.TestCase):
    def test_daily_sleep_boundaries_restart_and_individual_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            zone = timezone(timedelta(hours=8))
            now = datetime(2026, 10, 13, 22, 59, tzinfo=zone)
            path = Path(temporary) / "prefs.json"
            prefs = CatNotificationPreferences(path, clock=lambda: now, local_timezone=zone)
            prefs.set_sleep_window("123", 23, 8)
            prefs.set_cooldown_minutes("123", 15)
            self.assertTrue(prefs.should_send("123", "cat_detected"))
            now += timedelta(minutes=1)
            self.assertFalse(prefs.claim_delivery("123", "cat_detected"))
            self.assertEqual(prefs.status("123")["cooldown_remaining_seconds"], 0)
            self.assertTrue(prefs.should_send("456", "cat_detected"))
            self.assertTrue(prefs.should_send("123", "possible_vomit"))
            prefs = CatNotificationPreferences(path, clock=lambda: now, local_timezone=zone)
            now = datetime(2026, 10, 14, 7, 59, tzinfo=zone)
            self.assertFalse(prefs.should_send("123", "cat_detected"))
            now += timedelta(minutes=1)
            self.assertTrue(prefs.should_send("123", "cat_detected"))
            prefs.mute_hours("123", 1)
            prefs.clear_sleep_window("123")
            self.assertFalse(prefs.should_send("123", "cat_detected"))
            prefs.unmute("123")
            prefs.set_sleep_window("123", 8, 10)
            self.assertFalse(prefs.should_send("123", "cat_detected"))
            prefs.unmute("123")
            self.assertTrue(prefs.status("123")["sleep_active"])
            now += timedelta(hours=2)
            self.assertTrue(prefs.should_send("123", "cat_detected"))
            self.assertNotIn('"123"', path.read_text(encoding="utf-8"))

    def test_daily_sleep_rejects_invalid_hours_and_corrupt_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "prefs.json"
            prefs = CatNotificationPreferences(path)
            for start, end in ((23, 24), (-1, 8), (8, 8), (True, 8), ("23", 8)):
                with self.assertRaises(ValueError):
                    prefs.set_sleep_window("123", start, end)
            prefs.set_sleep_window("123", 23, 8)
            payload = json.loads(path.read_text(encoding="utf-8"))
            next(iter(payload["recipients"].values()))["sleep_window"]["end_hour"] = 24
            path.write_text(json.dumps(payload), encoding="utf-8")
            reopened = CatNotificationPreferences(path)
            self.assertTrue(reopened.status("123")["storage_error"])
            self.assertFalse(reopened.should_send("123", "cat_detected"))

    def test_personal_cooldown_boundary_restart_and_isolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            now = datetime(2026, 10, 12, tzinfo=timezone.utc)
            path = Path(temporary) / "prefs.json"
            prefs = CatNotificationPreferences(path, clock=lambda: now)
            prefs.set_cooldown_minutes("123", 15)
            self.assertTrue(prefs.claim_delivery("123", "cat_detected"))
            self.assertFalse(prefs.claim_delivery("123", "cat_detected"))
            self.assertTrue(prefs.claim_delivery("456", "cat_detected"))
            self.assertTrue(prefs.claim_delivery("123", "possible_vomit"))
            prefs = CatNotificationPreferences(path, clock=lambda: now)
            self.assertEqual(prefs.status("123")["cooldown_remaining_seconds"], 900)
            now += timedelta(seconds=899)
            self.assertFalse(prefs.should_send("123", "cat_detected"))
            now += timedelta(seconds=1)
            self.assertTrue(prefs.claim_delivery("123", "cat_detected"))
            prefs.set_cooldown_minutes("123", 0)
            self.assertTrue(prefs.claim_delivery("123", "cat_detected"))
            prefs.mute_hours("123", 1)
            self.assertFalse(prefs.claim_delivery("123", "cat_detected"))
            self.assertNotIn('"123"', path.read_text(encoding="utf-8"))

    def test_concurrent_claims_only_one_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefs = CatNotificationPreferences(Path(temporary) / "prefs.json")
            prefs.set_cooldown_minutes("123", 15)
            with ThreadPoolExecutor(max_workers=8) as workers:
                results = list(workers.map(lambda _: prefs.claim_delivery("123", "cat_detected"), range(20)))
            self.assertEqual(sum(results), 1)

    def test_legacy_schema_and_invalid_cooldown(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "prefs.json"
            key = CatNotificationPreferences._key("123")
            path.write_text(json.dumps({"version": 1, "recipients": {
                key: {"enabled": True, "mute_until": None}}}), encoding="utf-8")
            prefs = CatNotificationPreferences(path)
            self.assertEqual(prefs.status("123")["cooldown_minutes"], 0)
            for minutes in (-1, 10081, True, 1.5):
                with self.assertRaises(ValueError):
                    prefs.set_cooldown_minutes("123", minutes)
            prefs.set_cooldown_minutes("123", 15)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 2)

    def test_feedback_is_opt_in_per_chat_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "prefs.json"
            prefs = CatNotificationPreferences(path)
            self.assertFalse(prefs.feedback_enabled("123"))
            self.assertTrue(prefs.should_send("123", "cat_detected"))
            prefs.set_feedback_enabled("123", True)
            self.assertTrue(prefs.feedback_enabled("123"))
            self.assertFalse(prefs.feedback_enabled("456"))
            self.assertTrue(CatNotificationPreferences(path).feedback_enabled("123"))
            prefs.set_feedback_enabled("123", False)
            self.assertTrue(prefs.should_send("123", "cat_detected"))
            self.assertFalse(CatNotificationPreferences(path).feedback_enabled("123"))
            self.assertNotIn("123", path.read_text(encoding="utf-8"))

    def test_mute_expires_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            now = datetime(2026, 10, 6, tzinfo=timezone.utc)
            path = Path(temporary) / "prefs.json"
            first = CatNotificationPreferences(path, clock=lambda: now)
            first.mute_hours("123", 2)
            self.assertFalse(first.should_send("123", "cat_detected"))
            later = CatNotificationPreferences(path, clock=lambda: now + timedelta(hours=3))
            self.assertTrue(later.should_send("123", "cat_detected"))

    def test_corrupt_existing_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "prefs.json"
            path.write_text("{broken", encoding="utf-8")
            prefs = CatNotificationPreferences(path)
            self.assertFalse(prefs.should_send("123", "cat_detected"))
            with self.assertRaises(OSError):
                prefs.set_enabled("123", True)
            self.assertTrue(prefs.should_send("123", "possible_vomit"))


if __name__ == "__main__":
    unittest.main()
