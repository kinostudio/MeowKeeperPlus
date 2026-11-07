"""Offline receiver recovery; never contacts Telegram or changes a webhook."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from meowkeeper.core.notifications.telegram import TelegramNotificationError
from meowkeeper.core.notifications.telegram_feedback import TelegramFeedbackPoller
from meowkeeper.storage.database import SQLiteStore


class TelegramReceiverRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = SQLiteStore(Path(temporary.name) / "test.db")
        self.addCleanup(self.store.close)
        self.notifier = Mock()
        self.notifier.get_webhook_info.return_value = {"result": {"url": ""}}
        self.logs = []
        self.poller = TelegramFeedbackPoller(
            self.notifier, self.store, ["123"], logger=self.logs.append,
        )
        self.wait = Mock(return_value=False)
        self.poller._stop.wait = self.wait

    def finish(self):
        self.poller.close(wait=False)
        return []

    def test_conflict_recovers_same_receiver_and_preserves_offset(self):
        self.poller._commit_update(10)
        responses = iter([
            TelegramNotificationError("private diagnostic", status_code=409),
            [{"update_id": 11}], self.finish,
        ])
        def receive(**_kwargs):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response() if callable(response) else response
        self.notifier.get_updates.side_effect = receive
        self.poller._run()
        self.assertEqual(self.wait.call_args_list[0].args, (60.0,))
        self.assertEqual(self.notifier.get_webhook_info.call_count, 2)
        self.assertEqual(self.store.telegram_update_offset(), 12)
        self.assertEqual(self.notifier.get_updates.call_args_list[1].kwargs["offset"], 11)
        self.assertTrue(any("已恢復" in text for text in self.logs))
        self.assertFalse(any("private diagnostic" in text for text in self.logs))

    def test_repeated_conflict_is_bounded_and_shutdown_cancels_retry(self):
        self.notifier.get_updates.side_effect = TelegramNotificationError("hidden", status_code=409)
        self.wait.side_effect = [False, False, False, False, True]
        self.poller._run()
        self.assertEqual([call.args[0] for call in self.wait.call_args_list],
                         [60.0, 120.0, 240.0, 300.0, 300.0])
        self.assertEqual(self.notifier.get_updates.call_count, 5)

    def test_webhook_appearing_during_conflict_stops_without_polling_again(self):
        self.notifier.get_webhook_info.side_effect = [
            {"result": {"url": ""}}, {"result": {"url": "https://private.invalid/secret"}},
        ]
        self.notifier.get_updates.side_effect = TelegramNotificationError("hidden", status_code=409)
        self.poller._run()
        self.assertEqual(self.notifier.get_updates.call_count, 1)
        self.notifier.delete_webhook.assert_not_called()
        self.assertFalse(any("private.invalid" in text for text in self.logs))

    def test_credentials_remain_fail_closed(self):
        for code in (401, 403):
            with self.subTest(code=code):
                self.notifier.reset_mock()
                self.wait.reset_mock()
                self.notifier.get_updates.side_effect = TelegramNotificationError("hidden", status_code=code)
                self.poller._run()
                self.assertEqual(self.notifier.get_updates.call_count, 1)
                self.wait.assert_not_called()

    def test_startup_unexpected_error_retries_and_400_rechecks_webhook(self):
        self.notifier.get_webhook_info.side_effect = [RuntimeError("hidden"),
            {"result": {"url": ""}}, {"result": {"url": ""}}]
        calls = 0
        def receive(**_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TelegramNotificationError("hidden", status_code=400)
            return self.finish()
        self.notifier.get_updates.side_effect = receive
        self.poller._run()
        self.assertEqual([call.args[0] for call in self.wait.call_args_list], [3.0, 60.0])
        self.assertEqual(self.notifier.get_webhook_info.call_count, 3)

    def test_api_error_code_is_used_without_http_status(self):
        responses = iter([
            TelegramNotificationError("hidden", api_error_code=409),
            TelegramNotificationError("hidden", api_error_code=401),
        ])
        self.notifier.get_updates.side_effect = responses
        self.poller._run()
        self.assertEqual([call.args[0] for call in self.wait.call_args_list], [60.0])
        self.assertEqual(self.notifier.get_updates.call_count, 2)

    def test_startup_invalid_credentials_do_not_retry(self):
        self.notifier.get_webhook_info.side_effect = TelegramNotificationError(
            "hidden", api_error_code=403,
        )
        self.poller._run()
        self.notifier.get_updates.assert_not_called()
        self.wait.assert_not_called()

    def test_real_worker_shutdown_interrupts_conflict_wait_and_start_is_single(self):
        retrying = threading.Event()
        self.poller._stop = threading.Event()
        self.poller.logger = lambda _message: retrying.set()
        self.notifier.get_updates.side_effect = TelegramNotificationError("hidden", status_code=409)
        self.poller.start()
        self.addCleanup(self.poller.close)
        worker = self.poller._thread
        self.assertTrue(retrying.wait(2.0))
        self.poller.start()
        self.assertIs(self.poller._thread, worker)
        self.assertTrue(self.poller.running)
        self.poller.close()
        self.assertFalse(self.poller.running)
        self.assertEqual(self.notifier.get_updates.call_count, 1)


if __name__ == "__main__":
    unittest.main()
