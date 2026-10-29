"""Bounded, source-scoped recovery after an unexpected monitor-worker failure."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from meowkeeper.app.core_controller import RepositoryCoreController
from meowkeeper.storage.database import SQLiteStore


class _FakeTimer:
    created: list["_FakeTimer"] = []

    def __init__(self, interval, function, args=(), kwargs=None):
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = False
        self.started = False
        self.cancelled = False
        self.created.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if self.started and not self.cancelled:
            self.function(*self.args, **self.kwargs)


class CoreMonitorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="meowkeeper-recovery-")
        self.root = Path(self.temporary.name)
        self.store = SQLiteStore(self.root / "events.sqlite3")
        self.logs: list[str] = []
        self.controller = RepositoryCoreController(
            store=self.store,
            source_url="rtsp://127.0.0.1:8554/test",
            analyzer_mode="noop",
            ring_directory=self.root / "ring",
            event_directory=self.root / "events",
            preview_directory=self.root / "preview",
            logger=self.logs.append,
        )
        _FakeTimer.created.clear()

    def tearDown(self):
        self.controller.close()
        self.store.close()
        self.temporary.cleanup()

    def _wait_for(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_unexpected_overflow_records_safe_location_and_schedules_after_cleanup(self):
        with patch("meowkeeper.app.core_controller.threading.Timer", _FakeTimer), patch(
            "meowkeeper.app.core_controller.resolve_source",
            side_effect=OverflowError("private-token-must-not-be-logged"),
        ):
            self.controller.start_monitor()
            self.assertTrue(self._wait_for(lambda: bool(_FakeTimer.created)))

        status = self.controller.get_status()
        recovery = status["metrics"]["monitor_recovery"]
        self.assertEqual(status["status"], "error")
        self.assertIsNone(self.controller._thread)
        self.assertEqual(recovery["status"], "retry_wait")
        self.assertEqual(recovery["retry_attempt"], 1)
        self.assertEqual(recovery["next_retry_seconds"], 30)
        self.assertIn("core_controller.py:", recovery["last_exception_location"])
        self.assertTrue(_FakeTimer.created[0].started)
        self.assertEqual(_FakeTimer.created[0].interval, 30)
        self.assertNotIn("private-token-must-not-be-logged", "\n".join(self.logs))
        self.assertNotIn("private-token-must-not-be-logged", str(status))

    def test_recovery_attempts_back_off_and_stop_at_configured_limit(self):
        with patch("meowkeeper.app.core_controller.threading.Timer", _FakeTimer):
            with self.controller._lock:
                self.controller._state = "error"
                self.controller._schedule_monitor_restart_locked(self.controller._lifecycle_generation)
            delays = [self.controller._restart_timer.interval]
            exhausted = None
            for _ in range(len(self.controller._restart_delays_seconds)):
                timer = self.controller._restart_timer
                self.assertIsNotNone(timer)
                with patch.object(self.controller, "_start_monitor_locked") as start:
                    timer.fire()
                    start.assert_called_once_with(self.controller._lifecycle_generation)
                with self.controller._lock:
                    self.controller._state = "error"
                    exhausted = self.controller._schedule_monitor_restart_locked(self.controller._lifecycle_generation)
                if self.controller.get_status()["metrics"]["monitor_recovery"]["status"] == "exhausted":
                    break
                delays.append(self.controller._restart_timer.interval)
        self.assertEqual(exhausted, "🛑 核心自動復原已達重試上限；請檢查診斷後手動啟動監控。")
        self.assertEqual(self.controller.get_status()["metrics"]["monitor_recovery"]["status"], "exhausted")
        self.assertEqual(delays, [30, 60, 120, 300, 300])

    def test_retry_timer_uses_current_generation_and_preserves_attempt_count(self):
        with patch("meowkeeper.app.core_controller.threading.Timer", _FakeTimer):
            with self.controller._lock:
                self.controller._state = "error"
                self.controller._schedule_monitor_restart_locked(self.controller._lifecycle_generation)
            generation = self.controller._lifecycle_generation
            with patch.object(self.controller, "_start_monitor_locked") as start:
                _FakeTimer.created[0].fire()
            start.assert_called_once_with(generation)
        self.assertEqual(self.controller._failure_restart_attempt, 1)
        self.assertEqual(self.controller.get_status()["metrics"]["monitor_recovery"]["status"], "retrying")

    def test_manual_stop_cancels_pending_recovery_and_source_missing_never_retries(self):
        with patch("meowkeeper.app.core_controller.threading.Timer", _FakeTimer):
            with self.controller._lock:
                self.controller._state = "error"
                self.controller._schedule_monitor_restart_locked(self.controller._lifecycle_generation)
            self.controller.stop_monitor(confirm=True)
        self.assertTrue(_FakeTimer.created[0].cancelled)
        self.assertEqual(self.controller.get_status()["status"], "stopped")

        no_source = RepositoryCoreController(store=self.store, source_url=None)
        try:
            self.assertEqual(no_source.start_monitor()["status"], "waiting_for_source")
            self.assertEqual(no_source.get_status()["metrics"]["monitor_recovery"]["status"], "idle")
            self.assertIsNone(no_source._restart_timer)
        finally:
            no_source.close()

    def test_close_cancels_pending_recovery(self):
        with patch("meowkeeper.app.core_controller.threading.Timer", _FakeTimer):
            with self.controller._lock:
                self.controller._state = "error"
                self.controller._schedule_monitor_restart_locked(self.controller._lifecycle_generation)
            self.controller.close()
        self.assertTrue(_FakeTimer.created[0].cancelled)
        self.assertTrue(self.controller._closing)

    def test_expected_source_configuration_failure_does_not_auto_retry(self):
        with patch("meowkeeper.app.core_controller.threading.Timer", _FakeTimer), patch(
            "meowkeeper.app.core_controller.resolve_source",
            side_effect=ValueError("invalid source configuration"),
        ):
            self.controller.start_monitor()
            self.assertTrue(self._wait_for(lambda: self.controller._thread is None))
        self.assertEqual(self.controller.get_status()["status"], "error")
        self.assertEqual(_FakeTimer.created, [])


if __name__ == "__main__":
    unittest.main()
