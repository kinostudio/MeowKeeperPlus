"""單軌跡動作多選：持久化草稿、明確提交及不可覆寫歷史。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from meowkeeper.core.event_semantics import TAG_LABELS
from meowkeeper.storage.database import SQLiteStore

TAG_CALLBACK = re.compile(r"^tags:([0-9a-f-]{36}):([a-z_]{1,16})(?::([0-9]{1,6}))?$")
WHO_CALLBACK = re.compile(r"^who:([0-9a-f-]{36}):([a-z0-9]{1,12})(?::([0-9]{1,6}))?$")
NEXT_CALLBACK = re.compile(r"^next:([0-9a-f-]{36})$")
FEEDBACK_TAGS = {**TAG_LABELS, "unknown": "無法判斷"}


def revision_keyboard(markup: dict, revision: int | None) -> dict:
    """僅替新草稿按鈕加版本；已送達的舊式回報維持相容。"""
    if revision is None:
        return markup
    if type(revision) is not int or not 0 <= revision <= 999999:
        raise ValueError("回報選單版本不正確。")
    for row in markup["inline_keyboard"]:
        for button in row:
            if button["callback_data"].startswith(("tags:", "who:")):
                button["callback_data"] += f":{revision}"
                if len(button["callback_data"].encode()) > 64:
                    raise ValueError("回報按鈕超過可用長度。")
    return markup


def identity_keyboard(review_id: str, pets: list) -> dict:
    rows = [[{"text": pet.display_name[:24], "callback_data":
              f"who:{review_id}:{hashlib.sha256(pet.pet_id.encode()).hexdigest()[:12]}"}] for pet in pets[:8]]
    rows.extend([[{"text": "其他貓", "callback_data": f"who:{review_id}:other"},
                  {"text": "不是貓", "callback_data": f"who:{review_id}:no"}],
                 [{"text": "無法確認", "callback_data": f"who:{review_id}:uncertain"}],
                 [{"text": "取消回報", "callback_data": f"tags:{review_id}:cancel"}]])
    return {"inline_keyboard": rows}


def tag_keyboard(review_id: str, selected: list[str], *, event_id: str | None = None) -> dict:
    choices = list(FEEDBACK_TAGS.items())
    rows = [[{"text": ("☑ " if key in selected else "☐ ") + name,
              "callback_data": f"tags:{review_id}:{key}"}
             for key, name in choices[index:index + 2]] for index in range(0, len(choices), 2)]
    rows.append([{"text": f"下一步：確認已選 {len(selected)} 項", "callback_data": f"tags:{review_id}:review"},
                 {"text": "清除選取", "callback_data": f"tags:{review_id}:clear"}])
    rows.append([{"text": "取消這次動作回報", "callback_data": f"tags:{review_id}:cancel"}])
    return {"inline_keyboard": rows}


class TrackTagFeedback:
    def __init__(self, store: SQLiteStore, notifier, *, clock=None, ttl_seconds=86400) -> None:
        if not 60 <= ttl_seconds <= 86400:
            raise ValueError("回報有效期間不正確。")
        self.store, self.notifier = store, notifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ttl_seconds = ttl_seconds

    def _save(self, operation):
        def guarded():
            try:
                self.store.connection.execute("BEGIN IMMEDIATE")
                return operation()
            except Exception:
                self.store.connection.rollback()
                raise
        return self.store._transaction(guarded)

    def open(self, chat_id: str, event_id: str, pets: list) -> bool:
        recipient = hashlib.sha256(chat_id.encode()).hexdigest()
        reviewer = hashlib.sha256(f"user:{chat_id}".encode()).hexdigest()
        with self.store._connection_lock:
            identity = self.store.connection.execute(
                "SELECT label FROM behavior_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension='identity'",
                (event_id, reviewer),
            ).fetchone()
            cat = self.store.connection.execute(
                "SELECT label FROM behavior_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension='cat_presence'",
                (event_id, reviewer),
            ).fetchone()
        if identity is None or cat is None or cat[0] != "cat":
            raise ValueError("請先選擇這條軌跡的貓咪身分。")
        names = {pet.pet_id: pet.display_name for pet in pets}
        name = names.get(identity[0], "未確認身分的貓咪")
        review_id = str(uuid4())
        learning = event_id if identity[0] in names else None
        receipt = self.notifier.send_message(
            chat_id, f"第二步：{name}的動作（可多選）\n"
            "點選會勾選，再點一次取消；選完按下一步，再確認提交。\n"
            "例如可以同時選行走與玩耍。這份回報只對應目前這條軌跡。",
            reply_markup=tag_keyboard(review_id, [], event_id=learning),
        )
        if type(receipt.message_id) is not int or receipt.message_id <= 0:
            raise ValueError("動作選單缺少送達回條。")

        def save():
            self.store.connection.execute(
                "UPDATE telegram_track_tag_drafts SET state='superseded' WHERE event_id=? AND recipient_ref=? AND reviewer_ref=? AND state IN ('editing','confirming')",
                (event_id, recipient, reviewer),
            )
            self.store.connection.execute(
                "INSERT INTO telegram_track_tag_drafts(review_id,event_id,recipient_ref,reviewer_ref,message_id,identity_snapshot,name_snapshot,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (review_id, event_id, recipient, reviewer, receipt.message_id, identity[0], name, self.clock().isoformat()),
            )
            self.store.connection.execute(
                "INSERT OR IGNORE INTO behavior_event_telegram_receipts(event_id,recipient_ref,message_id) VALUES(?,?,?)",
                (event_id, recipient, receipt.message_id),
            )
        self._save(save)
        return True

    def _identity_baseline(self, event_id, reviewer_ref):
        rows = self.store.connection.execute(
            "SELECT dimension,label,updated_at FROM behavior_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension IN ('identity','cat_presence') ORDER BY dimension",
            (event_id, reviewer_ref),
        ).fetchall()
        revision = self.store.connection.execute(
            "SELECT MAX(update_id) FROM behavior_event_annotation_history WHERE event_id=? AND reviewer_ref=? AND dimension IN ('identity','cat_presence')",
            (event_id, reviewer_ref),
        ).fetchone()[0]
        return hashlib.sha256(json.dumps([[tuple(row) for row in rows], revision]).encode()).hexdigest()

    def _next_track(self, draft):
        if not draft["origin_message_id"]:
            return None
        parents = self.store.connection.execute("SELECT event_id FROM telegram_receipts WHERE recipient_ref=? AND message_id=? AND media_kind IN ('video','photo')", (draft["recipient_ref"], draft["origin_message_id"])).fetchall()
        if len(parents) != 1:
            return None
        rows = self.store.connection.execute("SELECT behavior_event_id AS event_id FROM telegram_media_track_refs WHERE media_event_id=? AND recipient_ref=? AND message_id=? ORDER BY ordinal",(parents[0][0],draft["recipient_ref"],draft["origin_message_id"])).fetchall()
        if not rows:
            rows = self.store.media_feedback_tracks(parents[0][0])
        indices = [index for index, row in enumerate(rows) if row["event_id"] == draft["event_id"]]
        if len(indices) != 1 or indices[0]+1 >= len(rows):
            return None
        return rows[indices[0]+1]["event_id"]

    def _prompt_crop(self, event_id, recipient, origin_message_id):
        from PIL import Image, ImageDraw
        with self.store._connection_lock:
            row = self.store.connection.execute("SELECT i.crop_sha256,i.crop_jpeg,i.captured_at,b.started_at,b.ended_at FROM identity_feedback_evidence i JOIN behavior_events b ON b.event_id=i.event_id WHERE i.event_id=?",(event_id,)).fetchone()
            mapping = self.store.connection.execute("SELECT r.ordinal,e.trigger_at,e.requested_pre_seconds,e.requested_post_seconds FROM telegram_media_track_refs r JOIN media_events e ON e.event_id=r.media_event_id WHERE r.behavior_event_id=? AND r.recipient_ref=? AND r.message_id=?",(event_id,recipient,origin_message_id)).fetchall()
        if row is None or not 0 < len(row[1]) <= 1024*1024 or hashlib.sha256(row[1]).hexdigest() != row[0]:
            return None, None
        captured = datetime.fromisoformat(row[2].replace("Z","+00:00"))
        started = datetime.fromisoformat(row[3].replace("Z","+00:00"))
        if captured.tzinfo is None or started.tzinfo is None or captured < started:
            return None, None
        ordinal = None
        if len(mapping) == 1:
            trigger = datetime.fromisoformat(mapping[0][1].replace("Z","+00:00"))
            offset = (captured-trigger).total_seconds()
            if not -mapping[0][2] <= offset <= mapping[0][3]:
                return None, None
            ordinal = mapping[0][0]
        if row[4] is not None and captured > datetime.fromisoformat(row[4].replace("Z","+00:00")):
            return None, None
        with Image.open(BytesIO(row[1])) as image:
            if min(image.size) < 48 or image.width*image.height > 4_000_000:
                return None, None
            image = image.convert("RGB")
            image.thumbnail((512,480))
            draw = ImageDraw.Draw(image)
            draw.rectangle((0,0,min(image.width,220),22),fill="#2d2823")
            draw.text((6,5),f"Track #{ordinal}" if ordinal else "This track crop",fill="white")
            output = BytesIO()
            image.save(output,format="JPEG",quality=85)
        return output.getvalue(), ordinal

    def open_next(self, review_id, *, chat_id, reviewer_ref, message_id, update_id, pets):
        recipient = hashlib.sha256(chat_id.encode()).hexdigest()
        self.store._validate_opaque_ref(reviewer_ref)
        if type(update_id) is not int or update_id < 0:
            raise ValueError("回報識別不正確。")
        def reserve():
            row = self.store.connection.execute("SELECT * FROM telegram_track_tag_drafts WHERE review_id=? AND recipient_ref=? AND reviewer_ref=? AND message_id=? AND state='submitted' AND staged_identity=1", (review_id, recipient, reviewer_ref, message_id)).fetchone()
            if row is None:
                raise ValueError("請先完成目前軌跡回報。")
            created = datetime.fromisoformat(row["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if not 0 <= (self.clock()-created).total_seconds() <= self.ttl_seconds:
                raise ValueError("原通知已過期，請到Dashboard回報。")
            next_event = self._next_track(row)
            if next_event is None:
                raise ValueError("這段影片沒有下一條可安全對應的軌跡。")
            nav_update = row["navigation_update_id"] if row["navigation_update_id"] is not None else update_id
            self.store.connection.execute("UPDATE telegram_track_tag_drafts SET navigation_update_id=? WHERE review_id=?", (nav_update, review_id))
            return next_event, row["origin_message_id"], nav_update
        next_event, origin_message, nav_update = self._save(reserve)
        if not self.store.bind_media_track_receipt(next_event, recipient, origin_message):
            raise ValueError("下一條軌跡與原影片回條不符。")
        return self.open_identity(chat_id, next_event, pets, origin_message_id=origin_message, origin_update_id=nav_update)

    def open_identity(self, chat_id: str, event_id: str, pets: list, *, origin_message_id: int,
                      origin_update_id: int | None = None) -> bool:
        recipient = hashlib.sha256(chat_id.encode()).hexdigest()
        reviewer = hashlib.sha256(f"user:{chat_id}".encode()).hexdigest()
        if origin_update_id is not None and (type(origin_update_id) is not int or origin_update_id < 0):
            raise ValueError("原通知回報識別不正確。")
        with self.store._connection_lock:
            if origin_update_id is not None:
                previous = self.store.connection.execute(
                    "SELECT event_id,recipient_ref,reviewer_ref,origin_message_id FROM telegram_track_tag_drafts WHERE origin_update_id=?",
                    (origin_update_id,),
                ).fetchone()
                if previous is not None:
                    if tuple(previous) != (event_id, recipient, reviewer, origin_message_id):
                        raise ValueError("回報識別已用於其他訊息。")
                    return True  # 重送同一個更新不重開選單，也不延長 TTL。
            row = self.store.connection.execute(
                "SELECT received_at FROM behavior_event_telegram_receipts WHERE event_id=? AND recipient_ref=? AND message_id=?",
                (event_id, recipient, origin_message_id),
            ).fetchone()
            if row is None:
                raise ValueError("回報與原影片收件回條不符。")
            created = datetime.fromisoformat(row[0])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if not 0 <= (self.clock() - created).total_seconds() <= self.ttl_seconds:
                raise ValueError("原通知已過期，請到 Dashboard 回報。")
            baseline = self._identity_baseline(event_id, reviewer)
        review_id = str(uuid4())
        markup = revision_keyboard(identity_keyboard(review_id,pets),0)
        try:
            crop, ordinal = self._prompt_crop(event_id,recipient,origin_message_id)
        except (OSError,ValueError,TypeError):
            crop, ordinal = None, None
        prompt_kind = "text"
        if crop and callable(getattr(self.notifier,"send_photo",None)):
            receipt = self.notifier.send_photo(chat_id,crop,caption=(f"第一步：{'原影片按鈕 #'+str(ordinal) if ordinal else '這條軌跡'}是哪隻貓？\n此圖是當次單貓裁切，不代表整段動作；最後確認提交才保存。"),reply_markup=markup)
            prompt_kind = "photo"
        else:
            receipt = self.notifier.send_message(chat_id,"第一步：這條軌跡是哪隻貓？\n本次沒有可核對的單貓裁切，無法對應時請選無法確認，不要猜測。最後確認提交才保存。",reply_markup=markup)
        if type(receipt.message_id) is not int or receipt.message_id <= 0:
            raise ValueError("身分選單缺少送達回條。")

        def save():
            self.store.connection.execute(
                "UPDATE telegram_track_tag_drafts SET state='superseded' WHERE event_id=? AND recipient_ref=? AND reviewer_ref=? AND state IN ('identity','editing','confirming')",
                (event_id, recipient, reviewer),
            )
            self.store.connection.execute(
                "INSERT INTO telegram_track_tag_drafts(review_id,event_id,recipient_ref,reviewer_ref,message_id,state,staged_identity,identity_baseline,created_at,origin_update_id,origin_message_id,prompt_media_kind) VALUES(?,?,?,?,?,'identity',1,?,?,?,?,?)",
                (review_id, event_id, recipient, reviewer, receipt.message_id, baseline, created.isoformat(), origin_update_id, origin_message_id,prompt_kind),
            )
            self.store.connection.execute(
                "INSERT OR IGNORE INTO behavior_event_telegram_receipts(event_id,recipient_ref,message_id) VALUES(?,?,?)",
                (event_id, recipient, receipt.message_id),
            )
        self._save(save)
        return True

    def handle(self, data: str, *, chat_id: str, reviewer_ref: str, message_id: int,
               update_id: int, callback_ref: str, pets: list | None = None) -> str:
        who = WHO_CALLBACK.fullmatch(data)
        match = who or TAG_CALLBACK.fullmatch(data)
        if match is None:
            raise ValueError("動作回報格式不正確。")
        review_id, operation, revision_text = match.groups()
        incoming_revision = int(revision_text) if revision_text is not None else None
        identity_choice = None
        if who:
            matches = [pet for pet in (pets or [])[:8]
                       if hashlib.sha256(pet.pet_id.encode()).hexdigest()[:12] == operation]
            if operation in {"other", "uncertain", "no"}:
                identity_choice = ("not_cat" if operation == "no" else "unknown",
                                   {"other": "其他貓", "uncertain": "無法確認身分", "no": "不是貓"}[operation])
            elif len(matches) == 1:
                identity_choice = (matches[0].pet_id, matches[0].display_name)
            else:
                raise ValueError("寵物選項已更改，請重新開啟回報。")
            operation = "identity_" + operation
        elif operation not in FEEDBACK_TAGS and operation not in {"clear", "done", "review", "back", "cancel", "identity_back"}:
            raise ValueError("動作選項已失效。")
        recipient = hashlib.sha256(chat_id.encode()).hexdigest()
        for reference in (recipient, reviewer_ref, callback_ref):
            self.store._validate_opaque_ref(reference)
        if type(update_id) is not int or update_id < 0:
            raise ValueError("回報識別格式不正確。")

        def save():
            row = self.store.connection.execute(
                "SELECT * FROM telegram_track_tag_drafts WHERE review_id=? AND recipient_ref=? AND reviewer_ref=? AND message_id=?",
                (review_id, recipient, reviewer_ref, message_id),
            ).fetchone()
            if row is None:
                raise ValueError("動作回報與收件者或訊息不符。")
            event_id = row["event_id"]
            receipt = self.store.connection.execute(
                "SELECT 1 FROM behavior_event_telegram_receipts WHERE event_id=? AND recipient_ref=? AND message_id=?",
                (event_id, recipient, message_id),
            ).fetchone()
            cat = self.store.connection.execute(
                "SELECT label FROM behavior_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension='cat_presence'",
                (event_id, reviewer_ref),
            ).fetchone()
            staged = row["staged_identity"] == 1
            if receipt is None or (not staged and (cat is None or cat[0] != "cat")):
                raise ValueError("此軌跡已不是確認的貓咪回報。")
            identity = self.store.connection.execute(
                "SELECT label FROM behavior_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension='identity'",
                (event_id, reviewer_ref),
            ).fetchone()
            if not staged and (identity is None or identity[0] != row["identity_snapshot"]):
                raise ValueError("貓咪身分已更改，請重新開啟這條軌跡的回報。")
            duplicates = self.store.connection.execute(
                "SELECT review_id,operation,callback_revision FROM telegram_track_tag_history WHERE update_id=? OR callback_ref=?",
                (update_id, callback_ref),
            ).fetchall()
            selected = json.loads(row["selected_json"])
            if not isinstance(selected, list) or any(not isinstance(tag, str) or tag not in FEEDBACK_TAGS for tag in selected):
                raise ValueError("動作草稿內容無法驗證，請重新開啟回報。")
            created = datetime.fromisoformat(row["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            expired = not 0 <= (self.clock() - created).total_seconds() <= self.ttl_seconds
            if duplicates:
                if any(tuple(duplicate) != (review_id, operation, incoming_revision) for duplicate in duplicates):
                    raise ValueError("重試識別已用於其他回報。")
                state = row["state"]
                if expired and state in {"identity", "editing", "confirming"}:
                    state = "expired"
                    self.store.connection.execute("UPDATE telegram_track_tag_drafts SET state='expired' WHERE review_id=?", (review_id,))
                return event_id, False
            if staged and self._identity_baseline(event_id, reviewer_ref) != row["identity_baseline"]:
                raise ValueError("這條軌跡已有新的身分修訂，請重新開啟回報。")
            if row["state"] not in {"identity", "editing", "confirming"}:
                raise ValueError("這份動作回報已提交或被新的選單取代，請重新開啟回報。")
            state = row["state"]
            if staged and not expired and incoming_revision != row["callback_revision"]:
                # 舊按鈕/連點不更動草稿、歷史或人工標籤，改回顯目前版本。
                return event_id, True
            if staged and row["callback_revision"] >= 999999:
                expired = True
            if expired:
                state = "expired"
            elif operation == "cancel":
                state = "cancelled"
            elif operation == "identity_back" and staged:
                state = "identity"
            elif who and staged and state == "identity":
                selected = []
                self.store.connection.execute(
                    "UPDATE telegram_track_tag_drafts SET identity_snapshot=?,name_snapshot=? WHERE review_id=?",
                    (*identity_choice, review_id),
                )
                state = "confirming" if identity_choice[0] == "not_cat" else "editing"
            elif who or (operation == "identity_back" and not staged):
                raise ValueError("請使用目前步驟的按鈕。")
            elif operation == "back":
                if state != "confirming" or (staged and row["identity_snapshot"] == "not_cat"):
                    raise ValueError("請使用目前步驟的按鈕。")
                state = "editing"
            elif operation in {"review", "done"}:
                if state not in {"editing", "confirming"}:
                    raise ValueError("請先選擇身分。")
                if not selected and row["identity_snapshot"] != "not_cat":
                    raise ValueError("請至少選擇一個動作，再按提交。")
                state = "submitted" if operation == "done" and state == "confirming" else "confirming"
                if state == "submitted" and staged:
                    dimension = "cat_presence" if row["identity_snapshot"] == "not_cat" else "identity"
                    label = row["identity_snapshot"]
                    learn = int(dimension == "identity" and label in {pet.pet_id for pet in (pets or [])})
                    self.store.connection.execute(
                        "INSERT INTO behavior_event_annotation_history(update_id,callback_ref,event_id,reviewer_ref,dimension,label,learning_consent) VALUES(?,?,?,?,?,?,?)",
                        (update_id, callback_ref, event_id, reviewer_ref, dimension, label, learn),
                    )
                    self.store.connection.execute(
                        "INSERT INTO behavior_event_annotations(event_id,reviewer_ref,dimension,label,learning_consent) VALUES(?,?,?,?,?) ON CONFLICT(event_id,reviewer_ref,dimension) DO UPDATE SET label=excluded.label,learning_consent=excluded.learning_consent,updated_at=CURRENT_TIMESTAMP",
                        (event_id, reviewer_ref, dimension, label, learn),
                    )
                    if dimension == "identity":
                        self.store.connection.execute(
                            "INSERT INTO behavior_event_annotations(event_id,reviewer_ref,dimension,label) VALUES(?,?,'cat_presence','cat') ON CONFLICT(event_id,reviewer_ref,dimension) DO UPDATE SET label='cat',updated_at=CURRENT_TIMESTAMP",
                            (event_id, reviewer_ref),
                        )
            elif operation == "clear":
                if state != "editing":
                    raise ValueError("請先返回動作選單再更改。")
                selected = []
            else:
                if state != "editing":
                    raise ValueError("請先返回動作選單再更改。")
                if operation == "unknown":
                    selected = [] if selected == ["unknown"] else ["unknown"]
                else:
                    selected = [tag for tag in selected if tag != "unknown"]
                    selected = [tag for tag in selected if tag != operation] if operation in selected else [*selected, operation]
                selected = [tag for tag in FEEDBACK_TAGS if tag in selected]
            tags_json = json.dumps(selected)
            self.store.connection.execute(
                "INSERT INTO telegram_track_tag_history(update_id,callback_ref,review_id,operation,tags_json,state,callback_revision) VALUES(?,?,?,?,?,?,?)",
                (update_id, callback_ref, review_id, operation, tags_json, state, incoming_revision),
            )
            self.store.connection.execute(
                "UPDATE telegram_track_tag_drafts SET selected_json=?,state=?,callback_revision=callback_revision+1 WHERE review_id=?",
                (tags_json, state, review_id),
            )
            self.store.connection.execute(
                "INSERT INTO telegram_update_state(id,next_offset) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET next_offset=MAX(next_offset,excluded.next_offset)",
                (update_id + 1,),
            )
            return event_id, False
        event_id, stale = self._save(save)
        with self.store._connection_lock:
            draft = self.store.connection.execute("SELECT name_snapshot,staged_identity,identity_snapshot,callback_revision,state,selected_json,origin_message_id,recipient_ref,event_id,prompt_media_kind FROM telegram_track_tag_drafts WHERE review_id=?", (review_id,)).fetchone()
            name = draft[0]
            state, selected = draft[4], json.loads(draft[5])
            named = self.store.connection.execute(
                "SELECT label,learning_consent FROM behavior_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension='identity'",
                (event_id, reviewer_ref),
            ).fetchone()
        learning = event_id if not draft[1] and named is not None and named[0] != "unknown" else None
        labels = "、".join(FEEDBACK_TAGS[tag] for tag in selected) or "尚未選取"
        if state == "identity":
            markup = identity_keyboard(review_id, pets or [])
            text = "第一步：這條軌跡是哪隻貓？\n選擇只會保存草稿；最後確認提交才建立人工回報。"
        elif state == "confirming":
            markup = {"inline_keyboard": [[{"text": "✅ 確認提交", "callback_data": f"tags:{review_id}:done"}],
                                           [{"text": "返回修改", "callback_data": f"tags:{review_id}:back"},
                                            {"text": "取消", "callback_data": f"tags:{review_id}:cancel"}]]}
            text = f"第三步：確認回報\n這條軌跡：{name}\n已選 {len(selected)} 項：{labels}\n這是整段影片曾出現的動作，不包含精準起迄。請確認後提交。"
            if draft[1]:
                markup["inline_keyboard"].append([{"text": "返回身分", "callback_data": f"tags:{review_id}:identity_back"}])
            if draft[2] == "not_cat":
                markup["inline_keyboard"] = [markup["inline_keyboard"][0],
                    [{"text": "返回身分", "callback_data": f"tags:{review_id}:identity_back"},
                     {"text": "取消", "callback_data": f"tags:{review_id}:cancel"}]]
                text = "第三步：確認這條軌跡不是貓。\n不詢問動作；確認提交才保存此回報。"
        elif state in {"submitted", "cancelled", "expired", "superseded"}:
            markup = {"inline_keyboard": []}
            if state == "submitted" and draft[1]:
                with self.store._connection_lock:
                    if self._next_track(draft):
                        markup["inline_keyboard"] = [[{"text":"繼續回報下一條軌跡", "callback_data":f"next:{review_id}"}]]
            text = f"已提交：{name}｜{labels}。" if state == "submitted" else "這次動作回報已取消，未提交。" if state == "cancelled" else "選單已過期或被取代，未提交；請重新開啟回報。"
        else:
            markup = tag_keyboard(review_id, selected, event_id=learning)
            if draft[1]:
                markup["inline_keyboard"].append([{"text": "上一步：修改身分", "callback_data": f"tags:{review_id}:identity_back"}])
            text = f"第二步：{name}的動作（可多選）\n已選 {len(selected)} 項：{labels}\n選完按下一步，再確認提交。"
        markup = revision_keyboard(markup, draft[3] if draft[1] and markup["inline_keyboard"] else None)
        if stale:
            text = "選單已更新，這次點擊未變更回報；以下是目前選取狀態。\n" + text
        try:
            edit_text = getattr(self.notifier, "edit_message_caption" if draft[9] == "photo" else "edit_message_text", None)
            if callable(edit_text):
                edit_text(chat_id, message_id, text, reply_markup=markup)
            else:
                self.notifier.edit_message_reply_markup(chat_id, message_id, reply_markup=markup)
        except Exception:  # noqa: BLE001 - 已落庫回報不受按鈕更新失敗影響
            if stale:
                return "這次點擊未變更回報；選單更新未完成，請重新開啟回報確認。"
            return "回報已保存；按鈕更新未完成，可重新開啟回報確認。"
        return text
