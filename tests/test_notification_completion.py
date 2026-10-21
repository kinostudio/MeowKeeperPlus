import threading
import unittest

from meowkeeper.core.notifications.dispatcher import NotificationDispatcher


class NotificationCompletionTests(unittest.TestCase):
    def test_executing_work_and_callback_remain_pending(self):
        entered = threading.Event()
        release = threading.Event()
        callback_entered = threading.Event()
        callback_release = threading.Event()
        dispatcher = NotificationDispatcher()

        def work():
            entered.set()
            self.assertTrue(release.wait(3))
            return 1

        def callback(_future):
            callback_entered.set()
            self.assertTrue(callback_release.wait(3))

        try:
            future = dispatcher.submit(work)
            future.add_done_callback(callback)
            self.assertTrue(entered.wait(3))
            self.assertEqual(dispatcher.pending_count, 1)
            release.set()
            self.assertTrue(callback_entered.wait(3))
            self.assertEqual(dispatcher.pending_count, 1)
            callback_release.set()
            dispatcher.shutdown(wait=True)
            self.assertEqual(dispatcher.pending_count, 0)
            self.assertEqual(future.result(), 1)
        finally:
            release.set()
            callback_release.set()
            dispatcher.shutdown(wait=True)
