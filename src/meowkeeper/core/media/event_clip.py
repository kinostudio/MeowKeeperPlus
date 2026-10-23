"""事件片段工作流：引用分段、組合 MP4、保存 SQLite、非同步 Telegram。"""

from __future__ import annotations

import builtins
import hashlib
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import CancelledError, Future, TimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty, Full, PriorityQueue

from meowkeeper.core.event_semantics import (
    TAG_LABELS,
    EventSemanticService,
    SemanticFuture,
    SemanticRequest,
    SemanticResult,
)
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.core.notifications.cat_preferences import CatNotificationPreferences
from meowkeeper.core.notifications.dispatcher import NotificationDispatcher
from meowkeeper.core.notifications.pet_feedback_wizard import first_round
from meowkeeper.core.notifications.telegram import TelegramNotifier
from meowkeeper.storage.database import SQLiteStore

from .clip_assembler import ClipAssembler, ClipAssemblyResult
from .media_validation import validate_media
from .ring_buffer import DiskSegmentRingBuffer
from .segment_index import SegmentRecord


@dataclass(frozen=True)
class EventClipRequest:
    """事件影片所需的最小 metadata。"""

    event_id: str
    trigger_at: datetime
    event_type: str
    source_id: str
    track_id: str | None = None
    pet_id: str | None = None
    confidence: float = 0.0
    pre_seconds: float = 5.0
    post_seconds: float = 10.0
    screenshot_bytes: bytes | None = None
    priority: int = 50
    trigger_origin: str = "detector"
    model_version: str | None = None
    model_sha256: str | None = None
    confidence_threshold: float | None = None
    track_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.trigger_at.tzinfo is None:
            object.__setattr__(self, "trigger_at", self.trigger_at.replace(tzinfo=timezone.utc))
        if not self.event_id or not self.event_type or not self.source_id:
            raise ValueError("事件必須包含 event_id、event_type 與 source_id。")
        if self.pre_seconds < 0 or self.post_seconds < 0:
            raise ValueError("事件前錄與後錄秒數不可小於零。")
        object.__setattr__(self, "confidence", max(0.0, min(1.0, float(self.confidence))))
        if self.trigger_origin not in {"detector", "simulated", "test"}:
            raise ValueError("事件來源分類不正確。")
        if self.confidence_threshold is not None and not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("事件信心門檻必須介於 0 到 1。")


@dataclass(frozen=True)
class LiveClipResult:
    path: Path | None
    status: str
    actual_duration_seconds: float | None
    error: str | None = None


@dataclass(frozen=True)
class _AssemblyJob:
    request: EventClipRequest
    references: tuple[SegmentRecord, ...]
    semantics: Future[SemanticResult] | None = None
    accepted_monotonic: float | None = None


def _format_natural_cat_headline(subject: str, tags: Any = (), posture: str | None = None) -> str:
    """將貓咪名稱、行為標籤與姿態轉化為溫馨生動的繁體中文語意描述。"""
    tag_set = set(tags) if tags else set()
    if posture:
        tag_set.add(posture)

    # 0. 多重行為標籤：保留組合描述
    valid_tags = [t for t in tags if t in TAG_LABELS and t not in {"unknown", "possible"}]
    if len(valid_tags) > 1:
        joined = "、".join(TAG_LABELS[t] for t in valid_tags[:3])
        return f"{subject}剛剛在{joined}。"

    # 1. 飲食與水
    if "eating" in tag_set:
        return f"{subject}現在正在吃飯飯，要看看嗎？"
    if "drinking" in tag_set:
        return f"{subject}正在大口喝水補充水分～"

    # 2. 睡眠與放鬆休息
    if "sleeping" in tag_set:
        return f"{subject}正在安心熟睡中，好舒服～"
    if "resting" in tag_set and "lying" in tag_set:
        return f"{subject}現在躺在床上休息，好舒服～"
    if "resting" in tag_set:
        return f"{subject}現在正安靜地休息放空喔～"
    if "lying" in tag_set:
        return f"{subject}正舒舒服服地趴躺著放鬆～"

    # 3. 姿態與活動
    if "playing" in tag_set:
        return f"{subject}正在活力滿滿地玩耍中！"
    if "grooming" in tag_set:
        return f"{subject}正在認真理毛，愛乾淨的乖寶寶～"
    if "litter_box" in tag_set or "in_litter_area" in tag_set:
        return f"{subject}正在使用貓砂盆呢。"
    if "sitting" in tag_set:
        return f"{subject}現在乖乖坐著，神情好愜意～"
    if "standing" in tag_set:
        return f"{subject}正站著好奇地張望周圍～"
    if "walking" in tag_set or "running" in tag_set:
        return f"{subject}正在畫面中四處巡視走動呢！"

    # 4. 單一標籤後備
    if valid_tags:
        return f"{subject}剛剛在{TAG_LABELS[valid_tags[0]]}。"

    return f"{subject}剛剛出現在畫面。"

class EventClipService:
    """事件 MP4 與 Telegram 的隔離服務。

    影片組片與通知都有固定大小佇列；事件觸發端只做輕量 metadata／分段引用，
    不等待 FFmpeg、網路或 SQLite 的慢操作。
    """

    def __init__(
        self,
        ring_buffer: DiskSegmentRingBuffer,
        assembler: ClipAssembler,
        *,
        output_directory: str | Path = "data/events",
        store: SQLiteStore | None = None,
        notifier: TelegramNotifier | None = None,
        chat_ids: Iterable[str] = (),
        telegram_enabled: bool = False,
        telegram_dispatcher: NotificationDispatcher | None = None,
        cat_notification_preferences: CatNotificationPreferences | None = None,
        max_clip_queue: int = 8,
        max_telegram_queue: int = 16,
        telegram_max_bytes: int = 50 * 1024 * 1024,
        cooldown_seconds: float = 180.0,
        merge_window_seconds: float = 5.0,
        telegram_retries: int = 2,
        segment_finalize_wait_seconds: float = 60.0,
        retry_sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        logger: Callable[[str], None] | None = None,
        semantic_service: EventSemanticService | None = None,
        semantic_wait_seconds: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_clip_queue < 1 or max_telegram_queue < 1:
            raise ValueError("事件與 Telegram 佇列長度必須至少為 1。")
        if telegram_max_bytes <= 0:
            raise ValueError("Telegram 影片大小上限必須大於零。")
        if cooldown_seconds < 0 or merge_window_seconds < 0:
            raise ValueError("事件冷卻與合併時間不可小於零。")
        if telegram_retries < 0:
            raise ValueError("Telegram 重試次數不可小於零。")
        if segment_finalize_wait_seconds < 0:
            raise ValueError("片段完成等待時間不可小於零。")
        if not 0 <= semantic_wait_seconds <= 10:
            raise ValueError("影片通知 AI 等待時間必須介於 0 到 10 秒。")
        self.semantic_service = semantic_service
        self._media_tasks = 0
        self._media_lock = threading.Lock()
        self.semantic_wait_seconds = float(semantic_wait_seconds)
        self.ring_buffer = ring_buffer
        self.assembler = assembler
        self.output_directory = Path(output_directory).resolve()
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.notifier = notifier
        self.chat_ids = tuple(str(chat_id) for chat_id in chat_ids if str(chat_id).strip())
        self.telegram_enabled = bool(telegram_enabled)
        self.cat_notification_preferences = cat_notification_preferences
        self.telegram_max_bytes = telegram_max_bytes
        self.cooldown_seconds = float(cooldown_seconds)
        self.merge_window_seconds = float(merge_window_seconds)
        self.telegram_retries = int(telegram_retries)
        self.segment_finalize_wait_seconds = float(segment_finalize_wait_seconds)
        self._sleep = retry_sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self.logger = logger
        self._clip_queue: PriorityQueue[tuple[int, int, _AssemblyJob]] = PriorityQueue(maxsize=max_clip_queue)
        self._sequence = 0
        self._last_by_type: dict[str, datetime] = {}
        self._lock = threading.RLock()
        self._active_clip_jobs = 0
        self._stopping = False
        self._clip_worker = threading.Thread(
            target=self._run_clip_worker,
            name="meowkeeper-clip-assembler",
            daemon=True,
        )
        self._clip_worker.start()
        self._owns_telegram_dispatcher = telegram_dispatcher is None
        self.telegram_dispatcher = telegram_dispatcher or NotificationDispatcher(
            max_pending=max_telegram_queue, logger=self.logger,
        )

    @property
    def pending_clip_count(self) -> int:
        return self._clip_queue.qsize()

    def submit_event(self, request: EventClipRequest) -> bool:
        """提交事件；冷卻或佇列滿時略過影片但不影響來源與 AI。"""

        if self._stopping:
            return False
        self._safe_create_event(request)
        if self.ring_buffer.status().disk_below_minimum:
            self._safe_store_update(
                request.event_id,
                clip_status="rejected_disk_low",
                last_error="磁碟剩餘空間低於門檻，已停止建立一般事件影片。",
            )
            self._log("⚠️ 磁碟剩餘空間不足，停止建立一般事件影片。")
            return False
        if request.pre_seconds > self.ring_buffer.retention_seconds:
            self._safe_store_update(request.event_id, clip_status="rejected_invalid_window", last_error="前錄時間超過環形快取。")
            return False
        if self._is_cooled_down(request):
            self._safe_store_update(request.event_id, clip_status="merged_cooldown", last_error="同類事件仍在冷卻時間內。")
            return False
        start = request.trigger_at - timedelta(seconds=request.pre_seconds)
        references = self.ring_buffer.acquire_window(start, request.trigger_at)
        try:
            with self._lock:
                if self._clip_queue.full():
                    raise Full
                semantics = None
                if (self.semantic_service is not None
                        and request.trigger_origin == "detector" and request.event_type == "cat_detected"):
                    tracks = request.track_ids or ((request.track_id,) if request.track_id else ())
                    try:
                        pet_profiles = []
                        try:
                            from meowkeeper.core.identity.pet_registry import PetRegistry
                            reg = PetRegistry(self.output_directory.parent / "pets")
                            for p in reg.list_pets(enabled_only=True):
                                pet_profiles.append({
                                    "pet_id": p.pet_id,
                                    "name": p.display_name,
                                    "traits": p.effective_traits(),
                                })
                        except Exception:
                            pass
                        semantics = self.semantic_service.submit(SemanticRequest(
                            request.event_id, request.screenshot_bytes or b"", tuple(str(t) for t in tracks),
                            trigger_at=request.trigger_at,
                            pet_profiles=tuple(pet_profiles),
                        ))
                    except Exception:  # noqa: BLE001 - API 排程不可使影片事件失敗
                        self._log("事件 AI 候選排程失敗；繼續本機事件影片。")
                job = _AssemblyJob(request, references, semantics, self._monotonic())
                sequence = self._sequence
                self._sequence += 1
                self._clip_queue.put_nowait((max(0, request.priority), sequence, job))
                self._last_by_type[request.event_type] = request.trigger_at
        except Full:
            self.ring_buffer.release(references)
            self._safe_store_update(request.event_id, clip_status="dropped_queue_full", last_error="事件影片佇列已滿。")
            self._log("⚠️ 事件影片佇列已滿，保留事件紀錄但略過低優先影片。")
            return False
        return True

    def get_recent_clip(
        self,
        pre_seconds: float,
        post_seconds: float = 0.0,
        *,
        output_name: str | None = None,
    ) -> LiveClipResult:
        """取得 `/live` 片段；預設只取指令抵達前的快取，不等待後錄。"""

        if pre_seconds < 0 or post_seconds < 0:
            raise ValueError("/live 前錄與後錄秒數不可小於零。")
        if pre_seconds > self.ring_buffer.retention_seconds:
            raise ValueError("/live 預錄長度不可大於環形快取。")
        trigger = self._normalise_now(self._clock())
        references = list(self.ring_buffer.acquire_window(trigger - timedelta(seconds=pre_seconds), trigger))
        try:
            if post_seconds:
                self._wait_until(trigger + timedelta(seconds=post_seconds))
                self.ring_buffer.scan()
                self._acquire_missing(
                    references,
                    self.ring_buffer.index.between(
                        trigger - timedelta(seconds=pre_seconds),
                        trigger + timedelta(seconds=post_seconds),
                    ),
                )
            destination = self.output_directory / (output_name or f"live_{uuid.uuid4().hex}.mp4")
            result = self._media_task(self.assembler.assemble, references, destination)
            return LiveClipResult(result.path, result.status, result.actual_duration_seconds, result.error)
        finally:
            self.ring_buffer.release(references)

    def record_from_now(self, seconds: float, *, output_name: str | None = None) -> LiveClipResult:
        """與 `/live` 分離的「從現在開始錄影」介面。"""

        if seconds <= 0:
            raise ValueError("從現在開始錄影的秒數必須大於零。")
        return self.get_recent_clip(0.0, seconds, output_name=output_name)

    def wait_for_idle(self, timeout_seconds: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                active = self._active_clip_jobs
            if self._clip_queue.unfinished_tasks == 0 and active == 0 and self.telegram_dispatcher.pending_count == 0:
                return True
            time.sleep(0.02)
        with self._lock:
            active = self._active_clip_jobs
        return self._clip_queue.unfinished_tasks == 0 and active == 0 and self.telegram_dispatcher.pending_count == 0

    def close(self, *, wait: bool = True) -> None:
        self._stopping = True
        if wait and self._clip_worker is not threading.current_thread():
            self._clip_worker.join(timeout=10.0)
        if self._owns_telegram_dispatcher:
            self.telegram_dispatcher.shutdown(wait=wait)
        self.ring_buffer.scan()

    shutdown = close

    def _run_clip_worker(self) -> None:
        while not self._stopping or not self._clip_queue.empty():
            try:
                _priority, _sequence, job = self._clip_queue.get(timeout=0.1)
            except Empty:
                continue
            with self._lock:
                self._active_clip_jobs += 1
            try:
                self._process_job(job)
            except Exception as exc:  # noqa: BLE001 - worker must survive one event
                self._safe_store_update(job.request.event_id, clip_status="failed", last_error=type(exc).__name__)
                self._log(f"⚠️ 事件影片背景工作失敗：{type(exc).__name__}")
            finally:
                with self._lock:
                    self._active_clip_jobs -= 1
                self._clip_queue.task_done()

    def _process_job(self, job: _AssemblyJob) -> None:
        request = job.request
        references = list(job.references)
        try:
            self._save_snapshot(request)
        except OSError:
            self._log("⚠️ 事件對照圖片未能保存；繼續組裝影片。")
        try:
            self._wait_until(request.trigger_at + timedelta(seconds=request.post_seconds))
            self._acquire_window_with_grace(
                references,
                start_at=request.trigger_at - timedelta(seconds=request.pre_seconds),
                end_at=request.trigger_at + timedelta(seconds=request.post_seconds),
            )
            destination = self.output_directory / f"event_{request.event_id}.mp4"
            result = self._media_task(self.assembler.assemble, references, destination)
            if result.path is None:
                snapshot_path = self._save_snapshot(request)
                self._safe_store_update(
                    request.event_id,
                    clip_status="failed_with_snapshot" if snapshot_path else "failed",
                    clip_path=str(snapshot_path) if snapshot_path else None,
                    last_error=result.error or "事件影片組片失敗。",
                )
                if snapshot_path is not None:
                    self._queue_snapshot_telegram(request, snapshot_path, result.error)
                return
            first = min(references, key=lambda item: item.start_at) if references else None
            last = max(references, key=lambda item: item.end_at) if references else None
            self._safe_store_update(
                request.event_id,
                clip_status="completed",
                clip_start_at=first.start_at if first else None,
                clip_end_at=last.end_at if last else None,
                actual_duration_seconds=result.actual_duration_seconds,
                clip_path=str(result.path),
                clip_size_bytes=result.size_bytes,
                clip_sha256=result.sha256,
                completed_at=self._clock(),
                last_error=None,
            )
            self._queue_telegram(request, result, references, semantics=job.semantics,
                                 prepared_since=job.accepted_monotonic)
        finally:
            self.ring_buffer.release(references)

    def _queue_telegram(
        self,
        request: EventClipRequest,
        result: ClipAssemblyResult,
        references: Iterable[SegmentRecord],
        *, semantics: Future[SemanticResult] | None = None, prepared_since: float | None = None,
    ) -> None:
        if not self.telegram_enabled or self.notifier is None or not self.chat_ids:
            self._safe_store_update(request.event_id, telegram_status="disabled", telegram_attempts=0)
            return
        if not self._eligible_chat_ids(request):
            self._safe_store_update(request.event_id, telegram_status="muted", telegram_attempts=0)
            return
        if result.path is None:
            return
        records = tuple(references)
        caption = self._caption(request, result, records)
        def send() -> tuple[int, str | None]:
            return self._send_with_retries(request, result.path, caption,
                                           semantics=semantics, clip_result=result, references=records,
                                           prepared_since=prepared_since)

        try:
            future = self.telegram_dispatcher.submit(send)
        except RuntimeError as exc:
            self._safe_store_update(request.event_id, telegram_status="failed", last_error=str(exc))
            return
        self._safe_store_update(request.event_id, telegram_status="pending")
        future.add_done_callback(lambda completed: self._telegram_done(request.event_id, completed))

    def _queue_snapshot_telegram(
        self, request: EventClipRequest, snapshot_path: Path, clip_error: str | None,
    ) -> None:
        if not self.telegram_enabled or self.notifier is None or not self.chat_ids:
            return
        if request.trigger_origin != "detector" or request.event_type != "cat_detected":
            return
        if not self._eligible_chat_ids(request):
            self._safe_store_update(request.event_id, telegram_status="muted", telegram_attempts=0)
            return
        caption = (
            "喵喵守護者+ 貓咪告警截圖\n"
            f"發生時間：{request.trigger_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"模型分數：{request.confidence:.0%}（不是正確機率）\n"
            "影片目前無法組裝，先附當下影格供人工確認。"
        )
        def send() -> tuple[int, str | None]:
            delivered: set[str] = set()
            reserved: set[str] = set()
            error = None
            for attempt in range(1, self.telegram_retries + 2):
                try:
                    image = snapshot_path.read_bytes()
                    for chat_id in self.chat_ids:
                        if chat_id in delivered or not self._claim_notify(chat_id, request, reserved):
                            continue
                        receipt = self.notifier.send_photo(
                            chat_id, image, caption=caption,
                            reply_markup=self._feedback_markup(chat_id, request),
                        )
                        self._record_receipt(request.event_id, chat_id, "photo", receipt)
                        delivered.add(chat_id)
                    return (attempt, None) if delivered else (0, "muted_by_user")
                except Exception as exc:  # noqa: BLE001 - bounded isolated notification
                    error = f"Telegram 截圖傳送失敗：{type(exc).__name__}"
                    if attempt <= self.telegram_retries:
                        self._sleep(min(8.0, 0.5 * (2 ** (attempt - 1))))
            return self.telegram_retries + 1, error or clip_error

        try:
            future = self.telegram_dispatcher.submit(send)
        except RuntimeError as exc:
            self._safe_store_update(request.event_id, telegram_status="failed", last_error=type(exc).__name__)
            return
        self._safe_store_update(request.event_id, telegram_status="pending")
        future.add_done_callback(lambda completed: self._telegram_done(request.event_id, completed))

    def _send_with_retries(
        self, request: EventClipRequest, path: Path, caption: str,
        *, semantics: Future[SemanticResult] | None = None,
        clip_result: ClipAssemblyResult | None = None,
        references: Iterable[SegmentRecord] = (),
        prepared_since: float | None = None,
    ) -> tuple[int, str | None]:
        if not self._eligible_chat_ids(request):
            return 0, "muted_by_user"
        candidate = path
        validation_started = self._monotonic()
        temporary: Path | None = None
        source_validation = validate_media(path)
        if not source_validation.valid:
            return self._send_screenshot_fallback(
                request, caption,
                source_validation.error or "事件影片無法驗證，已停止傳送。",
            )
        if path.stat().st_size > self.telegram_max_bytes or not source_validation.telegram_compatible:
            temporary = self.output_directory / f"telegram_{request.event_id}.mp4"
            compressed = self._media_task(self.assembler.transcode_for_telegram, path, temporary)
            if (
                compressed.path is None
                or compressed.path.stat().st_size > self.telegram_max_bytes
                or compressed.validation is None
                or not compressed.validation.telegram_compatible
            ):
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                return self._send_screenshot_fallback(
                    request, caption,
                    "Telegram 相容影片轉換或大小驗證失敗，已改傳事件截圖。",
                )
            candidate = compressed.path
            candidate_validation = compressed.validation
        else:
            candidate_validation = source_validation
        error: str | None = None
        attempts = 0
        delivered: set[str] = set()
        reserved: set[str] = set()
        ready_monotonic = self._monotonic()
        trace = {"trace_id": str(uuid.uuid4()), "event_trigger_at": request.trigger_at.isoformat(),
                 "media_sendable_at": self._clock().isoformat(),
                 "validation_transcode_ms": max(0, (ready_monotonic - validation_started) * 1000),
                 "extra_wait_ms": 0, "wait_budget_ms": self.semantic_wait_seconds * 1000,
                 "caption_source": "local", "wait_status": "not_requested", "uploads": [],
                 "omitted_upload_count": 0}
        if prepared_since is not None:
            trace["video_prepare_ms"] = max(0, (ready_monotonic - prepared_since) * 1000)
        if isinstance(semantics, SemanticFuture):
            trace["semantic_job_id"] = semantics.semantic_job_id
        try:
            # 影片相容性檢查／轉碼已完成；僅從此處開始計算額外等待。
            semantic_result = None
            trace["wait_started_at"] = self._clock().isoformat()
            wait_started = self._monotonic()
            if semantics is not None:
                completed_before_wait = semantics.done() is True
                try:
                    semantic_result = semantics.result(timeout=self.semantic_wait_seconds)
                    if not isinstance(semantic_result, SemanticResult):
                        semantic_result = None
                        trace["wait_status"] = "bad_response"
                    else:
                        trace["wait_status"] = semantic_result.status
                except (TimeoutError, builtins.TimeoutError):
                    trace["wait_status"] = "timeout"
                    self._log("事件 AI 候選尚未完成；使用本機摘要傳送影片。")
                except CancelledError:
                    trace["wait_status"] = "cancelled"
                except Exception:  # noqa: BLE001 - optional analysis never prevents the native video
                    trace["wait_status"] = "failed"
                    self._log("事件 AI 候選無法使用；使用本機摘要傳送影片。")
                trace["extra_wait_ms"] = max(0, (self._monotonic() - wait_started) * 1000)
                if (semantic_result is not None and not completed_before_wait
                        and trace["extra_wait_ms"] > trace["wait_budget_ms"]):
                    semantic_result = None
                    trace["wait_status"] = "timeout"
            trace["wait_finished_at"] = self._clock().isoformat()
            if clip_result is not None:
                caption = self._merge_candidate_caption(
                    caption, semantic_result,
                    pet_registry_dir=self.output_directory.parent / "pets",
                    store=self.store,
                    event_id=request.event_id,
                )
                if semantic_result is not None and semantic_result.status == "completed":
                    if any(tag not in {"unknown","possible"} for _,tags in semantic_result.tracks for tag in tags):
                        trace["caption_source"] = "ai_candidate"
                    else:
                        trace["wait_status"] = "candidate_empty"
            for attempts in range(1, self.telegram_retries + 2):
                try:
                    payload = candidate.read_bytes()
                    if self.store is not None:
                        caption = self._preflight_refresh_caption(
                            request=request,
                            current_caption=caption,
                            clip_result=clip_result,
                            references=references,
                            semantics=semantic_result,
                        )
                    for chat_id in self.chat_ids:
                        if chat_id in delivered or not self._claim_notify(chat_id, request, reserved):
                            continue
                        upload = {"recipient_ref": hashlib.sha256(chat_id.encode()).hexdigest(),
                                  "attempt": attempts, "started_at": self._clock().isoformat(), "accepted": False}
                        upload_started = self._monotonic()
                        try:
                            feedback_markup = self._feedback_markup(chat_id, request)
                            receipt = self.notifier.send_video(
                                chat_id, payload, caption=caption,
                                reply_markup=feedback_markup,
                                width=candidate_validation.width,
                                height=candidate_validation.height,
                                duration=candidate_validation.duration_seconds, supports_streaming=True,
                            )
                            upload["finished_at"] = self._clock().isoformat()
                            upload["elapsed_ms"] = max(0, (self._monotonic() - upload_started) * 1000)
                            self._record_receipt(request.event_id, chat_id, "video", receipt, feedback_markup=feedback_markup)
                            upload["accepted"] = True
                        finally:
                            if "finished_at" not in upload:
                                upload["finished_at"] = self._clock().isoformat()
                                upload["elapsed_ms"] = max(0, (self._monotonic() - upload_started) * 1000)
                            if len(trace["uploads"]) < 256:
                                trace["uploads"].append(upload)
                            else:
                                trace["omitted_upload_count"] += 1
                        delivered.add(chat_id)
                    return (attempts, None) if delivered else (0, "muted_by_user")
                except Exception as exc:  # noqa: BLE001 - bounded network retry
                    error = f"Telegram 影片傳送失敗：{type(exc).__name__}"
                    if attempts <= self.telegram_retries:
                        self._sleep(min(8.0, 0.5 * (2 ** (attempts - 1))))
        finally:
            # Timing persistence follows upload, and cannot trigger a delivered-message retry.
            if self.store is not None:
                try:
                    self.store.record_delivery_trace(request.event_id, trace)
                except Exception as exc:  # noqa: BLE001 - preserve media delivery if telemetry fails
                    self._log(f"⚠️ 通知時序未能保存：{type(exc).__name__}。")
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return attempts, error

    def _send_screenshot_fallback(
        self,
        request: EventClipRequest,
        caption: str,
        reason: str,
    ) -> tuple[int, str | None]:
        if not request.screenshot_bytes:
            return 1, reason
        if not self._eligible_chat_ids(request):
            return 0, "muted_by_user"
        try:
            delivered = 0
            for chat_id in self.chat_ids:
                if not self._claim_notify(chat_id, request, set()):
                    continue
                receipt = self.notifier.send_photo(
                    chat_id,
                    request.screenshot_bytes,
                    caption=caption + "\n⚠️ 影片相容性驗證未通過，改傳事件截圖。",
                    reply_markup=self._feedback_markup(chat_id, request),
                )
                self._record_receipt(request.event_id, chat_id, "photo", receipt)
                delivered += 1
        except Exception as exc:  # noqa: BLE001 - notification failure must not affect local clip
            return 1, f"{reason} 截圖通知失敗：{type(exc).__name__}"
        if not delivered:
            return 0, "muted_by_user"
        return 1, reason

    def _record_receipt(self, event_id: str, chat_id: str, kind: str, receipt: object, *, feedback_markup=None) -> None:
        message_id = getattr(receipt, "message_id", None)
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("Telegram 回應缺少有效 message_id，無法確認收件。")
        if self.store is None:
            return
        try:
            self.store.record_telegram_receipt(
                event_id, hashlib.sha256(chat_id.encode("utf-8")).hexdigest(), kind, message_id,
            )
            if kind == "video" and isinstance(feedback_markup,dict):
                import re
                refs = [match.group(1) for row in feedback_markup.get("inline_keyboard",[]) for button in row
                        if (match := re.fullmatch(r"trk:([0-9a-f-]{36}):open", str(button.get("callback_data",""))))]
                recipient = hashlib.sha256(chat_id.encode()).hexdigest()
                allowed = {row["event_id"] for row in self.store.media_feedback_tracks(event_id)}
                if len(refs) == len(set(refs)) and set(refs).issubset(allowed):
                    for ordinal,behavior in enumerate(refs,1):
                        self.store._write("INSERT OR IGNORE INTO telegram_media_track_refs(media_event_id,recipient_ref,message_id,behavior_event_id,ordinal) VALUES(?,?,?,?,?)",(event_id,recipient,message_id,behavior,ordinal))
        except Exception as exc:  # noqa: BLE001 - never resend a delivered message on DB failure
            self._log(f"⚠️ Telegram 已送達，但收件回條保存失敗：{type(exc).__name__}。")

    def _telegram_done(self, event_id: str, future: Future[tuple[int, str | None]]) -> None:
        try:
            attempts, error = future.result()
        except Exception as exc:  # noqa: BLE001 - preserve local event
            self._safe_store_update(event_id, telegram_status="failed", telegram_attempts=1, last_error=type(exc).__name__)
            return
        self._safe_store_update(
            event_id,
            telegram_status="muted" if error == "muted_by_user" else ("sent" if error is None else "failed"),
            telegram_attempts=attempts,
            last_error=None if error == "muted_by_user" else error,
        )

    def _claim_notify(self, chat_id: str, request: EventClipRequest, reserved: set[str]) -> bool:
        preferences = self.cat_notification_preferences
        if preferences is None or request.event_type != "cat_detected":
            return True
        if chat_id in reserved:
            state = preferences.status(chat_id)
            return (state["enabled"] is True and state["muted"] is False
                    and state["storage_error"] is False)
        if not preferences.claim_delivery(chat_id, request.event_type):
            return False
        reserved.add(chat_id)
        return True

    def _can_notify(self, chat_id: str, request: EventClipRequest) -> bool:
        preferences = self.cat_notification_preferences
        return preferences is None or preferences.should_send(chat_id, request.event_type)

    def _feedback_markup(self, chat_id: str, request: EventClipRequest) -> dict[str, object] | None:
        preferences = self.cat_notification_preferences
        if (request.event_type != "cat_detected" or request.trigger_origin != "detector"
                or preferences is None or not preferences.feedback_enabled(chat_id)):
            return None
        if self.store is not None:
            try:
                tracks = self.store.media_feedback_tracks(request.event_id)
                if isinstance(tracks, list) and tracks:
                    return {"inline_keyboard": [[{
                        "text": f"回報貓咪 #{index}",
                        "callback_data": f"trk:{row['event_id']}:open",
                    }] for index, row in enumerate(tracks, 1)]}
            except Exception as exc:  # noqa: BLE001 - optional feedback cannot block video delivery
                self._log(f"⚠️ 逐貓回報入口暫時無法建立：{type(exc).__name__}。")
        try:
            pets = PetRegistry(self.output_directory.parent / "pets").list_pets(enabled_only=True)
        except (OSError, RuntimeError, ValueError):
            pets = []
        return first_round(request.event_id, pets) if pets else self._cat_feedback_keyboard(request.event_id)

    def _eligible_chat_ids(self, request: EventClipRequest) -> tuple[str, ...]:
        return tuple(chat_id for chat_id in self.chat_ids if self._can_notify(chat_id, request))

    def _is_cooled_down(self, request: EventClipRequest) -> bool:
        with self._lock:
            previous = self._last_by_type.get(request.event_type)
            if previous is None:
                return False
            elapsed = (request.trigger_at - previous).total_seconds()
            return elapsed < max(self.cooldown_seconds, self.merge_window_seconds)

    def _wait_until(self, target: datetime) -> None:
        while not self._stopping:
            remaining = (target - self._normalise_now(self._clock())).total_seconds()
            if remaining <= 0:
                return
            time.sleep(min(0.25, remaining))

    @staticmethod
    def _normalise_now(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def _acquire_missing(self, target: list[SegmentRecord], candidates: Iterable[SegmentRecord]) -> None:
        known = {record.path for record in target}
        missing = [record for record in candidates if record.path not in known]
        target.extend(self.ring_buffer.index.acquire(missing))

    def _acquire_window_with_grace(
        self,
        target: list[SegmentRecord],
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> None:
        """Wait briefly for the active FFmpeg segment to become immutable.

        Stream-copy segmentation can only cut on keyframes. A long GOP may
        leave the only useful TS file open beyond the requested post window.
        The bounded wait runs solely in the clip worker and never blocks media
        ingest, inference, or the notification dispatcher.
        """

        deadline = time.monotonic() + self.segment_finalize_wait_seconds
        tolerance = timedelta(seconds=self.ring_buffer.segment_seconds)
        while True:
            self.ring_buffer.scan()
            self._acquire_missing(target, self.ring_buffer.index.between(start_at, end_at))
            latest_end = max((record.end_at for record in target), default=None)
            if latest_end is not None and latest_end >= end_at - tolerance:
                return
            # No writer can add another segment. Assemble the best available
            # window immediately instead of turning a finite/local source or
            # a pre-populated test ring into a needless grace-period wait.
            if not self.ring_buffer.has_active_writer:
                return
            if self._stopping or time.monotonic() >= deadline:
                return
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))

    def _caption(
        self,
        request: EventClipRequest,
        result: ClipAssemblyResult,
        references: Iterable[SegmentRecord],
        *, semantics: SemanticResult | None = None,
    ) -> str:
        records = tuple(references)
        start = min((record.start_at for record in records), default=request.trigger_at)
        end = start + timedelta(seconds=result.actual_duration_seconds or 0.0)
        pet_id = request.pet_id
        if self.store is not None and request.track_id is not None:
            try:
                saved = self.store.get_media_event(request.event_id)
                if (saved is not None and str(saved["track_id"]) == str(request.track_id)
                        and saved["pet_id"] not in {None, "unknown"}):
                    pet_id = saved["pet_id"]
            except (sqlite3.Error, OSError, RuntimeError):
                pass
        pet_name = None
        if pet_id and pet_id != "unknown":
            try:
                profile = PetRegistry(self.output_directory.parent / "pets").get_pet(pet_id)
                if profile and profile.enabled:
                    pet_name = profile.display_name
            except (OSError, ValueError, TypeError):
                pass
        pet = pet_name or "貓咪"
        source_suffix = " DINOv2辨識" if pet_name else ""
        headline_text = f"{pet}剛剛出現在畫面。" if request.event_type == "cat_detected" else "事件影片已就緒。"

        if self.store is not None and request.event_id is not None:
            try:
                with self.store._connection_lock:
                    row = self.store.connection.execute(
                        "SELECT event_tags_json, evidence_json FROM behavior_events WHERE event_id=? OR track_id=? ORDER BY started_at DESC LIMIT 1",
                        (str(request.event_id), str(request.track_id or request.event_id)),
                    ).fetchone()
                    if row:
                        t_json, ev_json = row[0], row[1]
                        tags_found = []
                        posture_candidate = None
                        if t_json:
                            try:
                                tags_found = json.loads(t_json)
                            except Exception:
                                pass
                        if ev_json:
                            try:
                                ev = json.loads(ev_json)
                                p_data = ev.get("posture")
                                if isinstance(p_data, dict) and p_data.get("posture") in {"sitting", "standing", "lying"}:
                                    posture_candidate = p_data["posture"]
                            except Exception:
                                pass
                        if tags_found or posture_candidate:
                            headline_text = _format_natural_cat_headline(pet, tags_found, posture_candidate)
                            source_suffix = " DINOv2辨識"
            except Exception:
                pass

        headline = f"🐈 {headline_text}" if not headline_text.startswith("🐈") else headline_text

        if semantics is not None and semantics.status == "completed":
            cloud_caption = getattr(semantics, "caption", None)
            if cloud_caption and isinstance(cloud_caption, str) and cloud_caption.strip():
                c_text = cloud_caption.strip()
                if not c_text.startswith("🐈"):
                    c_text = f"🐈 {c_text}"
                headline = c_text
                source_suffix = " 雲端辨識"
            else:
                lines = []
                for index, (_track, tags) in enumerate(semantics.tracks[:4], 1):
                    tags = tuple(tag for tag in tags if tag not in {"unknown", "possible"})
                    if tags:
                        subject = pet if len(semantics.tracks) == 1 else f"貓咪 #{index}"
                        lines.append(_format_natural_cat_headline(subject, tags))
                if lines:
                    headline = "\n".join(f"🐈 {l}" if not l.startswith("🐈") else l for l in lines)
                    source_suffix = " 雲端辨識"

        return (f"{headline}\n"
                f"{request.trigger_at.astimezone().strftime('%m/%d %H:%M')}｜"
                f"影片 {start.astimezone().strftime('%H:%M:%S')}～{end.astimezone().strftime('%H:%M:%S')}\n"
                f"喵喵守護者+{source_suffix}")

    def _preflight_refresh_caption(
        self,
        request: EventClipRequest,
        current_caption: str,
        clip_result: Any = None,
        references: Iterable[SegmentRecord] = (),
        semantics: SemanticResult | None = None,
    ) -> str:
        """Pre-flight check: Re-query database right before dispatch to catch any identity resolved during transcoding/waiting."""
        if self.store is None:
            return current_caption

        try:
            latest_pet_id = None
            tags_found = []
            posture_candidate = None

            with self.store._connection_lock:
                row = self.store.connection.execute(
                    "SELECT pet_id, track_id FROM media_events WHERE event_id=?",
                    (request.event_id,),
                ).fetchone()
                if row and row[0] not in {None, "unknown"}:
                    latest_pet_id = row[0]

                track_ident = request.track_id or (row[1] if row else None) or request.event_id
                b_row = self.store.connection.execute(
                    "SELECT pet_id, event_tags_json, evidence_json FROM behavior_events WHERE event_id=? OR track_id=? ORDER BY started_at DESC LIMIT 1",
                    (str(request.event_id), str(track_ident)),
                ).fetchone()
                if b_row:
                    if not latest_pet_id and b_row[0] not in {None, "unknown"}:
                        latest_pet_id = b_row[0]
                    if b_row[1]:
                        try:
                            tags_found = json.loads(b_row[1])
                        except Exception:
                            pass
                    if b_row[2]:
                        try:
                            ev = json.loads(b_row[2])
                            p_data = ev.get("posture")
                            if isinstance(p_data, dict) and p_data.get("posture") in {"sitting", "standing", "lying"}:
                                posture_candidate = p_data["posture"]
                        except Exception:
                            pass

            if not latest_pet_id:
                return current_caption

            pet_name = None
            try:
                reg = PetRegistry(self.output_directory.parent / "pets")
                prof = reg.get_pet(latest_pet_id)
                if prof and prof.enabled:
                    pet_name = prof.display_name
            except Exception:
                pass

            if not pet_name:
                return current_caption

            first_line, sep, rest = current_caption.partition("\n")
            if pet_name in first_line and "出現在畫面" not in first_line:
                return current_caption

            new_headline = _format_natural_cat_headline(pet_name, tags_found, posture_candidate)
            if not new_headline.startswith("🐈"):
                new_headline = f"🐈 {new_headline}"

            time_line, sep2, suffix_line = rest.partition("\n") if sep else (rest, "", "")
            if "喵喵守護者+" in suffix_line:
                if "辨識" not in suffix_line:
                    suffix_line = f"{suffix_line} DINOv2辨識"
            else:
                suffix_line = "喵喵守護者+ DINOv2辨識"

            refreshed = f"{new_headline}\n{time_line}\n{suffix_line}" if time_line else f"{new_headline}\n{suffix_line}"
            return refreshed
        except Exception:
            return current_caption

    def elevate_event_caption(
        self,
        event_id: str,
        pet_id: str,
        *,
        track_id: str | int | None = None,
        posture: str | None = None,
        tags: tuple[str, ...] = (),
    ) -> bool:
        """Post-delivery caption elevation: edit sent Telegram message caption silently when identity is resolved."""
        if not self.telegram_enabled or self.notifier is None or not self.store:
            return False
        if not pet_id or pet_id == "unknown":
            return False

        try:
            pet_name = pet_id
            try:
                reg = PetRegistry(self.output_directory.parent / "pets")
                p = reg.get_pet(pet_id)
                if p and p.enabled and p.display_name:
                    pet_name = p.display_name
            except Exception:
                pass

            with self.store._connection_lock:
                receipts = self.store.connection.execute(
                    "SELECT recipient_ref, message_id FROM telegram_receipts WHERE event_id=? AND media_kind='video'",
                    (event_id,),
                ).fetchall()

            if not receipts and track_id is not None:
                with self.store._connection_lock:
                    receipts = self.store.connection.execute(
                        """SELECT r.recipient_ref, r.message_id 
                           FROM telegram_receipts r 
                           JOIN media_events m ON r.event_id = m.event_id 
                           WHERE m.track_id=? AND r.media_kind='video'""",
                        (str(track_id),),
                    ).fetchall()

            if not receipts:
                return False

            chat_map = {hashlib.sha256(cid.encode("utf-8")).hexdigest(): cid for cid in self.chat_ids}

            if not posture and self.store:
                try:
                    with self.store._connection_lock:
                        row = self.store.connection.execute(
                            "SELECT event_tags_json, evidence_json FROM behavior_events WHERE event_id=? OR track_id=? ORDER BY started_at DESC LIMIT 1",
                            (str(event_id), str(track_id or event_id)),
                        ).fetchone()
                        if row:
                            if row[0] and not tags:
                                try:
                                    tags = tuple(json.loads(row[0]))
                                except Exception:
                                    pass
                            if row[1]:
                                try:
                                    ev = json.loads(row[1])
                                    p_data = ev.get("posture")
                                    if isinstance(p_data, dict) and p_data.get("posture") in {"sitting", "standing", "lying"}:
                                        posture = p_data["posture"]
                                except Exception:
                                    pass
                except Exception:
                    pass

            headline = _format_natural_cat_headline(pet_name, tags, posture)
            if not headline.startswith("🐈"):
                headline = f"🐈 {headline}"

            media_row = self.store.get_media_event(event_id)
            if media_row and media_row["trigger_at"]:
                trig = datetime.fromisoformat(media_row["trigger_at"]) if isinstance(media_row["trigger_at"], str) else media_row["trigger_at"]
                if trig.tzinfo is None:
                    trig = trig.replace(tzinfo=timezone.utc)
                time_str = trig.astimezone().strftime("%m/%d %H:%M")
                caption_text = f"{headline}\n{time_str}｜喵喵守護者+ DINOv2辨識"
            else:
                caption_text = f"{headline}\n喵喵守護者+ DINOv2辨識"

            fake_req = EventClipRequest(event_id=event_id, trigger_at=datetime.now(timezone.utc),
                                        event_type="cat_detected", source_id=(media_row["source_id"] if media_row and media_row["source_id"] else "camera"), trigger_origin="detector")

            for ref, msg_id in receipts:
                chat_id = chat_map.get(ref)
                if not chat_id:
                    continue
                try:
                    markup = self._feedback_markup(chat_id, fake_req)
                    self.notifier.edit_message_caption(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=caption_text[:1024],
                        reply_markup=markup,
                    )
                    self._log(f"✨ Telegram 訊息 #{msg_id} 標題已自動平滑升級為：{pet_name}")
                except Exception as exc:
                    self._log(f"⚠️ Telegram 標題升級失敗 (msg {msg_id})：{type(exc).__name__}")

            return True
        except Exception as exc:
            self._log(f"⚠️ 延遲身分自動平滑升級異常：{type(exc).__name__}")
            return False

    def update_event_semantics(self, event_id: str, semantics: SemanticResult) -> bool:
        """Post-delivery caption elevation from cloud AI Multimodal semantics."""
        if not self.telegram_enabled or self.notifier is None or not self.store:
            return False
        if semantics is None or semantics.status != "completed":
            return False

        try:
            with self.store._connection_lock:
                receipts = self.store.connection.execute(
                    "SELECT recipient_ref, message_id FROM telegram_receipts WHERE event_id=? AND media_kind='video'",
                    (event_id,),
                ).fetchall()

            if not receipts:
                return False

            chat_map = {hashlib.sha256(cid.encode("utf-8")).hexdigest(): cid for cid in self.chat_ids}

            media_row = self.store.get_media_event(event_id)
            time_str = ""
            if media_row and media_row["trigger_at"]:
                trig = datetime.fromisoformat(media_row["trigger_at"]) if isinstance(media_row["trigger_at"], str) else media_row["trigger_at"]
                if trig.tzinfo is None:
                    trig = trig.replace(tzinfo=timezone.utc)
                time_str = trig.astimezone().strftime("%m/%d %H:%M")

            base_caption = f"🐈 貓咪剛剛出現在畫面。\n{time_str}\n喵喵守護者+"
            elevated = self._merge_candidate_caption(
                base_caption, semantics,
                pet_registry_dir=self.output_directory.parent / "pets",
                store=self.store,
                event_id=event_id,
            )

            fake_req = EventClipRequest(event_id=event_id, trigger_at=datetime.now(timezone.utc),
                                        event_type="cat_detected", source_id=(media_row["source_id"] if media_row and media_row["source_id"] else "camera"), trigger_origin="detector")

            for ref, msg_id in receipts:
                chat_id = chat_map.get(ref)
                if not chat_id:
                    continue
                try:
                    markup = self._feedback_markup(chat_id, fake_req)
                    self.notifier.edit_message_caption(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=elevated[:1024],
                        reply_markup=markup,
                    )
                    self._log(f"✨ Telegram 訊息 #{msg_id} 雲端語意已自動更新")
                except Exception as exc:
                    self._log(f"⚠️ Telegram 雲端語意更新失敗 (msg {msg_id})：{type(exc).__name__}")

            return True
        except Exception as exc:
            self._log(f"⚠️ 雲端語意平滑升級異常：{type(exc).__name__}")
            return False

    @classmethod
    def _merge_candidate_caption(
        cls,
        local_caption: str,
        semantics: SemanticResult | None,
        *,
        pet_registry_dir: Path | None = None,
        store: Any = None,
        event_id: str | None = None,
    ) -> str:
        """Merge cloud semantic result (actions, open narrative, and identity) into final caption."""
        if semantics is None or semantics.status != "completed":
            return local_caption
        first, separator, rest = local_caption.partition("\n")

        local_pet = "貓咪"
        if first.startswith("🐈 "):
            for marker in ("現在", "剛剛", "正"):
                idx = first.find(marker)
                if idx > 2:
                    local_pet = first[2:idx].strip()
                    break

        cloud_pet_name = None
        cand_map = dict(getattr(semantics, "identity_candidates", ()))
        if cand_map and pet_registry_dir is not None:
            try:
                reg = PetRegistry(pet_registry_dir)
                for _tid, cand in cand_map.items():
                    c_pid = cand.get("pet_id")
                    if c_pid and c_pid != "unknown":
                        prof = reg.get_pet(c_pid)
                        if prof and prof.enabled:
                            cloud_pet_name = prof.display_name
                            if store is not None and event_id is not None:
                                try:
                                    store.connection.execute(
                                        "UPDATE media_events SET pet_id=? WHERE event_id=? AND pet_id='unknown'",
                                        (c_pid, event_id),
                                    )
                                    store.connection.commit()
                                except Exception:
                                    pass
                            break
            except Exception:
                pass

        pet = cloud_pet_name or local_pet
        time_line, sep2, _tail = rest.partition("\n") if separator else (rest, "", "")

        # 1. 優先使用雲端開放性描述 (如「Saki現在躺在床上休息，好舒服～」)
        cloud_caption = getattr(semantics, "caption", None)
        if cloud_caption and isinstance(cloud_caption, str) and cloud_caption.strip():
            text = cloud_caption.strip()
            if cloud_pet_name and ("貓咪" in text or (local_pet != "貓咪" and local_pet in text)):
                text = text.replace("貓咪", cloud_pet_name)
                if local_pet != "貓咪":
                    text = text.replace(local_pet, cloud_pet_name)
            if not text.startswith("🐈"):
                text = f"🐈 {text}"
            return f"{text}\n{time_line}\n喵喵守護者+ 雲端辨識"

        # 2. 若無開放文字描述，使用自然語意化格式
        lines = []
        for index, (track_id, raw_tags) in enumerate(semantics.tracks[:4], 1):
            tags = tuple(tag for tag in raw_tags if tag not in {"unknown", "possible"})
            if tags:
                cand_info = cand_map.get(track_id)
                track_pet = pet
                if cand_info and cand_info.get("pet_id") and cand_info.get("pet_id") != "unknown" and pet_registry_dir:
                    try:
                        p_obj = PetRegistry(pet_registry_dir).get_pet(cand_info["pet_id"])
                        if p_obj and p_obj.enabled:
                            track_pet = p_obj.display_name
                    except Exception:
                        pass
                subject = track_pet if len(semantics.tracks) == 1 else f"貓咪 #{index}"
                lines.append(_format_natural_cat_headline(subject, tags))

        if not lines:
            if cloud_pet_name and local_pet == "貓咪":
                new_first = f"🐈 {cloud_pet_name}剛剛出現在畫面。"
                return f"{new_first}\n{time_line}\n喵喵守護者+ 雲端辨識"
            return local_caption

        headline = "\n".join(f"🐈 {l}" if not l.startswith("🐈") else l for l in lines)
        return f"{headline}\n{time_line}\n喵喵守護者+ 雲端辨識"

    @staticmethod
    def _cat_feedback_keyboard(event_id: str) -> dict[str, object]:
        return {"inline_keyboard": [[
            {"text": "✅ 是貓咪", "callback_data": f"cat:{event_id}:yes"},
            {"text": "❌ 不是貓咪", "callback_data": f"cat:{event_id}:no"},
        ]]}

    def _save_snapshot(self, request: EventClipRequest) -> Path | None:
        if not request.screenshot_bytes:
            return None
        destination = self.output_directory / f"event_{request.event_id}.jpg"
        destination.write_bytes(request.screenshot_bytes)
        return destination

    def _safe_create_event(self, request: EventClipRequest) -> None:
        if self.store is None:
            return
        try:
            self.store.create_media_event(
                event_id=request.event_id,
                event_type=request.event_type,
                trigger_at=request.trigger_at,
                source_id=request.source_id,
                track_id=request.track_id,
                pet_id=request.pet_id or "unknown",
                confidence=request.confidence,
                requested_pre_seconds=request.pre_seconds,
                requested_post_seconds=request.post_seconds,
                trigger_origin=request.trigger_origin,
                model_version=request.model_version,
                model_sha256=request.model_sha256,
                confidence_threshold=request.confidence_threshold,
                track_ids=request.track_ids,
            )
        except Exception as exc:  # noqa: BLE001 - DB cannot stop media path
            self._log(f"⚠️ SQLite 事件建立失敗：{type(exc).__name__}")

    def _safe_store_update(self, event_id: str, **values: object) -> None:
        if self.store is None:
            return
        try:
            self.store.update_media_event(event_id, **values)
        except Exception as exc:  # noqa: BLE001 - DB cannot stop media path
            self._log(f"⚠️ SQLite 事件更新失敗：{type(exc).__name__}")

    @property
    def media_busy(self):
        with self._media_lock:
            return self._media_tasks > 0

    def _media_task(self, operation, *args):
        with self._media_lock:
            self._media_tasks += 1
        try:
            return operation(*args)
        finally:
            with self._media_lock:
                self._media_tasks -= 1

    def _log(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger(message)
            except Exception:  # noqa: BLE001 - diagnostics must not stop worker
                self.logger = None
