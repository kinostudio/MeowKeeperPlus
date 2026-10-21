from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from meowkeeper.core.analytics.daily_monitoring import (
    DailyMonitoringConfig,
    DailyMonitoringRecorder,
)
from meowkeeper.core.detection.frame_analysis import FrameAnalysis
from meowkeeper.core.events.analysis_pipeline import AnalysisPipeline
from meowkeeper.storage.database import SCHEMA_VERSION, SQLiteStore


class DailyMonitoringTests(unittest.TestCase):
    day = "2026-09-29"
    start = datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "stats.sqlite3"
        self.store = SQLiteStore(self.path)
        self.addCleanup(self.store.close)

    def recorder(self):
        recorder = DailyMonitoringRecorder(self.store, local_timezone=timezone.utc)
        self.addCleanup(recorder.close)
        return recorder

    def test_persists_across_restart_and_keeps_unavailable_behaviors_null(self):
        recorder = self.recorder()
        for second in (0, 1, 2):
            recorder.observe(self.start + timedelta(seconds=second), cat_present=True)
        self.assertTrue(recorder.close())
        self.store.close()
        self.store = SQLiteStore(self.path)
        self.addCleanup(self.store.close)
        self.store.initialize()
        self.assertEqual(self.store.schema_version, SCHEMA_VERSION)
        result = self.store.daily_monitoring_summary(day=self.day)
        self.assertEqual(result["monitoring_seconds"], 2.0)
        self.assertEqual(result["cat_active_seconds"], 2.0)
        for key in ("feeding_seconds", "drinking_seconds", "sleep_seconds"):
            self.assertIsNone(result[key])

    def test_missing_unknown_and_negative_analysis_are_distinct(self):
        self.assertIsNone(self.store.daily_monitoring_summary(day=self.day)["monitoring_seconds"])
        recorder = self.recorder()
        recorder.observe(self.start, cat_present=None)
        recorder.observe(self.start + timedelta(seconds=1), cat_present=None)
        recorder.close()
        result = self.store.daily_monitoring_summary(day=self.day)
        self.assertEqual(result["monitoring_seconds"], 1.0)
        self.assertIsNone(result["cat_active_seconds"])
        negative = self.recorder()
        negative.observe(self.start + timedelta(seconds=2), cat_present=False)
        negative.observe(self.start + timedelta(seconds=3), cat_present=False)
        negative.close()
        self.assertEqual(self.store.daily_monitoring_summary(day=self.day)["cat_active_seconds"], 0.0)

    def test_gaps_clock_regression_and_source_switch_do_not_inflate_time(self):
        recorder = self.recorder()
        for second in (0, 1, 0, 1, 100, 101):
            recorder.observe(self.start + timedelta(seconds=second), cat_present=True)
        recorder.end_session()
        recorder.observe(self.start + timedelta(seconds=102), cat_present=True)
        recorder.observe(self.start + timedelta(seconds=103), cat_present=True)
        recorder.close()
        result = self.store.daily_monitoring_summary(day=self.day)
        self.assertEqual(result["monitoring_seconds"], 3.0)
        self.assertEqual(result["cat_active_seconds"], 3.0)

    def test_midnight_splits_by_machine_timezone(self):
        recorder = DailyMonitoringRecorder(self.store, local_timezone=timezone(timedelta(hours=8)))
        before = datetime(2026, 9, 28, 15, 59, 59, tzinfo=timezone.utc)
        recorder.observe(before, cat_present=True)
        recorder.observe(before + timedelta(seconds=2), cat_present=True)
        recorder.close()
        for day in ("2026-09-28", "2026-09-29"):
            self.assertEqual(self.store.daily_monitoring_summary(day=day)["monitoring_seconds"], 1.0)

    def test_batch_is_idempotent_and_validates_totals(self):
        batch = {"batch_id": str(uuid4()), "day": self.day, "monitoring_seconds": 3.0,
                 "analyzed_seconds": 2.0, "cat_visible_seconds": 1.0}
        self.store.record_monitoring_batch(**batch)
        self.store.record_monitoring_batch(**batch)
        self.assertEqual(self.store.daily_monitoring_summary(day=self.day)["monitoring_seconds"], 3.0)
        for value in (float("nan"), float("inf"), -1, 4):
            with self.assertRaises(ValueError):
                self.store.record_monitoring_batch(**{**batch, "cat_visible_seconds": value})

    def test_slow_database_is_off_media_thread_and_queue_is_bounded(self):
        entered, release = threading.Event(), threading.Event()
        thread_ids = []

        class SlowStore:
            def record_monitoring_batch(self, **_batch):
                thread_ids.append(threading.get_ident())
                entered.set()
                release.wait(timeout=3)

        recorder = DailyMonitoringRecorder(
            SlowStore(), config=DailyMonitoringConfig(maximum_pending_batches=1),
        )
        try:
            recorder.observe(self.start, cat_present=True)
            recorder.observe(self.start + timedelta(seconds=1), cat_present=True)
            recorder.flush()
            self.assertTrue(entered.wait(timeout=1))
            for second in (2, 3, 4):
                recorder.observe(self.start + timedelta(seconds=second), cat_present=True)
                recorder.flush()
            self.assertGreater(recorder.dropped_batches, 0)
            self.assertNotIn(threading.get_ident(), thread_ids)
            self.assertFalse(recorder.close(wait_seconds=0))
        finally:
            release.set()
            self.assertTrue(recorder.close())

    def test_real_sqlite_lock_does_not_block_frame_observation(self):
        blocker = sqlite3.connect(self.path)
        recorder = self.recorder()
        try:
            blocker.execute("BEGIN IMMEDIATE")
            recorder.observe(self.start, cat_present=True)
            recorder.observe(self.start + timedelta(seconds=1), cat_present=True)
            recorder.flush()
            started = time.monotonic()
            recorder.observe(self.start + timedelta(seconds=2), cat_present=True)
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            blocker.rollback()
            blocker.close()
        self.assertTrue(recorder.close())
        self.assertEqual(self.store.daily_monitoring_summary(day=self.day)["monitoring_seconds"], 2.0)

    def test_failed_writer_preserves_unavailable_state_and_redacts_error(self):
        logs = []

        class BrokenStore:
            def record_monitoring_batch(self, **_batch):
                raise RuntimeError("token=DO_NOT_LOG_ME")

        recorder = DailyMonitoringRecorder(BrokenStore(), logger=logs.append)
        recorder.observe(self.start, cat_present=True)
        recorder.observe(self.start + timedelta(seconds=1), cat_present=True)
        recorder.close()
        self.assertEqual(recorder.failed_batches, 1)
        self.assertNotIn("DO_NOT_LOG_ME", "\n".join(logs))
        self.assertIsNone(self.store.daily_monitoring_summary(day=self.day)["monitoring_seconds"])

    def test_cooldown_does_not_hide_raw_observation(self):
        analysis = FrameAnalysis(True, "cat_detected", 0.9, "貓咪", {"cat_count": 1})
        analyzer = SimpleNamespace(analyze=lambda _frame: analysis)
        pipeline = AnalysisPipeline(analyzer, cooldown_seconds=180)
        self.assertIsNotNone(pipeline.process(SimpleNamespace(captured_at=self.start)))
        self.assertIsNone(pipeline.process(SimpleNamespace(captured_at=self.start + timedelta(seconds=1))))
        self.assertIs(pipeline.last_analysis, analysis)
