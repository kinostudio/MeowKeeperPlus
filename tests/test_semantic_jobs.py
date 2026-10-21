"""安全工作恢復、終結、重送與不保存影像的實際SQLite測試。"""

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from meowkeeper.core.event_semantics import (
    EventSemanticService,
    SemanticRequest,
    SemanticResult,
    normalize_result,
)
from meowkeeper.core.semantic_storyboard import TrackStoryboard
from meowkeeper.storage.database import SQLiteStore
from meowkeeper.storage.semantic_jobs import SemanticJobJournal


class SemanticJobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "events.db"
        self.store = SQLiteStore(self.path)
        self.addCleanup(self.store.close)
        self.store.create_media_event(event_id="event", event_type="cat_detected", trigger_at=datetime.now(timezone.utc),
                                      source_id="safe", track_id=None, pet_id=None, confidence=.8,
                                      requested_pre_seconds=5, requested_post_seconds=10)
        self.clock = 1000
        self.journal = SemanticJobJournal(self.store, clock=lambda: self.clock)
        self.request = SemanticRequest("event", b"private-image-not-to-be-persisted")
        self.provider = SimpleNamespace(provider_id="local_test", model_id="fixture")

    def test_restart_expires_queued_and_unknown_running_without_resending(self):
        queued, running = str(uuid4()), str(uuid4())
        self.journal.register(self.request, queued, self.provider, 60)
        second = SemanticRequest("event", b"different-input")
        self.journal.register(second, running, self.provider, 60)
        self.journal.start(running)
        self.clock += 61
        self.store.close()
        self.store = SQLiteStore(self.path)
        self.addCleanup(self.store.close)
        journal = SemanticJobJournal(self.store, clock=lambda: self.clock)
        journal.recover(self.store.record_event_semantics)
        journal.recover(self.store.record_event_semantics)
        history = self.store.event_semantic_history("event")
        self.assertEqual(len(history), 2)
        results = {row["result"]["job_id"]: row["result"] for row in history}
        self.assertFalse(results[queued]["transport"]["request_dispatched"])
        self.assertNotIn("transport", results[running])
        self.assertTrue(all(row["result"]["status"] == "cancelled" for row in history))
        dump = "".join(self.store.connection.iterdump())
        self.assertNotIn("private-image", dump)
        self.assertFalse(journal.register(self.request, str(uuid4()), self.provider, 60))

    def test_completed_outbox_recovers_once_after_sink_failure(self):
        job = str(uuid4())
        self.journal.register(self.request, job, self.provider, 60)
        self.journal.finish(job, SemanticResult("completed", (("scene", ("sitting",)),), job_id=job))
        with self.assertRaises(RuntimeError):
            self.journal.recover(lambda *_args: (_ for _ in ()).throw(RuntimeError("fixture failure")))
        self.journal.recover(self.store.record_event_semantics)
        self.journal.recover(self.store.record_event_semantics)
        self.assertEqual(len(self.store.event_semantic_history("event")), 1)
        self.assertEqual(self.journal.list_event("event")[0]["projected"], 1)

    def test_queue_full_and_invalid_media_have_persisted_terminal_results(self):
        gate, entered = threading.Event(), threading.Event()
        def analyze(*_args, **_kwargs):
            entered.set()
            gate.wait(2)
            return {"tracks": []}
        provider = SimpleNamespace(analyze=analyze)
        service = EventSemanticService(provider, journal=self.journal, result_sink=self.store.record_event_semantics, max_pending=1)
        try:
            first = service.submit(self.request)
            self.assertTrue(entered.wait(1))
            second = service.submit(SemanticRequest("event", b"queued"))
            full = service.submit(SemanticRequest("event", b"full"))
            invalid = service.submit(SemanticRequest("event", b""))
            self.assertEqual(full.result().status, "queue_full")
            self.assertEqual(invalid.result().status, "invalid_image")
            gate.set()
            first.result(2)
            second.result(2)
        finally:
            gate.set()
            service.close()
        self.journal.recover(self.store.record_event_semantics)
        states = {row["result"]["status"] for row in self.store.event_semantic_history("event")}
        self.assertTrue({"queue_full", "invalid_image"}.issubset(states))

    def test_external_database_lock_is_bounded_and_does_not_send(self):
        other = SQLiteStore(self.path)
        self.addCleanup(other.close)
        other.connection.execute("BEGIN IMMEDIATE")
        service = EventSemanticService(self.provider, journal=self.journal)
        try:
            started = time.monotonic()
            result = service.submit(self.request).result(1)
            self.assertLess(time.monotonic()-started, .25)
            self.assertEqual(result.status, "failed")
            self.assertFalse(result.request_dispatched)
        finally:
            other.connection.rollback()
            service.close()
        self.assertEqual(self.store.connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)

    def test_input_epoch_and_revision_change_idempotency_key(self):
        for request in (self.request, SemanticRequest("event", self.request.jpeg, track_epoch="new-view"),
                        SemanticRequest("event", self.request.jpeg, input_revision=2)):
            self.assertTrue(self.journal.register(request, str(uuid4()), self.provider, 60))
        self.assertFalse(self.journal.register(self.request, str(uuid4()), self.provider, 60))
        self.store.initialize()
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM semantic_work_jobs").fetchone()[0], 3)

    def test_versioned_posture_and_unverified_locations(self):
        request = SemanticRequest("event", b"image")
        self.assertEqual(normalize_result({"tracks": [{"track_id": "scene", "tags": ["sitting"]}]}, request).tracks[0][1], ("sitting",))
        for tags in (["near_water"], ["unknown", "walking"]):
            with self.assertRaises(ValueError):
                normalize_result({"tracks": [{"track_id": "scene", "tags": tags}]}, request)
        self.assertNotIn("image", json.dumps(self.journal.list_event("event")))

    def test_manual_reanalysis_reuses_only_saved_crops_and_deduplicates_request_id(self):
        board = TrackStoryboard("track", "track_01", b"cropped-fixture",(-1,-.5,0,.5),"epoch")
        request = SemanticRequest("event",b"private-full-image",("track",),datetime.now(timezone.utc),(board,))
        calls = []
        entered = threading.Event()
        def analyze(value,**_kwargs):
            calls.append(value)
            if len(calls) == 2:
                entered.set()
            return {"tracks":[{"track_alias":"track_01","tags":[]}]}
        provider = SimpleNamespace(analyze=analyze)
        service = EventSemanticService(provider,journal=self.journal,result_sink=self.store.record_event_semantics)
        request_id = str(uuid4())
        try:
            service.submit(request).result(2)
            response = service.reanalyze("event",request_id)
            self.assertTrue(entered.wait(2))
            self.assertEqual(service.reanalyze("event",request_id)["job_id"],response["job_id"])
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[1].storyboards,(board,))
            self.assertEqual(calls[1].jpeg,board.jpeg)
            self.assertEqual(calls[1].input_revision,2)
            with self.assertRaises(ValueError):
                service.reanalyze("event",str(uuid4()))
            self.assertNotIn("private-full-image","".join(self.store.connection.iterdump()))
        finally:
            service.close()
        service = EventSemanticService(provider,journal=self.journal)
        try:
            self.assertEqual(service.reanalyze("event",request_id)["job_id"],response["job_id"])
            self.assertEqual(len(calls),2)
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
