import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from meowkeeper.app.web.api import create_app
from meowkeeper.app.web.session import SessionManager
from meowkeeper.app.web.state import CoreStateFacade
from meowkeeper.core.analytics.behavior_summary import summarize_behavior
from meowkeeper.storage.database import SQLiteStore

START = "2026-09-27T00:00:00+08:00"
END = "2026-09-28T00:00:00+08:00"


class BehaviorStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "events.db")
        self.store.initialize()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add(self, event_id, start, end, *, pet="pet-a", track="track-a", state="eating"):
        self.store.connection.execute(
            """INSERT INTO behavior_events(event_id,pet_id,track_id,behavior_state,motion_state,
               event_tags_json,started_at,ended_at,raw_confidence,inference_method,model_version,
               rule_profile_version,source_id,evidence_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, pet, track, state, "stationary", "[]", start, end, 0.8,
             "rule", "test", "test", "private-source-never-returned", "{}"),
        )
        self.store.connection.commit()

    def test_empty_and_timezone_range_validation(self):
        self.assertEqual(summarize_behavior(self.store, START, END)["status"], "no_data")
        for start, end in (("bad", END), ("2026-09-27", END), (END, START), (START, "2026-12-01T00:00:00+08:00")):
            with self.assertRaises(ValueError):
                summarize_behavior(self.store, start, end)

    def test_cross_midnight_clip_pending_and_missing_time_not_sleep(self):
        self.add("before", "2026-09-26T23:50:00+08:00", "2026-09-27T00:10:00+08:00")
        self.add("pending", "2026-09-27T01:00:00+08:00", None)
        data = summarize_behavior(self.store, START, END)
        group = data["groups"][0]
        item = group["behaviors"][0]
        self.assertEqual(item["seconds"], 600)
        self.assertEqual(item["started_event_count"], 0)
        self.assertEqual(item["intersecting_event_count"], 1)
        self.assertEqual(group["pending_count"], 1)
        self.assertIsNone(group["effective_monitoring_percent"])
        self.assertNotIn("private-source", str(data))
        self.assertNotIn("sleeping", str(data))

    def test_overlap_suppresses_percentage_and_unknown_tracks_are_separate(self):
        self.add("one", START, "2026-09-27T00:10:00+08:00")
        self.add("two", START, "2026-09-27T00:20:00+08:00", state="resting")
        self.add("three", START, "2026-09-27T00:10:00+08:00", pet=None, track="x")
        self.add("four", START, "2026-09-27T00:10:00+08:00", pet=None, track="y")
        groups = summarize_behavior(self.store, START, END)["groups"]
        self.assertEqual(len(groups), 3)
        known = next(g for g in groups if g["identity_known"])
        self.assertTrue(known["overlapping_intervals"])
        self.assertTrue(all(b["calendar_percent"] is None for b in known["behaviors"]))

    def test_api_and_page_require_login_and_render_empty_state(self):
        sessions = SessionManager()
        sessions.configure_admin("admin", "test-password-long-enough")
        app = create_app(facade=CoreStateFacade(store=self.store), token="a" * 32, session_manager=sessions)
        with TestClient(app) as client:
            params = {"start": START, "end": END}
            self.assertEqual(client.get("/api/v1/analytics/behavior", params=params).status_code, 401)
            self.assertEqual(client.get("/analytics", follow_redirects=False).status_code, 307)
            response = client.get("/api/v1/analytics/behavior", params=params, headers={"Authorization": "Bearer " + "a" * 32})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "no_data")
            session = sessions.login("admin", "test-password-long-enough")
            client.cookies.set("meowkeeper_session", session.session_id)
            page = client.get("/analytics")
            self.assertEqual(page.status_code, 200)
            self.assertIn("summary-start", page.text)
            self.assertIn("/static/js/analytics.js", page.text)
            sessions.logout(session.session_id)
            self.assertEqual(client.get("/api/v1/analytics/behavior", params=params).status_code, 401)


if __name__ == "__main__":
    unittest.main()
