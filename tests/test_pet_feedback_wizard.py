from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from meowkeeper.core.notifications.pet_feedback_wizard import (
    PetFeedbackWizard,
    first_round,
    pet_token,
)
from meowkeeper.storage.database import SQLiteStore


class PetFeedbackWizardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SQLiteStore(Path(self.temp.name) / "review.db")
        self.addCleanup(self.store.close)
        self.event = str(uuid4())
        self.chat = "123"
        self.ref = hashlib.sha256(self.chat.encode()).hexdigest()
        self.reviewer = hashlib.sha256(b"user:123").hexdigest()
        self.store.create_media_event(event_id=self.event, event_type="cat_detected",
                                      trigger_at=datetime.now(timezone.utc), source_id="test",
                                      track_id=None, pet_id="unknown", confidence=0.5,
                                      requested_pre_seconds=5, requested_post_seconds=10,
                                      trigger_origin="detector")
        self.store.record_telegram_receipt(self.event, self.ref, "video", 10)
        self.pets = [SimpleNamespace(pet_id="first", display_name="嗨啵"),
                     SimpleNamespace(pet_id="second", display_name="Saki")]
        self.sent = []

        def send(chat, text, **kwargs):
            result = SimpleNamespace(message_id=100 + len(self.sent))
            self.sent.append((text, kwargs, result.message_id))
            return result
        self.notifier = SimpleNamespace(send_message=send)
        self.wizard = PetFeedbackWizard(self.store, self.notifier, lambda: self.pets)

    def handle(self, data, update=1, message=10, reviewer=None):
        return self.wizard.handle(data, chat_id=self.chat, reviewer_ref=reviewer or self.reviewer,
                                  message_id=message, update_id=update,
                                  callback_ref=hashlib.sha256(str(update).encode()).hexdigest())

    def choice(self, key):
        return f"pick:{self.event}:{key}"

    def callback(self, index=0):
        return self.sent[-1][1]["reply_markup"]["inline_keyboard"][0][index]["callback_data"]

    def test_first_round_names_and_no_behavior(self):
        buttons = [b for row in first_round(self.event, self.pets)["inline_keyboard"] for b in row]
        self.assertEqual([b["text"] for b in buttons], ["是嗨啵", "是Saki", "兩隻都在", "不是貓咪"])
        self.assertTrue(all(len(b["callback_data"].encode()) <= 64 for b in buttons))

    def test_both_sequential_and_separate_persistent_labels(self):
        self.handle(self.choice("both"))
        self.assertIn("嗨啵的行為", self.sent[-1][0])
        first_callback, first_message = self.callback(), self.sent[-1][2]
        self.handle(first_callback, 2, first_message)
        self.assertIn("Saki的行為", self.sent[-1][0])
        second_callback, second_message = self.callback(1), self.sent[-1][2]
        # New worker uses durable session, not an in-memory conversation flag.
        self.wizard = PetFeedbackWizard(self.store, self.notifier, lambda: self.pets)
        self.handle(second_callback, 3, second_message)
        self.assertIn("嗨啵：進食", self.sent[-1][0])
        self.assertIn("Saki：喝水", self.sent[-1][0])
        rows = self.store.connection.execute("SELECT pet_id,behavior FROM telegram_pet_review_history ORDER BY update_id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("first", "eating"), ("second", "drinking")])
        with self.assertRaises(ValueError):
            self.handle(first_callback, 4, first_message)

    def test_single_and_negative_no_followup(self):
        self.handle(self.choice(pet_token("second")))
        self.assertIn("Saki的行為", self.sent[-1][0])
        options = str(self.sent[-1][1])
        self.assertNotIn("不在畫面", options)
        self.assertNotIn("無法判斷", options)
        self.handle(self.choice("no"), 2)
        self.assertEqual(len(self.sent), 1)
        state = self.store.connection.execute("SELECT state_json FROM telegram_pet_reviews ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertFalse(json.loads(state)["active"])

    def test_walking_and_running_use_motion_dimension_and_separate_pets(self):
        self.handle(self.choice("both"))
        choices = [b for row in self.sent[-1][1]["reply_markup"]["inline_keyboard"] for b in row]
        walking = next(b["callback_data"] for b in choices if b["text"] == "行走")
        self.assertTrue(all(len(b["callback_data"].encode()) <= 64 for b in choices))
        self.handle(walking, 2, self.sent[-1][2])
        choices = [b for row in self.sent[-1][1]["reply_markup"]["inline_keyboard"] for b in row]
        running = next(b["callback_data"] for b in choices if b["text"] == "奔跑")
        self.handle(running, 3, self.sent[-1][2])
        self.assertIn("嗨啵：行走", self.sent[-1][0])
        self.assertIn("Saki：奔跑", self.sent[-1][0])
        self.store.initialize()
        rows = self.store.connection.execute("SELECT pet_id,behavior,motion FROM telegram_pet_review_history ORDER BY update_id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("first", "other_active", "walking"), ("second", "other_active", "running")])

    def test_receipt_and_reviewer_checks(self):
        with self.assertRaises(ValueError):
            self.handle(self.choice("both"), message=999)
        self.handle(self.choice("both"), update=2)
        callback, message = self.callback(), self.sent[-1][2]
        with self.assertRaises(ValueError):
            self.handle(callback, 3, message, hashlib.sha256(b"other").hexdigest())
        with self.assertRaises(ValueError):
            self.handle(callback, 3, 999)

    def test_duplicate_and_migration_replay(self):
        self.handle(self.choice("both"))
        self.handle(self.choice("both"))
        self.assertEqual(len(self.sent), 1)
        self.store.initialize()
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM telegram_pet_reviews").fetchone()[0], 1)

    def test_first_prompt_failure_can_resume(self):
        send = self.notifier.send_message
        self.notifier.send_message = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
        with self.assertRaises(RuntimeError):
            self.handle(self.choice("both"))
        self.notifier.send_message = send
        self.handle(self.choice("both"))
        self.assertIn("嗨啵的行為", self.sent[-1][0])

    def test_second_prompt_failure_retries_without_duplicate_label(self):
        self.handle(self.choice("both"))
        callback, message = self.callback(), self.sent[-1][2]
        send = self.notifier.send_message
        self.notifier.send_message = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
        with self.assertRaises(RuntimeError):
            self.handle(callback, 2, message)
        self.notifier.send_message = send
        self.handle(callback, 2, message)
        self.assertIn("Saki的行為", self.sent[-1][0])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM telegram_pet_review_history").fetchone()[0], 1)
