"""Authorized Telegram callback receiver for cat detection feedback."""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from typing import Any, ClassVar
from uuid import UUID

from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.core.notifications.cat_preferences import CatNotificationPreferences
from meowkeeper.core.notifications.pet_feedback_wizard import PICK, STEP, PetFeedbackWizard
from meowkeeper.core.notifications.telegram import TelegramNotificationError, TelegramNotifier
from meowkeeper.core.notifications.track_tag_feedback import (
    NEXT_CALLBACK,
    TAG_CALLBACK,
    WHO_CALLBACK,
    TrackTagFeedback,
)
from meowkeeper.storage.database import SQLiteStore


class TelegramFeedbackPoller:
    """One bounded long-poll worker; it never touches media ingest or frames."""

    _CALLBACK = re.compile(r"^cat:([0-9a-fA-F-]{36}):(yes|no)$")
    _ANNOTATION = re.compile(r"^fb:([0-9a-fA-F-]{36}):(i|b):([a-z0-9_-]{1,16})$")
    _TRACK_OPEN = re.compile(r"^trk:([0-9a-fA-F-]{36}):open$")
    _TRACK_ANNOTATION = re.compile(r"^be:([0-9a-fA-F-]{36}):(c|i|b|m|l):([a-z0-9_-]{1,32})$")
    _BEHAVIORS: ClassVar[dict[str, str]] = {
        "eating": "進食", "drinking": "喝水", "resting": "休息",
        "sleeping": "睡覺", "playing": "玩耍", "grooming": "理毛",
        "litter_box": "使用貓砂", "other_active": "其他活動",
        "out_of_view": "不在畫面", "unknown": "無法判斷",
    }
    _MOTIONS: ClassVar[dict[str, str]] = {
        "stationary": "靜止", "low_motion": "低度移動", "walking": "行走",
        "running": "快速跑動", "jumping": "跳躍",
    }

    def __init__(
        self,
        notifier: TelegramNotifier,
        store: SQLiteStore,
        allowed_chat_ids: Iterable[str],
        *,
        command_router: Any | None = None,
        pet_registry: PetRegistry | None = None,
        preferences: CatNotificationPreferences | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.notifier = notifier
        self.store = store
        self.allowed_chat_ids = frozenset(str(value).strip() for value in allowed_chat_ids if str(value).strip())
        self.command_router = command_router
        self.pet_registry = pet_registry
        self.preferences = preferences
        self.logger = logger or (lambda _message: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = store.telegram_update_offset()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self.allowed_chat_ids:
            raise ValueError("Telegram 貓咪回饋需要至少一位已啟用的通知對象。")
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="meowkeeper-telegram-feedback", daemon=True,
        )
        self._thread.start()

    def close(self, *, wait: bool = True) -> None:
        self._stop.set()
        if wait and self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=35.0)

    shutdown = close

    def _run(self) -> None:
        delay = 3.0
        while not self._stop.is_set():
            try:
                webhook = self.notifier.get_webhook_info()
                result = webhook.get("result")
                if isinstance(result, Mapping) and result.get("url"):
                    self._log("⚠️ Telegram 已設定 webhook；為避免與其他接收端衝突，貓咪回饋輪詢未啟動。")
                    return
                if self.command_router is not None:
                    try:
                        self.command_router.register_menu()
                    except Exception as exc:  # noqa: BLE001 - menu setup must not stop media monitoring
                        self._log(f"⚠️ Telegram 命令選單同步失敗：{type(exc).__name__}。")
                break
            except TelegramNotificationError as exc:
                if exc.status_code in {401, 403}:
                    self._log("⚠️ Telegram 憑證或 Bot 權限未通過；回饋接收已停止，請在本機 Dashboard 檢查設定。")
                    return
                self._log("⚠️ Telegram webhook 狀態暫時無法確認；回饋接收稍後重試。")
                if self._stop.wait(delay):
                    return
                delay = min(60.0, delay * 2)

        delay = 3.0
        while not self._stop.is_set():
            try:
                updates = self.notifier.get_updates(offset=self._offset, timeout_seconds=20)
                delay = 3.0
                for update in updates:
                    if self._stop.is_set():
                        return
                    update_id = update.get("update_id")
                    if type(update_id) is not int or update_id < 0:
                        self._log("⚠️ Telegram 回饋訊息格式不正確，已略過。")
                        continue
                    try:
                        self._process_update(update_id, update)
                    except Exception as exc:  # noqa: BLE001 - preserve pending update after any storage error
                        self._log(f"⚠️ Telegram 回饋尚未保存，稍後重試：{type(exc).__name__}。")
                        if self._stop.wait(1.0):
                            return
                        break
            except TelegramNotificationError as exc:
                if exc.status_code in {400, 409}:
                    self._log("⚠️ Telegram 回饋接收與既有 webhook／poller 衝突，已停止輪詢。")
                    return
                if exc.status_code in {401, 403}:
                    self._log("⚠️ Telegram 憑證或 Bot 權限失效；回饋接收已停止，請在本機 Dashboard 檢查設定。")
                    return
                self._log("⚠️ Telegram 回饋服務暫時無法連線，稍後重試。")
                if self._stop.wait(delay):
                    return
                delay = min(60.0, delay * 2)
            except Exception as exc:  # noqa: BLE001 - feedback worker must not stop core monitoring
                self._log(f"⚠️ Telegram 回饋背景工作發生錯誤：{type(exc).__name__}。")
                if self._stop.wait(delay):
                    return
                delay = min(60.0, delay * 2)

    def _process_update(self, update_id: int, update: Mapping[str, Any]) -> None:
        callback = update.get("callback_query")
        if isinstance(callback, Mapping) and self.command_router is not None:
            try:
                if self.command_router.handle_callback_update(callback, update_id=update_id):
                    self._commit_update(update_id)
                    self.command_router.after_update_committed(update_id)
                    return
            except Exception as exc:
                self._log(f"⚠️ Telegram 命令回呼處理失敗：{type(exc).__name__}。")
                raise
        if not isinstance(callback, Mapping):
            message = update.get("message")
            if isinstance(message, Mapping) and self.command_router is not None:
                try:
                    self.command_router.handle_message_update(message)
                except Exception as exc:
                    self._log(f"⚠️ Telegram 命令處理失敗：{type(exc).__name__}。")
                    raise
            self._commit_update(update_id)
            if self.command_router is not None:
                self.command_router.after_update_committed(update_id)
            return

        callback_id = callback.get("id")
        accepted = False
        response = "此回饋無效或已過期，資料未變更。"
        match = self._CALLBACK.fullmatch(str(callback.get("data") or ""))
        annotation = self._ANNOTATION.fullmatch(str(callback.get("data") or ""))
        track_open = self._TRACK_OPEN.fullmatch(str(callback.get("data") or ""))
        track_annotation = self._TRACK_ANNOTATION.fullmatch(str(callback.get("data") or ""))
        tag_match = TAG_CALLBACK.fullmatch(str(callback.get("data") or "")) or WHO_CALLBACK.fullmatch(str(callback.get("data") or ""))
        next_match = NEXT_CALLBACK.fullmatch(str(callback.get("data") or ""))
        wizard_data = str(callback.get("data") or "")
        wizard_match = PICK.fullmatch(wizard_data) or STEP.fullmatch(wizard_data)
        message = callback.get("message")
        sender = callback.get("from")
        chat = message.get("chat") if isinstance(message, Mapping) else None
        chat_id = str(chat.get("id")) if isinstance(chat, Mapping) and chat.get("id") is not None else ""
        user_id = str(sender.get("id")) if isinstance(sender, Mapping) and sender.get("id") is not None else ""
        message_id = message.get("message_id") if isinstance(message, Mapping) else None

        authorized = (chat_id in self.allowed_chat_ids and chat_id == user_id
                and isinstance(chat, Mapping) and chat.get("type") == "private"
                and type(message_id) is int)
        if (match or annotation or track_open or track_annotation or tag_match or next_match or wizard_match) and authorized and (
            self.preferences is None or not self.preferences.feedback_enabled(chat_id)
        ):
            self._commit_update(update_id)
            response = "你尚未參與回饋；可輸入 /feedback on 開啟。"
            if callback_id:
                self.notifier.answer_callback_query(str(callback_id), text=response, show_alert=True)
            return
        if next_match and authorized:
            try:
                TrackTagFeedback(self.store, self.notifier).open_next(next_match.group(1), chat_id=chat_id,
                    reviewer_ref=self._digest(f"user:{user_id}"), message_id=message_id, update_id=update_id, pets=self._pets())
                accepted = True
                response = "已開啟下一條軌跡，原影片不會重傳。"
            except (ValueError, TypeError):
                response = "下一條回報未開啟；請確認原通知仍有效且已完成目前回報。"
            self._commit_update(update_id)
        elif tag_match and authorized:
            try:
                response = TrackTagFeedback(self.store, self.notifier).handle(
                    str(callback.get("data")), chat_id=chat_id,
                    reviewer_ref=self._digest(f"user:{user_id}"), message_id=message_id,
                    update_id=update_id, callback_ref=self._digest(str(callback_id)),
                    pets=self._pets(),
                )
                accepted = True
                self._commit_update(update_id)
                if ":done" in str(callback.get("data")):
                    self._sync_feedback_learning()
            except (ValueError, TypeError) as exc:
                response = str(exc)
                self._commit_update(update_id)
        elif wizard_match and authorized:
            try:
                response = PetFeedbackWizard(self.store, self.notifier, self._pets).handle(
                    wizard_data, chat_id=chat_id, reviewer_ref=self._digest(f"user:{user_id}"),
                    message_id=message_id, update_id=update_id,
                    callback_ref=self._digest(str(callback_id)),
                )
                accepted = True
                self._commit_update(update_id)
            except (ValueError, TypeError):
                self._commit_update(update_id)
        elif track_open and authorized:
            try:
                event_id = str(UUID(track_open.group(1)))
                if (not self.store.has_behavior_event_telegram_receipt(
                    event_id, self._digest(chat_id), message_id,
                ) and not self.store.bind_media_track_receipt(event_id, self._digest(chat_id), message_id)):
                    raise ValueError("逐軌跡通知回執不存在。")
                opened = TrackTagFeedback(self.store, self.notifier).open_identity(
                    chat_id, event_id, self._pets(), origin_message_id=message_id, origin_update_id=update_id,
                )
                if not opened:
                    response = "回報選單目前無法送出，請稍後再試。"
                    self._commit_update(update_id)
                else:
                    accepted = True
                    response = "已開啟這條軌跡的獨立回報選單。"
                    self._commit_update(update_id)
            except (ValueError, TypeError) as exc:
                response = str(exc)
                self._commit_update(update_id)
        elif track_annotation and authorized:
            try:
                event_id = str(UUID(track_annotation.group(1)))
                with self.store._connection_lock:
                    staged_prompt = self.store.connection.execute(
                        "SELECT 1 FROM telegram_track_tag_drafts WHERE event_id=? AND recipient_ref=? AND message_id=? AND staged_identity=1",
                        (event_id, self._digest(chat_id), message_id),
                    ).fetchone()
                if staged_prompt is not None:
                    raise ValueError("請使用這份回報的身分／動作按鈕，最後確認提交。")
                dimension_key = track_annotation.group(2)
                choice = track_annotation.group(3)
                dimension = {
                    "c": "cat_presence", "i": "identity",
                    "b": "behavior", "m": "motion", "l": "identity",
                }[dimension_key]
                if dimension_key == "l":
                    if choice not in {"yes", "no"}:
                        raise ValueError("學習同意選項不合法。")
                    with self.store._connection_lock:
                        named = self.store.connection.execute(
                            "SELECT label FROM behavior_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension='identity'",
                            (event_id, self._digest(f"user:{user_id}")),
                        ).fetchone()
                    if named is None or named[0] not in {pet.pet_id for pet in self._pets()}:
                        raise ValueError("請先回報已登錄的貓咪身分。")
                    label = named[0]
                elif dimension == "cat_presence":
                    if choice not in {"yes", "no"}:
                        raise ValueError("貓咪正誤選項已失效。")
                    label = "cat" if choice == "yes" else "not_cat"
                elif dimension in {"behavior", "motion"}:
                    choices = self._BEHAVIORS if dimension == "behavior" else self._MOTIONS
                    if choice not in choices:
                        raise ValueError("行為或動作選項已失效。")
                    label = choice
                elif choice in {"unknown", "uncertain"}:
                    label = "unknown"
                else:
                    matches = [pet for pet in self._pets() if self._pet_token(pet.pet_id) == choice]
                    if len(matches) != 1:
                        raise ValueError("寵物選項已失效，請重新登錄。")
                    label = matches[0].pet_id
                result = self.store.record_behavior_event_annotation(
                    event_id=event_id, recipient_ref=self._digest(chat_id),
                    reviewer_ref=self._digest(f"user:{user_id}"), message_id=message_id,
                    dimension=dimension, label=label, update_id=update_id,
                    callback_ref=self._digest(str(callback_id)),
                    learning_consent=(dimension_key == "l" and choice == "yes") or (
                        dimension_key == "i" and label in {pet.pet_id for pet in self._pets()}),
                )
                accepted = True
                response = "這條軌跡的回饋已經記錄。" if result == "duplicate" else "已保存這條軌跡的人工回饋。"
                if dimension_key == "l":
                    response = "已更新這筆回報的使用設定；人工回報歷史仍保留。"
                if dimension == "identity" and dimension_key != "l" and result != "duplicate":
                    self._send_behavior_event_prompt(chat_id, event_id, actions_only=True)
                if dimension in {"identity", "cat_presence"} and result != "duplicate":
                    self._sync_feedback_learning()
                self._offset = max(self._offset or 0, update_id + 1)
            except (ValueError, TypeError) as exc:
                if str(exc) == "請先確認這條軌跡是貓咪，再標註身分或行為。":
                    response = "請先選登錄貓名或「其他貓」，再回報行為或動作。"
                self._commit_update(update_id)
        elif match and authorized:
            try:
                event_id = str(UUID(match.group(1)))
                result = self.store.record_media_event_feedback(
                    event_id=event_id,
                    recipient_ref=self._digest(chat_id),
                    message_id=message_id,
                    reviewer_ref=self._digest(f"user:{user_id}"),
                    label="cat" if match.group(2) == "yes" else "not_cat",
                    update_id=update_id,
                    callback_ref=self._digest(str(callback_id)),
                )
                accepted = True
                response = {
                    "recorded": "已記錄：是貓咪。",
                    "updated": "已更新這筆回饋：是貓咪。" if match.group(2) == "yes" else "已更新這筆回饋：不是貓咪。",
                    "disputed": "已記錄；目前不同使用者的標註不一致，已列為待複核。",
                    "duplicate": "這筆回饋已經記錄。",
                }.get(result, "已記錄這筆回饋。")
                if result == "recorded" and match.group(2) == "no":
                    response = "已記錄：不是貓咪。"
                if match.group(2) == "yes" and result != "duplicate":
                    self._send_annotation_prompt(chat_id, event_id)
            except (ValueError, TypeError):
                self._commit_update(update_id)
            else:
                self._offset = max(self._offset or 0, update_id + 1)
        elif annotation and authorized:
            try:
                event_id = str(UUID(annotation.group(1)))
                dimension = "identity" if annotation.group(2) == "i" else "behavior"
                choice = annotation.group(3)
                if dimension == "behavior":
                    if choice not in self._BEHAVIORS:
                        raise ValueError("無效的行為選項。")
                    label = choice
                elif choice == "unknown":
                    label = "unknown"
                else:
                    matches = [pet for pet in self._pets() if self._pet_token(pet.pet_id) == choice]
                    if len(matches) != 1:
                        raise ValueError("寵物選項已失效，請重新登錄。")
                    label = matches[0].pet_id
                result = self.store.record_media_event_annotation(
                    event_id=event_id, recipient_ref=self._digest(chat_id),
                    reviewer_ref=self._digest(f"user:{user_id}"),
                    dimension=dimension, label=label, update_id=update_id,
                    callback_ref=self._digest(str(callback_id)),
                )
                accepted = True
                response = "這筆回饋已經記錄。" if result == "duplicate" else "已保存人工回饋。"
            except (ValueError, TypeError):
                self._commit_update(update_id)
            else:
                self._offset = max(self._offset or 0, update_id + 1)
        else:
            self._commit_update(update_id)

        if callback_id:
            try:
                self.notifier.answer_callback_query(
                    str(callback_id), text=response, show_alert=not accepted,
                )
            except Exception as exc:  # noqa: BLE001 - feedback is durable even if toast fails
                self._log(f"⚠️ 回饋已處理，但 Telegram 確認提示未送達：{type(exc).__name__}。")

    def _pets(self) -> list[Any]:
        if self.pet_registry is None:
            return []
        return self.pet_registry.list_pets(enabled_only=True)

    @staticmethod
    def _pet_token(pet_id: str) -> str:
        return hashlib.sha256(pet_id.encode("utf-8")).hexdigest()[:12]

    def _send_annotation_prompt(self, chat_id: str, event_id: str) -> None:
        send = getattr(self.notifier, "send_message", None)
        if not callable(send):
            return
        try:
            pets = self._pets()[:8]
        except (OSError, RuntimeError, ValueError) as exc:
            self._log(f"⚠️ 寵物名單暫時無法讀取，仍可回覆行為：{type(exc).__name__}。")
            pets = []
        rows: list[list[dict[str, str]]] = []
        for pet in pets:
            rows.append([{"text": f"🐱 {pet.display_name[:24]}", "callback_data": f"fb:{event_id}:i:{self._pet_token(pet.pet_id)}"}])
        rows.append([{"text": "🐱 身分不確定", "callback_data": f"fb:{event_id}:i:unknown"}])
        choices = [(key, name) for key, name in self._BEHAVIORS.items()
                   if key not in {"out_of_view", "unknown"}]
        for index in range(0, len(choices), 2):
            rows.append([{"text": name, "callback_data": f"fb:{event_id}:b:{key}"}
                         for key, name in choices[index:index + 2]])
        try:
            send(chat_id, "請選擇是哪隻貓與正在做什麼；不確定可不回覆。這些是人工標註，不會立即訓練模型。", reply_markup={"inline_keyboard": rows})
        except Exception as exc:  # noqa: BLE001 - notification follow-up must not roll back durable cat feedback
            self._log(f"⚠️ 貓咪回饋已保存，但細項選單未送達：{type(exc).__name__}。")

    def _send_behavior_event_prompt(self, chat_id: str, event_id: str, *, actions_only: bool = False) -> bool:
        send = getattr(self.notifier, "send_message", None)
        if not callable(send):
            return False
        try:
            pets = self._pets()[:8]
        except (OSError, RuntimeError, ValueError) as exc:
            self._log(f"⚠️ 寵物名單暫時無法讀取；仍可回報貓咪正誤與行為：{type(exc).__name__}。")
            pets = []
        if actions_only:
            try:
                return TrackTagFeedback(self.store, self.notifier).open(chat_id, event_id, pets)
            except Exception as exc:  # noqa: BLE001 - 回報提示失敗不影響已保存身分
                self._log(f"⚠️ 動作多選選單未送達：{type(exc).__name__}。")
                return False
        rows: list[list[dict[str, str]]] = []
        for pet in pets:
            rows.append([{
                "text": f"🐱 {pet.display_name[:24]}",
                "callback_data": f"be:{event_id}:i:{self._pet_token(pet.pet_id)}",
            }])
        rows.append([
            {"text": "其他貓", "callback_data": f"be:{event_id}:i:unknown"},
            {"text": "不是貓", "callback_data": f"be:{event_id}:c:no"},
        ])
        rows.append([{"text": "無法確認身分", "callback_data": f"be:{event_id}:i:uncertain"}])
        try:
            result = send(
                chat_id,
                "第一步：這條軌跡是哪隻貓？選登錄貓名、其他貓或不是貓；選貓名後再回報行為。",
                reply_markup={"inline_keyboard": rows},
            )
            if result.message_id is None:
                return False
            self.store.record_behavior_event_telegram_receipt(
                event_id, self._digest(chat_id), result.message_id,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - feedback must not stop polling or monitoring
            self._log(f"⚠️ 逐軌跡回報選單未送達：{type(exc).__name__}。")
            return False

    def _sync_feedback_learning(self) -> None:
        """Immediately extract and publish consented track identity embeddings to feedback_embeddings.npz."""
        if self.pet_registry is None:
            return
        try:
            from meowkeeper.core.identity.dinov2_embedder import shared_cpu_embedder
            from meowkeeper.core.identity.feedback_learning import FeedbackIdentityLearner

            FeedbackIdentityLearner(
                self.store,
                self.pet_registry,
                shared_cpu_embedder(),
            ).refresh(max_new_samples=16)
        except Exception:  # noqa: BLE001 - optional embedder or test stub must never interrupt Telegram polling
            pass

    def _commit_update(self, update_id: int) -> None:
        """Advance memory and durable offsets together for ignored/invalid updates."""
        self.store.commit_telegram_update(update_id)
        self._offset = max(self._offset or 0, update_id + 1)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _log(self, message: str) -> None:
        try:
            self.logger(message)
        except Exception:  # noqa: BLE001 - diagnostics cannot stop callback processing
            self.logger = lambda _message: None
