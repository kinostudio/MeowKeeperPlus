import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from meowkeeper.core.event_semantics import (
    EventSemanticService,
    ProviderReply,
    SemanticRequest,
    SemanticResult,
)
from meowkeeper.storage.database import SCHEMA_VERSION, SQLiteStore


class SemanticRevisionTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.store.create_media_event(event_id="event", event_type="cat_detected",
                                     trigger_at=datetime.now(timezone.utc), source_id="test",
                                     track_id=None, pet_id=None, confidence=.8,
                                     requested_pre_seconds=5, requested_post_seconds=10)

    def tearDown(self):
        self.store.close()

    def test_append_replay_conflict_and_no_overwrite(self):
        first = SemanticResult("completed", (("scene", ("walking",)),), str(uuid4()))
        second = SemanticResult("completed", (("scene", ("resting",)),), str(uuid4()))
        self.store.record_event_semantics("event", first)
        self.store.record_event_semantics("event", second)
        self.store.record_event_semantics("event", first)
        history = self.store.event_semantic_history("event")
        self.assertEqual([row["revision"] for row in history], [1, 2])
        self.assertEqual(history[0]["result"], first.payload())
        self.assertEqual(self.store.event_semantics("event"), second.payload())
        with self.assertRaises(ValueError):
            self.store.record_event_semantics("event", SemanticResult("failed", job_id=first.job_id))
        self.assertFalse(self.store.connection.in_transaction)
        self.assertEqual(len(self.store.event_semantic_history("event")), 2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.connection.execute("UPDATE event_semantic_revisions SET status='failed'")
        self.store.connection.rollback()
        self.assertEqual(self.store.get_media_event("event")["pet_id"], "unknown")

    def test_schema_17_migration_preserves_only_known_snapshot_and_is_repeatable(self):
        old = SemanticResult("completed", (("scene", ("walking",)),)).payload()
        self.store.connection.execute(
            "INSERT INTO event_semantic_candidates(event_id,status,candidate_json) VALUES(?,?,?)",
            ("event", "completed", json.dumps(old)))
        self.store.connection.execute("UPDATE schema_meta SET value='17' WHERE key='schema_version'")
        self.store.connection.commit()
        self.store.initialize()
        self.store.initialize()
        history = self.store.event_semantic_history("event")
        self.assertEqual(self.store.schema_version, SCHEMA_VERSION)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["result"], old)
        self.assertNotIn("timings", history[0]["result"])

    def test_sqlite_backup_and_reopen_keep_revisions(self):
        self.store.record_event_semantics("event", SemanticResult("failed", job_id=str(uuid4())))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "backup.db"
            target = sqlite3.connect(path)
            self.store.connection.backup(target)
            target.close()
            restored = SQLiteStore(path)
            try:
                self.assertEqual(restored.event_semantic_history("event"),
                                 self.store.event_semantic_history("event"))
            finally:
                restored.close()

    def test_newer_schema_refuses_implicit_downgrade(self):
        self.store.connection.execute("UPDATE schema_meta SET value='999' WHERE key='schema_version'")
        self.store.connection.commit()
        with self.assertRaises(ValueError):
            self.store.initialize()
        self.assertEqual(self.store.schema_version, 999)

    def test_worker_measures_queue_response_validation_separately(self):
        class Provider:
            def analyze(self, request, *, timeout_seconds):
                return ProviderReply({"tracks": [{"track_id": "scene", "tags": ["walking"]}]},
                                     request_dispatched=True, http_status=200, api_elapsed_ms=0)
        service = EventSemanticService(Provider())
        try:
            result = service.submit(SemanticRequest("event", b"jpeg")).result(2)
            timing = result.payload()["timings"]
            self.assertEqual(result.status, "completed")
            for key in ("queue_ms", "api_response_ms", "validation_ms"):
                self.assertGreaterEqual(timing[key], 0)
            for key in ("queued_at", "started_at", "provider_started_at", "finished_at"):
                self.assertEqual(datetime.fromisoformat(timing[key]).utcoffset().total_seconds(), 0)
            self.assertNotIn("response_received_at",timing)  # 這個fixture只提供duration，不能補造網路UTC。
        finally:
            service.close()

    def test_unknown_telemetry_or_nonfinite_values_cannot_be_saved(self):
        for timing in (("payload", "private"), ("queue_ms", float("nan")),
                       ("queue_ms", True), ("started_at", "2026-10-14T10:00:00")):
            with self.assertRaises(ValueError):
                SemanticResult("failed", job_id=str(uuid4()), timings=(timing,))

    def test_model_json_cannot_assert_dispatch_and_future_id_matches_revision(self):
        class Provider:
            def analyze(self, request, *, timeout_seconds):
                return {"tracks": [], "transport": {"request_dispatched": True, "http_status": 200}}
        service = EventSemanticService(Provider())
        try:
            future = service.submit(SemanticRequest("event", b"jpeg"))
            result = future.result(2)
            self.assertEqual(result.job_id, future.semantic_job_id)
            self.assertIsNone(result.request_dispatched)
            self.assertNotIn("transport", result.payload())
            self.assertNotIn("api_response_ms", dict(result.timings))
            self.assertIn("provider_call_ms", dict(result.timings))
        finally:
            service.close()

    def test_failed_transport_does_not_fabricate_response_time(self):
        class Provider:
            def analyze(self, request, *, timeout_seconds):
                raise TimeoutError("不得保存此原始錯誤內容")
        service = EventSemanticService(Provider())
        try:
            result = service.submit(SemanticRequest("event", b"jpeg")).result(2)
            self.assertEqual(result.status, "timeout")
            self.assertNotIn("api_response_ms", result.payload()["timings"])
            self.assertNotIn("response_received_at", result.payload()["timings"])
            self.assertNotIn("原始錯誤", json.dumps(result.payload(), ensure_ascii=False))
        finally:
            service.close()

    def test_view_invalidated_during_api_drops_candidate_but_preserves_request_evidence(self):
        valid = [True]
        class Provider:
            def analyze(self, request, *, timeout_seconds):
                valid[0] = False
                return ProviderReply({"tracks":[{"track_id":"scene","tags":["walking"]}]},
                                     request_dispatched=True,http_status=200,api_elapsed_ms=3)
        service = EventSemanticService(Provider(),dispatch_validator=lambda _request:valid[0])
        try:
            result = service.submit(SemanticRequest("event",b"jpeg")).result(2)
            self.assertEqual(result.status,"insufficient_evidence")
            self.assertEqual(result.tracks,())
            self.assertTrue(result.request_dispatched)
            self.assertEqual(result.http_status,200)
            self.assertEqual(result.payload()["timings"]["api_response_ms"],3)
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
