import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from meowkeeper.core.event_semantics import SemanticRequest, VisionFailure
from meowkeeper.core.vision_request_guard import VisionRequestGuard
from meowkeeper.storage.database import SCHEMA_VERSION, SQLiteStore


class VisionRequestGuardTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "events.db"
        self.store = SQLiteStore(self.path)
        self.now = datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp()
        self.guard = VisionRequestGuard(self.store, clock=lambda: self.now)
        self.reference = "a" * 64
        for event in ("e1", "e2", "e3", "e4", "e5"):
            self.store.create_media_event(event_id=event, event_type="cat_detected",
                                         trigger_at=datetime.now(timezone.utc), source_id="test", track_id=None,
                                         pet_id=None, confidence=.8, requested_pre_seconds=0, requested_post_seconds=1)

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def reserve(self, event, limit=100):
        return self.guard.reserve(self.reference, SemanticRequest(event, b"jpeg"), daily_limit=limit)

    def rejected(self, event, category, limit=100):
        with self.assertRaises(VisionFailure) as caught:
            self.reserve(event, limit)
        self.assertEqual(caught.exception.category, category)

    def test_daily_budget_failed_requests_count_and_utc_rollover(self):
        key = self.reserve("e1", 1)
        self.guard.finish(key, "bad_response")
        self.rejected("e2", "skipped_budget", 1)
        self.assertEqual(self.guard.status(self.reference)["reserved_attempts"], 1)
        self.assertEqual(self.guard.status(self.reference)["daily"]["skipped_budget"], 1)
        usage = self.guard.usage(self.reference, hours=1)
        self.assertEqual(usage["counts"]["skipped_budget"], 1)
        self.assertEqual(usage["requests"], 1)  # Denied attempts are decisions, not sent API requests.
        self.now += 86400
        self.reserve("e2", 1)

    def test_duplicate_busy_restart_and_expired_lease_do_not_repeat_cost(self):
        self.reserve("e1")
        self.rejected("e1", "duplicate_request")
        self.rejected("e2", "skipped_busy")
        other = SQLiteStore(self.path)
        try:
            fresh = VisionRequestGuard(other, clock=lambda: self.now)
            with self.assertRaises(VisionFailure) as caught:
                fresh.reserve(self.reference, SemanticRequest("e1", b"jpeg"), daily_limit=100)
            self.assertEqual(caught.exception.category, "duplicate_request")
        finally:
            other.close()
        self.now += 46
        self.reserve("e2")
        row = self.store.connection.execute("SELECT outcome FROM vision_request_jobs WHERE event_id='e1'").fetchone()
        self.assertEqual(row[0], "interrupted")
        self.rejected("e1", "duplicate_request")

    def test_one_automatic_attempt_per_event_but_manual_revision_can_reanalyze(self):
        first = self.guard.reserve(self.reference, SemanticRequest("e1", b"first-image"), daily_limit=10)
        self.guard.finish(first, "completed")
        self.rejected("e1", "duplicate_request")
        manual = SemanticRequest("e1", b"corrected-image", input_revision=2)
        manual_key = self.guard.reserve(self.reference, manual, daily_limit=10)
        self.guard.finish(manual_key, "completed")
        self.assertEqual(self.guard.status(self.reference)["reserved_attempts"], 2)

    def test_auth_error_requires_key_replacement_and_does_not_erase_attempts(self):
        self.guard.finish(self.reserve("e1"), "auth_error")
        self.now += 86400
        self.rejected("e2", "circuit_open")
        self.assertTrue(self.guard.status(self.reference)["key_fix_required"])
        self.guard.key_replaced(self.reference)
        self.reserve("e2")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM vision_request_jobs").fetchone()[0], 2)

    def test_retry_after_and_only_one_probe_after_cooldown(self):
        self.guard.finish(self.reserve("e1"), "rate_limited", retry_after=120)
        self.now += 119
        self.rejected("e2", "circuit_open")
        self.now += 1
        probe = self.reserve("e2")
        self.rejected("e3", "skipped_busy")
        self.guard.finish(probe, "completed")
        self.assertFalse(self.guard.status(self.reference)["circuit_open"])

    def test_three_failures_open_circuit_and_unknown_outcome_rejected(self):
        for event in ("e1", "e2", "e3"):
            self.guard.finish(self.reserve(event), "timeout")
        self.rejected("e4", "circuit_open")
        self.now += 61
        key = self.reserve("e4")
        with self.assertRaises(ValueError):
            self.guard.finish(key, "untrusted-payload")
        self.guard.finish(key, "completed")
        self.assertFalse(self.guard.status(self.reference)["circuit_open"])

    def test_media_removal_does_not_erase_cost_or_daily_budget(self):
        self.guard.finish(self.reserve("e1", 1), "completed")
        self.store._write("DELETE FROM media_events WHERE event_id='e1'")
        self.assertEqual(self.guard.status(self.reference)["reserved_attempts"], 1)
        self.rejected("e2", "skipped_budget", 1)

    def test_two_connections_concurrent_only_one_request_reserved(self):
        other = SQLiteStore(self.path)
        barrier = threading.Barrier(2)
        results = []

        def run(store, event):
            guard = VisionRequestGuard(store, clock=lambda: self.now)
            barrier.wait()
            try:
                guard.reserve(self.reference, SemanticRequest(event, b"jpeg"), daily_limit=100)
                results.append("reserved")
            except VisionFailure as error:
                results.append(error.category)
        threads = [threading.Thread(target=run, args=(self.store, "e1")),
                   threading.Thread(target=run, args=(other, "e2"))]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(3)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(sorted(results), ["reserved", "skipped_busy"])
        finally:
            other.close()

    def test_status_recovers_expired_work_without_a_new_cloud_request(self):
        self.reserve("e1")
        self.now += 46
        self.assertEqual(self.guard.status(self.reference)["reserved_attempts"], 1)
        row = self.store.connection.execute("SELECT state,outcome FROM vision_request_jobs WHERE event_id='e1'").fetchone()
        self.assertEqual(tuple(row), ("finished", "interrupted"))

    def test_sliding_rpm_and_tpm_use_actual_usage_without_minute_boundary_burst(self):
        first = self.guard.reserve(self.reference, SemanticRequest("e1", b"jpeg"),
                                   daily_limit=20, rpm_limit=2, tpm_limit=100,
                                   estimated_tokens=60, model_id="model-a")
        self.guard.finish(first, "completed", request_dispatched=True, http_status=200,
                          api_elapsed_ms=125.5, input_tokens=70, output_tokens=20)
        self.now += 30
        second = self.guard.reserve(self.reference, SemanticRequest("e2", b"jpeg"),
                                    daily_limit=20, rpm_limit=2, tpm_limit=200,
                                    estimated_tokens=20, model_id="model-a")
        self.guard.finish(second, "timeout", request_dispatched=True, http_status=504, api_elapsed_ms=1500)
        with self.assertRaises(VisionFailure) as caught:
            self.guard.reserve(self.reference, SemanticRequest("e3", b"jpeg"),
                               daily_limit=20, rpm_limit=2, tpm_limit=100,
                               estimated_tokens=1, model_id="model-a")
        self.assertEqual(caught.exception.category, "skipped_rpm")
        self.now += 31
        with self.assertRaises(VisionFailure) as caught:
            self.guard.reserve(self.reference, SemanticRequest("e3", b"jpeg"),
                               daily_limit=20, rpm_limit=3, tpm_limit=20,
                               estimated_tokens=1, model_id="model-a")
        self.assertEqual(caught.exception.category, "skipped_tpm")
        policy = self.guard.status(self.reference)
        self.assertEqual(policy["daily"]["input_tokens_actual"], 70)
        self.assertEqual(policy["daily"]["output_tokens_actual"], 20)
        self.assertEqual(policy["daily"]["timeouts"], 1)
        self.assertEqual(policy["daily"]["skipped_rpm"], 1)
        self.assertEqual(policy["daily"]["skipped_tpm"], 1)

    def test_gemini_daily_quota_rolls_at_pacific_midnight(self):
        from datetime import datetime
        self.now = datetime(2026, 10, 16, 6, 59, tzinfo=timezone.utc).timestamp()
        first = self.guard.reserve(self.reference, SemanticRequest("e1", b"jpeg"), daily_limit=5,
                                   model_id="gemini-model", reset_timezone="America/Los_Angeles")
        self.guard.finish(first, "completed")
        self.assertEqual(self.guard.status(self.reference, reset_timezone="America/Los_Angeles")["quota_day"], "2026-10-15")
        self.now += 120
        self.assertEqual(self.guard.status(self.reference, reset_timezone="America/Los_Angeles")["quota_day"], "2026-10-16")

    def test_event_cooldown_only_starts_after_a_request_was_dispatched(self):
        first = self.guard.reserve(self.reference, SemanticRequest("e1", b"jpeg"), daily_limit=20,
                                   model_id="model-a", cooldown_seconds=60, cooldown_ref=self.reference)
        self.guard.finish(first, "unavailable", request_dispatched=False)
        next_job = self.guard.reserve(self.reference, SemanticRequest("e2", b"jpeg"), daily_limit=20,
                                      model_id="model-a", cooldown_seconds=60, cooldown_ref=self.reference)
        self.guard.finish(next_job, "completed", request_dispatched=True, http_status=200)
        with self.assertRaises(VisionFailure) as caught:
            self.guard.reserve(self.reference, SemanticRequest("e3", b"jpeg"), daily_limit=20,
                               model_id="model-a", cooldown_seconds=60, cooldown_ref=self.reference)
        self.assertEqual(caught.exception.category, "skipped_cooldown")
        self.assertEqual(self.guard.usage(self.reference, hours=1)["counts"]["skipped_cooldown"], 1)

    def test_token_probe_is_persisted_but_only_keeps_safe_count_metadata(self):
        probe = self.guard.reserve_token_probe(self.reference, "gemini-test", daily_limit=5, rpm_limit=1)
        self.guard.finish_token_probe(probe, "completed", request_dispatched=True, http_status=200,
                                      total_tokens=321, elapsed_ms=15)
        with self.assertRaises(VisionFailure) as caught:
            self.guard.reserve_token_probe(self.reference, "gemini-test", daily_limit=5, rpm_limit=1)
        self.assertEqual(caught.exception.category, "skipped_rpm")
        row = self.store.connection.execute("SELECT outcome,http_status,total_tokens,elapsed_ms FROM vision_token_probes").fetchone()
        self.assertEqual(tuple(row), ("completed", 200, 321, 15))
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM vision_token_probes").fetchone()[0], 1)

    def test_schema_28_adds_scoped_cooldown_column_without_losing_jobs(self):
        old_path = Path(self.folder.name) / "schema-28.db"
        connection = sqlite3.connect(old_path)
        try:
            connection.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            connection.execute("INSERT INTO schema_meta(key,value) VALUES('schema_version','28')")
            connection.execute(
                "CREATE TABLE vision_request_jobs(job_key TEXT PRIMARY KEY,event_id TEXT NOT NULL,"
                "provider_ref TEXT NOT NULL,utc_day TEXT NOT NULL,state TEXT NOT NULL,outcome TEXT,"
                "started_at REAL NOT NULL,lease_until REAL NOT NULL,finished_at REAL,quota_day TEXT,"
                "model_id TEXT,request_dispatched INTEGER,http_status INTEGER,api_elapsed_ms REAL,"
                "input_tokens INTEGER,output_tokens INTEGER,estimated_tokens INTEGER)"
            )
            connection.execute(
                "INSERT INTO vision_request_jobs(job_key,event_id,provider_ref,utc_day,state,started_at,lease_until) "
                "VALUES('old-job','old-event',?,'2026-10-15','finished',1,1)", (self.reference,)
            )
            connection.commit()
        finally:
            connection.close()
        migrated = SQLiteStore(old_path)
        try:
            columns = {row[1] for row in migrated.connection.execute("PRAGMA table_info(vision_request_jobs)")}
            self.assertIn("cooldown_ref", columns)
            self.assertEqual(migrated.schema_version, SCHEMA_VERSION)
            row = migrated.connection.execute("SELECT event_id FROM vision_request_jobs WHERE job_key='old-job'").fetchone()
            self.assertEqual(row[0], "old-event")
        finally:
            migrated.close()


if __name__ == "__main__":
    unittest.main()
