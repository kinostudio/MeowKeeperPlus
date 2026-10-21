"""影片級兩輪回饋：個體／數量 → 每隻貓的行為；不冒用 track 標籤。"""

from __future__ import annotations

import hashlib
import json
import re
from uuid import uuid4

from meowkeeper.storage.database import SQLiteStore

BEHAVIORS = {
    "eating": "進食", "drinking": "喝水", "resting": "休息",
    "sleeping": "睡覺", "playing": "玩耍", "grooming": "理毛",
    "litter_box": "使用貓砂", "other_active": "其他活動",
}
MOTIONS = {"walking": "行走", "running": "奔跑"}
PICK = re.compile(r"^pick:([0-9a-f-]{36}):([a-z0-9]{1,12})$")
STEP = re.compile(r"^step:([0-9a-f-]{36}):([0-7]):([a-z_]{1,16})$")


def pet_token(pet_id: str) -> str:
    return hashlib.sha256(pet_id.encode()).hexdigest()[:12]


def first_round(event_id: str, pets: list) -> dict:
    rows = [[{"text": f"是{pet.display_name[:24]}",
              "callback_data": f"pick:{event_id}:{pet_token(pet.pet_id)}"}] for pet in pets[:8]]
    if len(pets) == 2:
        rows.append([{"text": "兩隻都在", "callback_data": f"pick:{event_id}:both"}])
    rows.append([{"text": "不是貓咪", "callback_data": f"pick:{event_id}:no"}])
    return {"inline_keyboard": rows}


class PetFeedbackWizard:
    def __init__(self, store: SQLiteStore, notifier, pets) -> None:
        self.store, self.notifier, self.pets = store, notifier, pets

    def handle(self, data: str, *, chat_id: str, reviewer_ref: str,
               message_id: int, update_id: int, callback_ref: str) -> str:
        recipient = hashlib.sha256(chat_id.encode()).hexdigest()
        pick, step = PICK.fullmatch(data), STEP.fullmatch(data)
        if pick:
            event_id, choice = pick.groups()
            pets = self.pets()[:8]
            selected = pets if choice == "both" and len(pets) == 2 else [
                pet for pet in pets if pet_token(pet.pet_id) == choice
            ]
            if choice != "no" and not selected:
                raise ValueError("寵物選項已變更，請重新開啟回報。")
            result = self.store.record_media_event_feedback(
                event_id=event_id, recipient_ref=recipient, message_id=message_id,
                reviewer_ref=reviewer_ref, label="not_cat" if choice == "no" else "cat",
                update_id=update_id, callback_ref=callback_ref,
            )
            if result == "duplicate":
                with self.store._connection_lock:
                    previous = self.store.connection.execute(
                        "SELECT review_id,state_json FROM telegram_pet_reviews WHERE origin_update_id=? AND recipient_ref=? AND reviewer_ref=?",
                        (update_id, recipient, reviewer_ref),
                    ).fetchone()
                if previous:
                    state = json.loads(previous[1])
                    if state["active"]:
                        self._prompt(chat_id, previous[0], state)
                    return "這筆回報已處理。"
            # A fresh choice supersedes earlier unfinished/completed review sessions.
            review_id = str(uuid4())
            state = {"pets": [{"id": p.pet_id, "name": p.display_name} for p in selected],
                     "index": 0, "behaviors": {}, "active": choice != "no"}

            def save():
                self.store.connection.execute(
                    "UPDATE telegram_pet_reviews SET state_json=json_set(state_json,'$.active',json('false')) WHERE event_id=? AND reviewer_ref=?",
                    (event_id, reviewer_ref),
                )
                self.store.connection.execute(
                    "INSERT INTO telegram_pet_reviews(review_id,event_id,recipient_ref,reviewer_ref,origin_update_id,state_json) VALUES(?,?,?,?,?,?)",
                    (review_id, event_id, recipient, reviewer_ref, update_id, json.dumps(state, ensure_ascii=False)),
                )
            self.store._transaction(save)
            if choice == "no":
                return "已記錄：不是貓咪；不再詢問行為。"
            self._prompt(chat_id, review_id, state)
            return "已確認貓咪，請接著選擇行為。"
        if not step:
            raise ValueError("回報格式不正確。")
        review_id, index_text, behavior = step.groups()
        if behavior not in BEHAVIORS and behavior not in MOTIONS:
            raise ValueError("行為選項已失效。")

        def save_behavior():
            row = self.store.connection.execute(
                "SELECT state_json FROM telegram_pet_reviews WHERE review_id=? AND recipient_ref=? AND reviewer_ref=?",
                (review_id, recipient, reviewer_ref),
            ).fetchone()
            receipt = self.store.connection.execute(
                "SELECT 1 FROM telegram_pet_review_receipts WHERE review_id=? AND message_id=?",
                (review_id, message_id),
            ).fetchone()
            if row is None or receipt is None:
                raise ValueError("回報與收件者或訊息不符。")
            state = json.loads(row[0])
            if self.store.connection.execute(
                "SELECT 1 FROM telegram_pet_review_history WHERE update_id=?", (update_id,),
            ).fetchone():
                return state, False
            index = int(index_text)
            if not state["active"] or index != state["index"]:
                raise ValueError("此步驟已完成，請使用最新的行為選單。")
            pet = state["pets"][index]
            motion = behavior if behavior in MOTIONS else None
            primary_behavior = "other_active" if motion else behavior
            state["behaviors"][pet["id"]] = primary_behavior
            if motion:
                state.setdefault("motions", {})[pet["id"]] = motion
            state["index"] += 1
            state["active"] = state["index"] < len(state["pets"])
            self.store.connection.execute(
                "INSERT INTO telegram_pet_review_history(update_id,review_id,pet_id,behavior,motion) VALUES(?,?,?,?,?)",
                (update_id, review_id, pet["id"], primary_behavior, motion),
            )
            self.store.connection.execute(
                "UPDATE telegram_pet_reviews SET state_json=?,updated_at=CURRENT_TIMESTAMP WHERE review_id=?",
                (json.dumps(state, ensure_ascii=False), review_id),
            )
            self.store.connection.execute(
                "INSERT INTO telegram_update_state(id,next_offset) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET next_offset=MAX(next_offset,excluded.next_offset)",
                (update_id + 1,),
            )
            return state, True
        state, changed = self.store._transaction(save_behavior)
        if changed or state["active"]:
            self._prompt(chat_id, review_id, state)
        return "已保存這隻貓的行為。"

    def _prompt(self, chat_id: str, review_id: str, state: dict) -> None:
        if not state["active"]:
            text = "已完成回報：\n" + "\n".join(
                f"{p['name']}：{MOTIONS[state['motions'][p['id']]] if state.get('motions', {}).get(p['id']) else BEHAVIORS[state['behaviors'][p['id']]]}" for p in state["pets"]
            )
            self.notifier.send_message(chat_id, text + "\n人工標註不會立即重訓模型。")
            return
        index = state["index"]
        with self.store._connection_lock:
            if self.store.connection.execute(
                "SELECT 1 FROM telegram_pet_review_receipts WHERE review_id=? AND step_index=?",
                (review_id, index),
            ).fetchone():
                return
        choices = list({**BEHAVIORS, **MOTIONS}.items())
        rows = [[{"text": name, "callback_data": f"step:{review_id}:{index}:{key}"}
                 for key, name in choices[i:i + 2]] for i in range(0, len(choices), 2)]
        result = self.notifier.send_message(
            chat_id, f"{state['pets'][index]['name']}的行為\n請依影片選擇；不確定可不回覆。",
            reply_markup={"inline_keyboard": rows},
        )
        if result.message_id is None:
            raise ValueError("行為選單缺少送達回條。")
        self.store._write(
            "INSERT OR IGNORE INTO telegram_pet_review_receipts(review_id,message_id,step_index) VALUES(?,?,?)",
            (review_id, result.message_id, index),
        )
