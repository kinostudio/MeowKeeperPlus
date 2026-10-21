import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from meowkeeper.app.web import CoreStateFacade, create_app
from meowkeeper.core.behavior import BehaviorEvent
from meowkeeper.core.events.web_review import ReviewConflict, WebEventReview
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.domain.behavior import BehaviorState, MotionState
from meowkeeper.storage.database import SQLiteStore


class IdentityEvidenceAPITests(unittest.TestCase):
    def test_scoped_review_uses_existing_history_and_never_overwrites_ai(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStore(Path(folder) / "events.db")
            try:
                registry = PetRegistry(Path(folder) / "pets")
                registry.register_pet("測試貓", pet_id="test-cat")
                now = datetime.now(timezone.utc)
                event = BehaviorEvent(None, "track-a", BehaviorState.RESTING, MotionState.STATIONARY, (),
                                      now, now, .8, .8, "fixture", "fixture", "fixture", "fixture", None)
                store.save_behavior_event(event)
                store.create_media_event(event_id="media", event_type="cat_detected", trigger_at=now,
                                         source_id="fixture", track_id="track-a", pet_id=None, confidence=.8,
                                         requested_pre_seconds=5, requested_post_seconds=10)
                snapshot = {"predicted_pet_id": None, "similarity": .7, "candidates": [], "unknown_reason": "small_margin"}
                store.record_identity_evidence(event.event_id, "track-a", snapshot)
                service = WebEventReview(store, registry)
                actor = hashlib.sha256(b"web-test").hexdigest()
                body = {"request_id": str(uuid4()), "expected_revision": 0, "label": "cat",
                        "pets": [{"pet_id": "test-cat", "behavior": None, "motion": None}],
                        "track_ref": event.event_id, "use_for_learning": False}
                self.assertEqual(service.submit("media", actor, body)["revision"], 1)
                self.assertEqual(service.submit("media", actor, body)["status"], "duplicate")
                row = store.connection.execute("SELECT label,learning_consent FROM behavior_event_annotations WHERE dimension='identity'").fetchone()
                self.assertEqual(tuple(row), ("test-cat", 0))
                self.assertEqual(store.identity_evidence("media")[0]["unknown_reason"], "small_margin")
                conflicting = {**body, "request_id": str(uuid4())}
                with self.assertRaises(ReviewConflict):
                    service.submit("media", actor, conflicting)
                missing = {**body, "request_id": str(uuid4()), "expected_revision": 1, "use_for_learning": True}
                with self.assertRaises(ValueError):
                    service.submit("media", actor, missing)
                self.assertEqual(len(service.view("media", actor)["history"]), 1)
                image = (Path(__file__).parent / "assets/cat.jpg").read_bytes()
                store._write("INSERT INTO identity_feedback_evidence(event_id,crop_sha256,crop_jpeg,captured_at) VALUES(?,?,?,?)",
                             (event.event_id, hashlib.sha256(image).hexdigest(), image, now.isoformat()))
                self.assertEqual(service.submit("media", actor, missing)["revision"], 2)
                row = store.connection.execute("SELECT learning_consent FROM behavior_event_annotations WHERE dimension='identity'").fetchone()[0]
                self.assertEqual(row, 1)
                self.assertEqual(store.identity_evidence("media")[0]["unknown_reason"], "small_margin")
                third = {**body, "request_id": str(uuid4()), "expected_revision": 2, "label": "not_cat", "pets": []}
                self.assertEqual(service.submit("media", actor, third)["revision"], 3)
                self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM behavior_event_annotations WHERE dimension='identity'").fetchone()[0], 0)
                self.assertEqual(len(service.view("media", actor)["history"]), 3)
            finally:
                store.close()

    def test_two_tracks_explicit_binding_auth_history_and_missing_crop(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStore(Path(folder) / "events.db")
            try:
                now = datetime.now(timezone.utc)
                store.create_media_event(event_id="media", event_type="cat_detected", trigger_at=now,
                                         source_id="safe-source", track_id=None, pet_id=None, confidence=.8,
                                         requested_pre_seconds=5, requested_post_seconds=10,
                                         track_ids=("track-a", "track-b"))
                for track in ("track-a", "track-b"):
                    event = BehaviorEvent(None, track, BehaviorState.RESTING, MotionState.STATIONARY, (),
                                          now-timedelta(seconds=1), now+timedelta(seconds=5),
                                          .8, .8, "rules", "fixture", "fixture", "safe-source", None)
                    store.save_behavior_event(event)
                    snapshot = {"predicted_pet_id": None, "similarity": .8, "model_version": "fixture",
                                "candidates": [], "unknown_reason": "small_margin", "valid_frames": 5,
                                "vote_fraction": .8, "evidence_seconds": 4,
                                "captured_at": now.isoformat(), "decision": "unknown"}
                    store.record_identity_evidence(event.event_id, track, snapshot)
                    store.record_identity_evidence(event.event_id, track, snapshot)
                store.initialize()  # migration may be rerun without dropping bindings
                self.assertEqual(len(store.identity_evidence("media")), 2)
                core = CoreStateFacade(store=store, event_directory=Path(folder) / "events")
                with TestClient(create_app(facade=core, token="isolated-identity-token")) as client:
                    path = "/api/v1/events/media/identity"
                    self.assertEqual(client.get(path).status_code, 401)
                    headers = {"Authorization": "Bearer isolated-identity-token"}
                    response = client.get(path, headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(len(response.json()["tracks"]), 2)
                    crop = response.json()["tracks"][0]["crop_url"]
                    self.assertEqual(client.get(crop).status_code, 401)
                    self.assertEqual(client.get(crop, headers=headers).status_code, 404)
                    self.assertEqual(client.get("/api/v1/events/absent/identity", headers=headers).status_code, 404)
            finally:
                store.close()
