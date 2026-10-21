from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from meowkeeper.app.web import CoreStateFacade, create_app
from meowkeeper.core.behavior.models import BehaviorEvent
from meowkeeper.core.event_semantics import SemanticResult
from meowkeeper.core.events.web_review import ReviewConflict, WebEventReview
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.core.notifications.track_tag_feedback import TrackTagFeedback
from meowkeeper.domain.behavior import BehaviorState, MotionState
from meowkeeper.storage.database import SQLiteStore


class WebEventReviewTests(unittest.TestCase):
    def test_candidate_accept_reject_revisions_keep_original_and_do_not_identify_pet(self):
        self.store.record_event_semantics(self.event, SemanticResult("completed", (("track-1", ("walking", "sitting")),)))
        payload = {"request_id":str(uuid4()), "expected_revision":0, "analysis_revision":1,
                   "track_id":"track-1", "tag_code":"walking", "decision":"accepted"}
        self.assertEqual(self.service.review_candidate(self.event, self.actor, payload)["revision"], 1)
        self.assertEqual(self.service.review_candidate(self.event, self.actor, payload)["revision"], 1)
        with self.assertRaises(ReviewConflict):
            self.service.review_candidate(self.event, self.actor, {**payload, "decision":"rejected"})
        self.service.review_candidate(self.event, self.actor, {**payload, "request_id":str(uuid4()), "expected_revision":1, "decision":"rejected"})
        history = self.service.view(self.event, self.actor)["candidate_reviews"]
        self.assertEqual([row["decision"] for row in history], ["accepted", "rejected"])
        self.assertEqual(self.store.event_semantics(self.event)["tracks"][0]["tags"], ["walking", "sitting"])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM behavior_event_annotations").fetchone()[0], 0)
        for changes in ({"track_id":"invented"}, {"tag_code":"drinking"}, {"analysis_revision":999}):
            with self.assertRaises(ValueError):
                self.service.review_candidate(self.event, self.actor, {**payload, "request_id":str(uuid4()), "expected_revision":2, **changes})
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.connection.execute("UPDATE web_semantic_reviews SET decision='accepted'")
        self.store.connection.rollback()

    def test_candidate_review_endpoint_preserves_auth_and_csrf(self):
        self.store.record_event_semantics(self.event, SemanticResult("completed", (("track-1", ("walking",)),)))
        token = "c" * 32
        app = create_app(facade=CoreStateFacade(store=self.store, event_directory=self.root / "events"), token=token)
        path = f"/api/v1/events/{self.event}/semantic/review"
        payload = {"request_id":str(uuid4()), "expected_revision":0, "analysis_revision":1,
                   "track_id":"track-1", "tag_code":"walking", "decision":"accepted"}
        with TestClient(app) as client:
            self.assertEqual(client.post(path, json=payload).status_code, 401)
            headers = {"Authorization":"Bearer " + token}
            self.assertEqual(client.post(path, json=payload, headers=headers).status_code, 403)
            headers["X-MeowKeeper-CSRF"] = hashlib.sha256((token+":csrf").encode()).hexdigest()
            self.assertEqual(client.post(path, json=payload, headers=headers).status_code, 200)
            self.assertEqual(client.post(path, json={**payload, "request_id":str(uuid4())}, headers=headers).status_code, 409)

    def test_submitted_telegram_track_tags_are_visible_in_same_media_review(self):
        event = BehaviorEvent(None, "track-bound", BehaviorState.RESTING, MotionState.STATIONARY,
                              (), datetime.now(timezone.utc), None, .8, None, "rule", "test", "test", "safe", None)
        self.store.save_behavior_event(event)
        self.store._write("INSERT INTO media_event_tracks(event_id,track_id) VALUES(?,?)", (self.event, event.track_id))
        digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
        self.store.record_behavior_event_telegram_receipt(event.event_id, digest("123"), 9)
        self.store.record_behavior_event_annotation(event_id=event.event_id, recipient_ref=digest("123"), reviewer_ref=digest("user:123"),
                                                     message_id=9, dimension="identity", label="cat-one", update_id=1, callback_ref=digest("1"))
        notifier = SimpleNamespace(send_message=lambda *_args, **_kwargs: SimpleNamespace(message_id=10),
                                   edit_message_reply_markup=lambda *_args, **_kwargs: None)
        service = TrackTagFeedback(self.store, notifier)
        service.open("123", event.event_id, self.registry.list_pets())
        review = self.store.connection.execute("SELECT review_id FROM telegram_track_tag_drafts").fetchone()[0]
        for update, tag in enumerate(("walking", "grooming", "review", "done"), 2):
            service.handle(f"tags:{review}:{tag}", chat_id="123", reviewer_ref=digest("user:123"), message_id=10,
                           update_id=update, callback_ref=digest(str(update)))
        history = self.service.view(self.event, self.actor)["telegram_tag_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["pets"][0]["tags"], ["walking", "grooming"])
        self.assertEqual(history[0]["pets"][0]["pet_id"], "cat-one")
        self.assertEqual(history[0]["scope"]["time_quality"], "video_level")

    def test_notification_timing_is_same_event_and_requires_existing_login(self):
        now = datetime.now(timezone.utc).isoformat()
        trace = {"trace_id": str(uuid4()), "event_trigger_at": now, "media_sendable_at": now,
                 "wait_started_at": now, "wait_finished_at": now, "validation_transcode_ms": 10,
                 "extra_wait_ms": 0, "wait_budget_ms": 5000, "caption_source": "local",
                 "wait_status": "not_requested", "uploads": [], "omitted_upload_count": 0}
        self.store.record_delivery_trace(self.event, trace)
        token = "d" * 32
        app = create_app(facade=CoreStateFacade(store=self.store, event_directory=self.root / "events"), token=token)
        with TestClient(app) as client:
            url = f"/api/v1/events/{self.event}/review"
            self.assertEqual(client.get(url).status_code, 401)
            response = client.get(url, headers={"Authorization": "Bearer " + token})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["delivery_history"], [trace])
        self.assertEqual(self.store.event_delivery_history("other-event"), [])

    def test_confirmed_not_cat_track_is_not_presented_as_cat_history(self):
        event = BehaviorEvent(None, "negative-track", BehaviorState.RESTING, MotionState.STATIONARY,
                              (), datetime.now(timezone.utc), None, .8, None, "rule", "test", "test", "safe", None)
        self.store.save_behavior_event(event)
        self.store._write("INSERT INTO media_event_tracks(event_id,track_id) VALUES(?,?)", (self.event, event.track_id))
        digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
        self.store.record_behavior_event_telegram_receipt(event.event_id, digest("123"), 9)
        notifier = SimpleNamespace(send_message=lambda *_args, **_kwargs: SimpleNamespace(message_id=10),
                                   edit_message_reply_markup=lambda *_args, **_kwargs: None)
        service = TrackTagFeedback(self.store, notifier)
        service.open_identity("123", event.event_id, self.registry.list_pets(), origin_message_id=9)
        review = self.store.connection.execute("SELECT review_id FROM telegram_track_tag_drafts WHERE state='identity'").fetchone()[0]
        for update, data in ((2, f"who:{review}:no:0"), (3, f"tags:{review}:done:1")):
            service.handle(data, chat_id="123", reviewer_ref=digest("user:123"), message_id=10,
                           update_id=update, callback_ref=digest(str(update)), pets=self.registry.list_pets())
        history = self.service.view(self.event, self.actor)["telegram_tag_history"]
        self.assertEqual(history[0]["label"], "not_cat")
        self.assertEqual(history[0]["pets"][0]["tags"], [])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = SQLiteStore(self.root / "events.db")
        self.addCleanup(self.store.close)
        self.registry = PetRegistry(self.root / "pets")
        self.registry.register_pet("測試貓一", pet_id="cat-one")
        self.registry.register_pet("測試貓二", pet_id="cat-two")
        self.event = str(uuid4())
        self.store.create_media_event(event_id=self.event,event_type="cat_detected",trigger_at=datetime.now(timezone.utc),
            source_id="test-source",track_id=None,pet_id=None,confidence=0.8,requested_pre_seconds=5,requested_post_seconds=10)
        self.actor = hashlib.sha256(b"test-reviewer").hexdigest()
        self.service = WebEventReview(self.store,self.registry)

    def payload(self):
        return {"request_id":str(uuid4()),"expected_revision":0,"label":"cat","pets":[
            {"pet_id":"cat-one","behavior":None,"motion":"walking"},
            {"pet_id":"cat-two","behavior":"eating","motion":None}]}

    def test_multi_cat_history_dedup_and_revision_conflict(self):
        payload = self.payload()
        self.assertEqual(self.service.submit(self.event,self.actor,payload)["revision"],1)
        self.assertEqual(self.service.submit(self.event,self.actor,payload)["status"],"duplicate")
        with self.assertRaises(ReviewConflict):
            self.service.submit(self.event,self.actor,self.payload())
        second = {"request_id":str(uuid4()),"expected_revision":1,"label":"not_cat","pets":[]}
        self.assertEqual(self.service.submit(self.event,self.actor,second)["revision"],2)
        view=self.service.view(self.event,self.actor)
        self.assertEqual(len(view["history"]),2)
        self.assertEqual(view["history"][0]["pets"][0]["motion"],"walking")
        self.assertEqual(view["history"][0]["pets"][1]["behavior"],"eating")
        self.store.initialize()
        self.assertEqual(len(self.service.view(self.event,self.actor)["history"]),2)

    def test_invalid_pet_or_empty_action_never_writes(self):
        for changes in ({"pet_id":"absent"},{"behavior":None,"motion":None},{"behavior":"medical_diagnosis"}):
            payload=self.payload(); payload["pets"][0].update(changes)
            with self.assertRaises(ValueError):
                self.service.submit(self.event,self.actor,payload)
        self.assertEqual(self.service.view(self.event,self.actor)["history"],[])

    def test_web_auth_and_csrf_are_independent_from_telegram_opt_in(self):
        token="t" * 32
        app=create_app(facade=CoreStateFacade(store=self.store,event_directory=self.root / "events"),token=token)
        headers={"Authorization":"Bearer "+token}
        path=f"/api/v1/events/{self.event}/review"
        with TestClient(app) as client:
            self.assertEqual(client.get(path).status_code,401)
            self.assertEqual(client.post(path,json=self.payload(),headers=headers).status_code,403)
            headers["X-MeowKeeper-CSRF"]=hashlib.sha256((token+":csrf").encode()).hexdigest()
            result=client.post(path,json=self.payload(),headers=headers)
            self.assertEqual(result.status_code,200)
            self.assertEqual(len(client.get(path,headers=headers).json()["history"]),1)
            self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM telegram_receipts").fetchone()[0],0)

    def test_segment_and_multiple_tags_are_saved_without_overwriting_previous_segment(self):
        self.store.update_media_event(self.event, actual_duration_seconds=15)
        first = self.payload()
        first["segment"] = {"start_seconds": 0, "end_seconds": 5}
        first["pets"][0].update(behavior=None, motion=None, tags=["walking", "playing"])
        first["pets"][1].update(behavior=None, motion=None, tags=["eating"])
        self.service.submit(self.event, self.actor, first)
        second = self.payload()
        second.update(expected_revision=1, segment={"start_seconds":5,"end_seconds":15})
        second["pets"][0].update(behavior=None, motion=None, tags=["resting"])
        self.service.submit(self.event, self.actor, second)
        view = self.service.view(self.event, self.actor)
        self.assertEqual(len(view["history"]), 2)
        self.assertEqual(view["history"][0]["pets"][0]["tags"], ["walking", "playing"])
        self.assertEqual(view["history"][0]["scope"]["segment"]["end_seconds"], 5)
        self.assertEqual(view["history"][1]["scope"]["segment"]["start_seconds"], 5)

    def test_invalid_segments_and_tags_do_not_write(self):
        self.store.update_media_event(self.event, actual_duration_seconds=15)
        for start, end in ((-1,5),(5,5),(5,25),(float("nan"),5),(False,5)):
            payload = self.payload()
            payload["segment"] = {"start_seconds":start,"end_seconds":end}
            with self.assertRaises(ValueError):
                self.service.submit(self.event, self.actor, payload)
        payload = self.payload()
        payload["pets"][0]["tags"] = ["unsupported"]
        with self.assertRaises(ValueError):
            self.service.submit(self.event, self.actor, payload)
        self.assertEqual(self.service.view(self.event,self.actor)["history"], [])

    def test_segment_edit_and_withdraw_append_without_deleting_original(self):
        self.store.update_media_event(self.event, actual_duration_seconds=15)
        first = self.payload()
        first["segment"] = {"start_seconds":0, "end_seconds":5}
        self.service.submit(self.event,self.actor,first)
        edited = {**first,"request_id":str(uuid4()),"expected_revision":1,"supersedes_revision":1,
                  "segment":{"start_seconds":1,"end_seconds":6}}
        self.service.submit(self.event,self.actor,edited)
        self.assertEqual(self.service.submit(self.event,self.actor,edited)["status"],"duplicate")
        with self.assertRaises(ReviewConflict):
            self.service.submit(self.event,self.actor,{**edited,"request_id":str(uuid4()),"expected_revision":2})
        withdrawn = {**edited,"request_id":str(uuid4()),"expected_revision":2,"supersedes_revision":2,"withdrawn":True}
        self.service.submit(self.event,self.actor,withdrawn)
        history = self.service.view(self.event,self.actor)["history"]
        self.assertEqual(len(history),3)
        self.assertEqual(history[0]["scope"]["segment"]["start_seconds"],0)
        self.assertEqual(history[1]["scope"]["supersedes_revision"],1)
        self.assertTrue(history[2]["scope"]["withdrawn"])
