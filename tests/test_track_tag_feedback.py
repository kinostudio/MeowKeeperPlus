"""逐軌跡多選回報的重送、重啟、授權、提交與歷史。"""

import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from PIL import Image

from meowkeeper.core.behavior.models import BehaviorEvent
from meowkeeper.core.notifications.telegram import TelegramNotifier
from meowkeeper.core.notifications.track_tag_feedback import (
    TrackTagFeedback,
    identity_keyboard,
    revision_keyboard,
    tag_keyboard,
)
from meowkeeper.domain.behavior import BehaviorState, MotionState
from meowkeeper.storage.database import SCHEMA_VERSION, SQLiteStore


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class TrackTagFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.addCleanup(self.store.close)
        self.event = BehaviorEvent(None, "session:1", BehaviorState.RESTING, MotionState.STATIONARY,
                                   (), datetime.now(timezone.utc), None, .8, None, "rule", "test", "test", "safe", None)
        self.store.save_behavior_event(self.event)
        self.event_id = self.event.event_id
        self.store.record_behavior_event_telegram_receipt(self.event_id, digest("123"), 10)
        self.store.record_behavior_event_annotation(event_id=self.event_id, recipient_ref=digest("123"),
            reviewer_ref=digest("user:123"), message_id=10, dimension="identity", label="pet-a",
            update_id=1, callback_ref=digest("identity-1"))
        self.sent, self.edits = [], []

        def send(chat, text, **kwargs):
            message = 100 + len(self.sent)
            self.sent.append((chat, text, kwargs, message))
            return SimpleNamespace(message_id=message)

        self.notifier = SimpleNamespace(send_message=send,
            edit_message_reply_markup=lambda chat, message, **kwargs: self.edits.append((chat, message, kwargs)),
            edit_message_text=lambda chat, message, text, **kwargs: self.edits.append((chat, message, {**kwargs, "text": text})))
        self.service = TrackTagFeedback(self.store, self.notifier)
        self.pets = [SimpleNamespace(pet_id="pet-a", display_name="測試貓")]
        self.service.open("123", self.event_id, self.pets)
        self.review = self.store.connection.execute("SELECT review_id FROM telegram_track_tag_drafts").fetchone()[0]

    def select(self, tag, update, *, chat="123", message=100, review=None):
        return self.service.handle(f"tags:{review or self.review}:{tag}", chat_id=chat,
            reviewer_ref=digest(f"user:{chat}"), message_id=message, update_id=update, callback_ref=digest(str(update)))

    def test_multiple_actions_toggle_and_explicit_submission(self):
        self.select("walking", 2)
        self.select("playing", 3)
        self.select("walking", 4)
        self.select("running", 5)
        draft = self.store.connection.execute("SELECT selected_json,state FROM telegram_track_tag_drafts").fetchone()
        self.assertEqual((json.loads(draft[0]), draft[1]), (["running", "playing"], "editing"))
        self.assertIn("第三步", self.select("done", 6))
        self.assertEqual(self.store.connection.execute("SELECT state FROM telegram_track_tag_drafts").fetchone()[0], "confirming")
        self.assertIn("已提交", self.select("done", 7))
        self.assertEqual(self.store.connection.execute("SELECT state FROM telegram_track_tag_drafts").fetchone()[0], "submitted")
        self.assertEqual(self.edits[-1][2]["reply_markup"], {"inline_keyboard": []})
        with self.assertRaises(ValueError):
            self.select("grooming", 8)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM telegram_track_tag_history").fetchone()[0], 6)

    def test_retry_after_restart_does_not_toggle_twice(self):
        self.select("walking", 2)
        self.service = TrackTagFeedback(self.store, self.notifier)
        self.store.initialize()
        self.select("walking", 2)
        row = self.store.connection.execute("SELECT selected_json FROM telegram_track_tag_drafts").fetchone()
        self.assertEqual(json.loads(row[0]), ["walking"])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM telegram_track_tag_history").fetchone()[0], 1)

    def test_wrong_user_message_receipt_and_stale_menu_are_rejected(self):
        for values in ({"chat": "456"}, {"message": 999}, {"review": str(uuid4())}):
            with self.assertRaises(ValueError):
                self.select("walking", 2, **values)
        self.service.open("123", self.event_id, self.pets)
        with self.assertRaises(ValueError):
            self.select("walking", 3)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM telegram_track_tag_history").fetchone()[0], 0)

    def test_empty_submit_rejected_and_clear_preserves_history(self):
        with self.assertRaises(ValueError):
            self.select("done", 2)
        self.select("walking", 3)
        self.select("clear", 4)
        self.assertEqual(self.store.connection.execute("SELECT selected_json FROM telegram_track_tag_drafts").fetchone()[0], "[]")
        rows = self.store.connection.execute("SELECT operation,tags_json FROM telegram_track_tag_history ORDER BY update_id").fetchall()
        self.assertEqual([tuple(row) for row in rows], [("walking", '["walking"]'), ("clear", "[]")])

    def test_buttons_keep_checkmarks_and_fit_telegram_limit(self):
        self.select("walking", 2)
        buttons = [b for row in self.edits[-1][2]["reply_markup"]["inline_keyboard"] for b in row]
        self.assertTrue(any(b["text"] == "☑ 行走" for b in buttons))
        self.assertTrue(all(len(b["callback_data"].encode()) <= 64 for b in buttons))
        self.assertNotIn("不在畫面", str(buttons))
        self.assertIn("無法判斷", str(buttons))  # V4.3 正式規格要求未知且與其他動作互斥。

    def test_not_cat_correction_invalidates_draft_without_erasing_history(self):
        self.select("walking", 2)
        self.store.record_behavior_event_annotation(event_id=self.event_id, recipient_ref=digest("123"),
            reviewer_ref=digest("user:123"), message_id=100, dimension="cat_presence", label="not_cat",
            update_id=3, callback_ref=digest("not-cat"))
        with self.assertRaises(ValueError):
            self.select("done", 4)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM telegram_track_tag_history").fetchone()[0], 1)

    def test_visual_refresh_failure_keeps_committed_selection(self):
        self.notifier.edit_message_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
        self.assertIn("回報已保存", self.select("walking", 2))
        self.assertEqual(self.store.connection.execute("SELECT selected_json FROM telegram_track_tag_drafts").fetchone()[0], '["walking"]')

    def test_confirmation_back_cancel_unknown_and_identity_change(self):
        self.select("walking", 2)
        self.select("unknown", 3)
        self.assertEqual(json.loads(self.store.connection.execute("SELECT selected_json FROM telegram_track_tag_drafts").fetchone()[0]), ["unknown"])
        self.select("playing", 4)
        self.assertIn("第三步", self.select("review", 5))
        with self.assertRaises(ValueError):
            self.select("running", 6)
        self.select("back", 7)
        self.select("running", 8)
        self.assertIn("未提交", self.select("cancel", 9))
        with self.assertRaises(ValueError):
            self.select("done", 10)
        self.service.open("123", self.event_id, self.pets)
        self.review = self.store.connection.execute("SELECT review_id FROM telegram_track_tag_drafts WHERE state='editing'").fetchone()[0]
        self.store.connection.execute("UPDATE behavior_event_annotations SET label='different-pet' WHERE dimension='identity'")
        self.store.connection.commit()
        with self.assertRaises(ValueError):
            self.select("walking", 11, message=101)

    def test_expired_menu_has_no_submission_and_keeps_history(self):
        self.select("walking", 2)
        self.service.clock = lambda: datetime.now(timezone.utc) + timedelta(days=2)
        self.assertIn("已過期", self.select("done", 3))
        self.assertEqual(self.store.connection.execute("SELECT state FROM telegram_track_tag_drafts").fetchone()[0], "expired")
        self.assertEqual(self.edits[-1][2]["reply_markup"], {"inline_keyboard": []})

    def test_notifier_text_edit_preserves_keyboard_and_checks_message(self):
        notifier = TelegramNotifier.__new__(TelegramNotifier)
        notifier._call = Mock(return_value={"ok": True})
        markup = {"inline_keyboard": [[{"text": "確認", "callback_data": "opaque"}]]}
        notifier.edit_message_text("123", 100, "確認回報", reply_markup=markup)
        args = notifier._call.call_args
        self.assertEqual(args.args[0], "editMessageText")
        self.assertEqual(json.loads(args.kwargs["fields"]["reply_markup"]), markup)
        with self.assertRaises(ValueError):
            notifier.edit_message_text("123", True, "確認回報", reply_markup=markup)

    def test_identity_is_a_draft_until_confirmed_and_can_be_changed_or_cancelled(self):
        pets = [*self.pets, SimpleNamespace(pet_id="pet-b", display_name="另一隻貓")]
        self.service.open_identity("123", self.event_id, pets, origin_message_id=10)
        review = self.store.connection.execute("SELECT review_id FROM telegram_track_tag_drafts WHERE state='identity'").fetchone()[0]
        callbacks = {}

        def choose(operation, update):
            prefix = "who" if operation in {"other", "uncertain", "no", digest("pet-b")[:12], digest("pet-a")[:12]} else "tags"
            revision = self.store.connection.execute("SELECT callback_revision FROM telegram_track_tag_drafts WHERE review_id=?", (review,)).fetchone()[0]
            data = callbacks.setdefault(update, f"{prefix}:{review}:{operation}:{revision}")
            return self.service.handle(data, chat_id="123", reviewer_ref=digest("user:123"),
                message_id=101, update_id=update, callback_ref=digest(f"staged-{update}"), pets=pets)

        choose(digest("pet-b")[:12], 20)
        self.assertEqual(self.store.connection.execute("SELECT label FROM behavior_event_annotations WHERE dimension='identity'").fetchone()[0], "pet-a")
        choose("walking", 21)
        choose("review", 22)
        choose("identity_back", 23)
        choose(digest("pet-a")[:12], 24)
        self.assertEqual(self.store.connection.execute("SELECT selected_json FROM telegram_track_tag_drafts WHERE review_id=?", (review,)).fetchone()[0], "[]")
        choose("unknown", 25)
        choose("review", 26)
        choose("done", 27)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM behavior_event_annotation_history").fetchone()[0], 2)
        self.assertEqual(self.store.connection.execute(
            "SELECT learning_consent FROM behavior_event_annotations WHERE dimension='identity'"
        ).fetchone()[0], 1)
        choose("done", 27)  # 同一回條重送，不新增人工修訂。
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM behavior_event_annotation_history").fetchone()[0], 2)

        self.service.open_identity("123", self.event_id, pets, origin_message_id=10)
        row = self.store.connection.execute("SELECT review_id,message_id FROM telegram_track_tag_drafts WHERE state='identity'").fetchone()
        result = self.service.handle(f"tags:{row[0]}:cancel:0", chat_id="123", reviewer_ref=digest("user:123"),
            message_id=row[1], update_id=28, callback_ref=digest("cancel-first"), pets=pets)
        self.assertIn("未提交", result)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM behavior_event_annotation_history").fetchone()[0], 2)

    def test_not_cat_skips_actions_but_requires_confirmation(self):
        self.service.open_identity("123", self.event_id, self.pets, origin_message_id=10)
        row = self.store.connection.execute("SELECT review_id,message_id FROM telegram_track_tag_drafts WHERE state='identity'").fetchone()
        for operation, prefix, update in (("no", "who", 20), ("done", "tags", 21)):
            text = self.service.handle(f"{prefix}:{row[0]}:{operation}:{update - 20}", chat_id="123", reviewer_ref=digest("user:123"),
                message_id=row[1], update_id=update, callback_ref=digest(f"negative-{update}"), pets=self.pets)
            if operation == "no":
                self.assertIn("不詢問動作", text)
                self.assertEqual(self.store.connection.execute("SELECT label FROM behavior_event_annotations WHERE dimension='cat_presence'").fetchone()[0], "cat")
                with self.assertRaisesRegex(ValueError, "目前步驟"):
                    self.service.handle(f"tags:{row[0]}:back:1", chat_id="123", reviewer_ref=digest("user:123"),
                        message_id=row[1], update_id=22, callback_ref=digest("forged-negative-back"), pets=self.pets)
        self.assertEqual(self.store.connection.execute("SELECT label FROM behavior_event_annotations WHERE dimension='cat_presence'").fetchone()[0], "not_cat")

    def test_first_identity_ttl_migration_and_external_revision_conflict(self):
        self.service.open_identity("123", self.event_id, self.pets, origin_message_id=10)
        row = self.store.connection.execute("SELECT review_id,message_id FROM telegram_track_tag_drafts WHERE state='identity'").fetchone()
        self.store.initialize()
        self.assertEqual(self.store.connection.execute("SELECT state FROM telegram_track_tag_drafts WHERE review_id=?", (row[0],)).fetchone()[0], "identity")
        self.service.clock = lambda: datetime.now(timezone.utc) + timedelta(days=2)
        result = self.service.handle(f"who:{row[0]}:other:0", chat_id="123", reviewer_ref=digest("user:123"),
            message_id=row[1], update_id=20, callback_ref=digest("expired-first"), pets=self.pets)
        self.assertIn("已過期", result)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM behavior_event_annotation_history").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            self.service.open_identity("123", self.event_id, self.pets, origin_message_id=10)

    def test_new_identity_review_rejects_intervening_annotation_revision(self):
        self.service.open_identity("123", self.event_id, self.pets, origin_message_id=10)
        row = self.store.connection.execute("SELECT review_id,message_id FROM telegram_track_tag_drafts WHERE state='identity'").fetchone()
        self.store.record_behavior_event_annotation(event_id=self.event_id, recipient_ref=digest("123"),
            reviewer_ref=digest("user:123"), message_id=10, dimension="identity", label="unknown",
            update_id=20, callback_ref=digest("external-revision"))
        with self.assertRaisesRegex(ValueError, "新的身分修訂"):
            self.service.handle(f"who:{row[0]}:other:0", chat_id="123", reviewer_ref=digest("user:123"),
                message_id=row[1], update_id=21, callback_ref=digest("stale-first"), pets=self.pets)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM telegram_track_tag_history").fetchone()[0], 0)

    def staged_review(self):
        self.service.open_identity("123", self.event_id, self.pets, origin_message_id=10)
        return self.store.connection.execute("SELECT review_id,message_id FROM telegram_track_tag_drafts WHERE state='identity'").fetchone()

    def staged_click(self, review, data, update, service=None):
        return (service or self.service).handle(data, chat_id="123", reviewer_ref=digest("user:123"),
            message_id=review[1], update_id=update, callback_ref=digest(f"version-{update}"), pets=self.pets)

    def test_stale_double_click_refreshes_without_toggling_and_retry_survives_restart(self):
        row = self.staged_review()
        self.staged_click(row, f"who:{row[0]}:other:0", 30)
        data = f"tags:{row[0]}:walking:1"
        self.staged_click(row, data, 31)
        self.assertIn("這次點擊未變更", self.staged_click(row, data, 32))
        self.service = TrackTagFeedback(self.store, self.notifier)
        self.store.initialize()
        self.staged_click(row, data, 31)
        draft = self.store.connection.execute("SELECT selected_json,callback_revision FROM telegram_track_tag_drafts WHERE review_id=?", (row[0],)).fetchone()
        self.assertEqual(tuple(draft), ('["walking"]', 2))
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM telegram_track_tag_history").fetchone()[0], 2)
        self.assertIn("☑ 行走", str(self.edits[-1]))
        self.assertIn(f"tags:{row[0]}:walking:2", str(self.edits[-1]))
        with self.assertRaisesRegex(ValueError, "重試識別"):
            self.staged_click(row, f"tags:{row[0]}:walking:2", 31)

    def test_stale_identity_and_confirmation_cannot_overwrite_or_submit(self):
        row = self.staged_review()
        for update, data in enumerate((f"who:{row[0]}:other:0", f"tags:{row[0]}:walking:1",
                                      f"tags:{row[0]}:review:2", f"tags:{row[0]}:back:3"), 30):
            self.staged_click(row, data, update)
        for update, data in enumerate((f"who:{row[0]}:no:0", f"tags:{row[0]}:done:3"), 40):
            self.assertIn("這次點擊未變更", self.staged_click(row, data, update))
        draft = self.store.connection.execute("SELECT identity_snapshot,state,callback_revision FROM telegram_track_tag_drafts WHERE review_id=?", (row[0],)).fetchone()
        self.assertEqual(tuple(draft), ("unknown", "editing", 4))
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM behavior_event_annotation_history").fetchone()[0], 1)

    def test_unversioned_staged_menu_refreshes_and_schema22_history_is_preserved(self):
        self.select("walking", 2)  # 既有非 staged 回報仍相容。
        row = self.staged_review()
        self.store.connection.execute("ALTER TABLE telegram_track_tag_drafts DROP COLUMN callback_revision")
        self.store.connection.execute("ALTER TABLE telegram_track_tag_history DROP COLUMN callback_revision")
        self.store.connection.execute("PRAGMA user_version=22")
        self.store.connection.execute("UPDATE schema_meta SET value='22' WHERE key='schema_version'")
        self.store.connection.commit()
        self.store.initialize()
        self.store.initialize()
        text = self.staged_click(row, f"who:{row[0]}:other", 30)
        self.assertIn("這次點擊未變更", text)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM telegram_track_tag_history").fetchone()[0], 1)
        self.assertEqual(self.store.schema_version, SCHEMA_VERSION)
        self.assertIn(f"who:{row[0]}:other:0", str(self.edits[-1]))

    def test_two_database_connections_accept_only_one_same_revision_mutation(self):
        row = self.staged_review()
        self.staged_click(row, f"who:{row[0]}:other:0", 30)
        with tempfile.TemporaryDirectory() as temporary:
            first = SQLiteStore(Path(temporary) / "events.db")
            self.store.connection.backup(first.connection)
            second = SQLiteStore(Path(temporary) / "events.db")
            try:
                barrier = Barrier(2)
                def click(store, update):
                    barrier.wait(timeout=5)
                    return self.staged_click(row, f"tags:{row[0]}:walking:1", update,
                                             TrackTagFeedback(store, self.notifier))
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(click, store, update) for store, update in ((first, 31), (second, 32))]
                    results = [future.result(timeout=10) for future in futures]
                self.assertEqual(sum("這次點擊未變更" in text for text in results), 1)
                self.assertEqual(first.connection.execute("SELECT COUNT(*) FROM telegram_track_tag_history").fetchone()[0], 2)
                self.assertEqual(first.connection.execute("SELECT selected_json FROM telegram_track_tag_drafts WHERE review_id=?", (row[0],)).fetchone()[0], '["walking"]')
            finally:
                second.close()
                first.close()

    def test_maximum_revision_buttons_fit_and_exhaustion_expires(self):
        for markup in (identity_keyboard(self.review, self.pets), tag_keyboard(self.review, []),
                       {"inline_keyboard": [[{"callback_data": f"tags:{self.review}:identity_back"}]]}):
            revision_keyboard(markup, 999999)
            self.assertTrue(all(len(button["callback_data"].encode()) <= 64
                                for buttons in markup["inline_keyboard"] for button in buttons))
        row = self.staged_review()
        self.store.connection.execute("UPDATE telegram_track_tag_drafts SET callback_revision=999999 WHERE review_id=?", (row[0],))
        self.store.connection.commit()
        self.assertIn("已過期", self.staged_click(row, f"who:{row[0]}:other:999999", 30))
        self.assertEqual(self.edits[-1][2]["reply_markup"], {"inline_keyboard": []})

    def test_next_track_uses_original_video_receipt_and_repeated_navigation_opens_once(self):
        second = BehaviorEvent(None,"session:2",BehaviorState.RESTING,MotionState.STATIONARY,(),
                               self.event.started_at,None,.8,None,"rule","test","test","safe",None)
        self.store.save_behavior_event(second)
        media = str(uuid4())
        self.store.create_media_event(event_id=media,event_type="cat_detected",trigger_at=self.event.started_at,
            source_id="safe",track_id=self.event.track_id,pet_id=None,confidence=.8,requested_pre_seconds=5,requested_post_seconds=10)
        self.store._write("INSERT OR IGNORE INTO media_event_tracks(event_id,track_id) VALUES(?,?)",(media,second.track_id))
        self.store.record_telegram_receipt(media,digest("123"),"video",10)
        row = self.staged_review()
        for update,data in enumerate((f"who:{row[0]}:other:0",f"tags:{row[0]}:walking:1",f"tags:{row[0]}:review:2",f"tags:{row[0]}:done:3"),30):
            self.staged_click(row,data,update)
        self.assertIn(f"next:{row[0]}",str(self.edits[-1]))
        for update in (40,41):
            self.assertTrue(self.service.open_next(row[0],chat_id="123",reviewer_ref=digest("user:123"),message_id=row[1],update_id=update,pets=self.pets))
        self.assertEqual(len(self.sent),3)  # 舊選單、目前回報、下一條回報；不重傳原影片。
        draft = self.store.connection.execute("SELECT event_id,state,origin_message_id FROM telegram_track_tag_drafts WHERE state='identity'").fetchone()
        self.assertEqual(tuple(draft),(second.event_id,"identity",10))
        with self.assertRaises(ValueError):
            self.service.open_next(row[0],chat_id="456",reviewer_ref=digest("user:456"),message_id=row[1],update_id=42,pets=self.pets)

    def test_verified_track_crop_uses_photo_and_edits_caption_without_resending(self):
        crop = BytesIO()
        Image.new("RGB",(128,128),"orange").save(crop,format="JPEG")
        jpeg = crop.getvalue()
        self.store._write("INSERT INTO identity_feedback_evidence(event_id,crop_sha256,crop_jpeg,captured_at) VALUES(?,?,?,?)",(self.event_id,hashlib.sha256(jpeg).hexdigest(),jpeg,self.event.started_at.isoformat()))
        media = str(uuid4())
        self.store.create_media_event(event_id=media,event_type="cat_detected",trigger_at=self.event.started_at,
            source_id="safe",track_id=self.event.track_id,pet_id=None,confidence=.8,requested_pre_seconds=5,requested_post_seconds=10)
        self.store.record_telegram_receipt(media,digest("123"),"video",10)
        self.store._write("INSERT INTO telegram_media_track_refs(media_event_id,recipient_ref,message_id,behavior_event_id,ordinal) VALUES(?,?,?,?,1)",(media,digest("123"),10,self.event_id))
        self.notifier.send_photo = Mock(return_value=SimpleNamespace(message_id=101))
        self.notifier.edit_message_caption = Mock()
        self.service.open_identity("123",self.event_id,self.pets,origin_message_id=10)
        self.assertIn("原影片按鈕 #1",self.notifier.send_photo.call_args.kwargs["caption"])
        row = self.store.connection.execute("SELECT review_id,message_id,prompt_media_kind FROM telegram_track_tag_drafts WHERE state='identity'").fetchone()
        self.assertEqual(row[2],"photo")
        self.staged_click(row,f"who:{row[0]}:other:0",40)
        self.assertEqual(self.notifier.send_photo.call_count,1)
        self.notifier.edit_message_caption.assert_called_once()
        self.assertEqual(self.edits,[])


if __name__ == "__main__":
    unittest.main()
