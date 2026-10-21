"""V0.2B 行為優先的單一 FramePacket 入口。

主路徑固定為：追蹤／動作與行為 → 事件落地／通知 → 背景身分解析 → 統計補寫。
背景 DINO 工作不會被拿來當作建立行為事件的前置條件。
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, ClassVar

from meowkeeper.core.analytics import PerformanceMetrics
from meowkeeper.core.behavior import (
    BehaviorEngine,
    BehaviorEvent,
    BehaviorFeatureExtractor,
    BehaviorObservation,
)
from meowkeeper.core.identity import (
    BackgroundIdentityResolver,
    IdentityClassifier,
    IdentityPrediction,
    IdentityResolution,
    TemporalIdentity,
)
from meowkeeper.core.identity.feedback_learning import (
    FeedbackIdentityLearner,
    FeedbackLearningPolicy,
    save_track_evidence,
)
from meowkeeper.core.inference import (
    GPUInferenceScheduler,
    GPUResourceMonitor,
    InferenceLoadController,
    InferencePriority,
)
from meowkeeper.core.inference.timings import StageTimings
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.core.notifications.dispatcher import NotificationDispatcher
from meowkeeper.core.notifications.telegram import TelegramNotifier, TelegramSendResult
from meowkeeper.core.notifications.track_evidence import numbered_track_sheet
from meowkeeper.core.tracking import TrackHistory, TrackingBackend
from meowkeeper.core.tracking.base import TrackObservation
from meowkeeper.domain.settings import MX250_MINIMUM
from meowkeeper.storage import SQLiteStore


@dataclass(frozen=True)
class V02TrackResult:
    observation: TrackObservation
    pet_id: str | None
    pet_name: str | None
    identity_confidence: float
    behavior_state: str
    behavior_confidence: float
    events: tuple[BehaviorEvent, ...] = ()
    identity_status: str = "unknown"
    identity_pending: bool = False
    feedback_event: BehaviorEvent | None = None


@dataclass(frozen=True)
class V02FrameResult:
    frame: FramePacket
    tracks: tuple[V02TrackResult, ...]
    finalized_events: tuple[BehaviorEvent, ...] = ()
    notifications: tuple[TelegramSendResult, ...] = ()
    warnings: tuple[str, ...] = ()


class V02AnalysisPipeline:
    """只接收既有單一媒體入口的 FramePacket，不另開 RTSP。"""

    _BEHAVIOR_LABELS: ClassVar[dict[str, str]] = {
        "sleeping": "睡覺", "resting": "休息", "eating": "進食",
        "drinking": "喝水", "playing": "玩耍", "grooming": "理毛",
        "litter_box": "使用貓砂", "other_active": "其他活動",
        "out_of_view": "不在畫面", "unknown": "無法辨識",
    }
    _MOTION_LABELS: ClassVar[dict[str, str]] = {
        "stationary": "靜止", "low_motion": "低度移動", "walking": "行走",
        "running": "快速跑動", "jumping": "跳躍",
    }

    def __init__(
        self,
        tracker: TrackingBackend,
        identity_classifier: IdentityClassifier,
        *,
        temporal_identity: TemporalIdentity | None = None,
        history: TrackHistory | None = None,
        feature_extractor: BehaviorFeatureExtractor,
        behavior_engine: BehaviorEngine,
        store: SQLiteStore | None = None,
        notifier: TelegramNotifier | None = None,
        chat_ids: tuple[str, ...] = (),
        feedback_preferences: Any | None = None,
        identity_interval_seconds: float = 10.0,
        identity_low_confidence: float = 0.62,
        behavior_interval_seconds: float = 1.0,
        metrics: PerformanceMetrics | None = None,
        inference_scheduler: GPUInferenceScheduler | None = None,
        identity_resolver: BackgroundIdentityResolver | None = None,
        notification_dispatcher: NotificationDispatcher | None = None,
        load_controller: InferenceLoadController | None = None,
        logger: Callable[[str], None] | None = None,
        feedback_learning_policy: FeedbackLearningPolicy | None = None,
    ) -> None:
        self.tracker = tracker
        self.identity_classifier = identity_classifier
        self.temporal_identity = temporal_identity or TemporalIdentity()
        self.history = history or TrackHistory()
        self.feature_extractor = feature_extractor
        self.behavior_engine = behavior_engine
        self.store = store
        self.notifier = notifier
        self.chat_ids = tuple(chat_ids)
        self.feedback_preferences = feedback_preferences
        if identity_interval_seconds <= 0:
            raise ValueError("身分 embedding 採樣間隔必須大於零。")
        if behavior_interval_seconds <= 0:
            raise ValueError("行為分析採樣間隔必須大於零。")
        self.identity_interval_seconds = identity_interval_seconds
        self.identity_low_confidence = identity_low_confidence
        self.behavior_interval_seconds = behavior_interval_seconds
        self.metrics = metrics or PerformanceMetrics()
        self.logger = logger
        self.inference_scheduler = inference_scheduler or GPUInferenceScheduler(
            max_pending_tasks=2,
            resource_monitor=GPUResourceMonitor(),
            logger=logger,
        )
        self._owns_scheduler = inference_scheduler is None
        self.identity_resolver = identity_resolver or BackgroundIdentityResolver(
            identity_classifier,
            scheduler=self.inference_scheduler,
            recheck_interval_seconds=identity_interval_seconds,
            maximum_pending_tasks=MX250_MINIMUM.identity_max_pending_tasks,
            task_timeout_seconds=MX250_MINIMUM.identity_timeout_seconds,
            minimum_crop_pixels=MX250_MINIMUM.identity_minimum_crop_pixels,
            low_confidence_threshold=identity_low_confidence,
            logger=logger,
        )
        self.load_controller = load_controller or InferenceLoadController(
            normal_fps=MX250_MINIMUM.normal_yolo_fps,
            active_fps=MX250_MINIMUM.active_yolo_fps,
            quiet_fps=MX250_MINIMUM.quiet_yolo_fps,
            active_seconds=MX250_MINIMUM.active_mode_seconds,
            monitor=self.inference_scheduler.resource_monitor,
        )
        if notifier is not None and self.chat_ids:
            self.notification_dispatcher = notification_dispatcher or NotificationDispatcher(
                logger=logger
            )
            self._owns_notification_dispatcher = notification_dispatcher is None
        else:
            self.notification_dispatcher = notification_dispatcher
            self._owns_notification_dispatcher = notification_dispatcher is None
        self._source_id: str | None = None
        self._generation = 0
        self._seen_tracks: set[str | int] = set()
        self._identity_cache: dict[str | int, tuple[datetime, IdentityPrediction]] = {}
        self._stable_identity: dict[str | int, IdentityPrediction] = {}
        self._behavior_cache: dict[str | int, tuple[datetime, BehaviorObservation]] = {}
        self._persisted_event_ids: set[str] = set()
        self._learning_policy = feedback_learning_policy or FeedbackLearningPolicy()
        registry = getattr(identity_classifier, "registry", None)
        self._feedback_learner = (
            FeedbackIdentityLearner(store, registry, identity_classifier.embedder, self._learning_policy)
            if store is not None and registry is not None else None
        )
        self._learning_future = None
        self._learning_checked_at = 0.0
        self._crop_evidence_saved: set[str] = set()
        self.timings = StageTimings()

    def process(self, frame: FramePacket) -> V02FrameResult:
        samples: dict[str, float] = {}
        started = time.perf_counter()
        try:
            return self._process_frame(frame, samples)
        finally:
            samples["v02_frame_pipeline_total_ms"] = max(0.0, time.perf_counter() - started) * 1000
            self.timings.observe(samples)

    def _process_frame(self, frame: FramePacket, samples: dict[str, float]) -> V02FrameResult:
        warnings: list[str] = []
        stage = time.perf_counter()
        self._drain_identity_results()
        self.load_controller.set_mode_for_resource()
        if self._source_id is not None and self._source_id != frame.source_url:
            self.reset("source_changed")
            warnings.append("來源變更，已重置短期 track 與身分投票。")
        self._source_id = frame.source_url
        samples["v02_preflight_ms"] = max(0.0, time.perf_counter() - stage) * 1000

        stage = time.perf_counter()
        if self.load_controller is not None and not self.load_controller.should_analyze(frame.captured_at):
            self.metrics.record_frame(dropped=True)
            self.metrics.ai_frames_dropped += 1
            warnings.append("ℹ️ GPU 負載控制略過一張普通 AI 影格；環形錄影仍持續。")
            samples["v02_sampling_gate_ms"] = max(0.0, time.perf_counter() - stage) * 1000
            return V02FrameResult(frame, (), warnings=tuple(warnings))
        samples["v02_sampling_gate_ms"] = max(0.0, time.perf_counter() - stage) * 1000

        self.metrics.record_frame()
        started_inference = time.perf_counter()
        try:
            observations = self.inference_scheduler.run_sync(
                lambda: self.tracker.update(frame),
                priority=InferencePriority.HIGH,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            samples["v02_yolo_scheduler_roundtrip_ms"] = max(0.0, time.perf_counter() - started_inference) * 1000
            warnings.append(f"⚠️ 主要影像推論失敗：{type(exc).__name__}；保留影音接收。")
            return V02FrameResult(frame, (), warnings=tuple(warnings))
        samples["v02_yolo_scheduler_roundtrip_ms"] = max(0.0, time.perf_counter() - started_inference) * 1000
        self.metrics.record_yolo(
            latency_ms=(time.perf_counter() - started_inference) * 1000.0,
            backend=getattr(self.tracker, "inference_backend", None),
        )
        self.load_controller.observe_activity(
            cat_seen=bool(observations),
            active=bool(observations),
        )

        tracks: list[V02TrackResult] = []
        finalized: list[BehaviorEvent] = []
        for observation in observations:
            stage = time.perf_counter()
            self.history.update(observation)
            identity = self._stable_identity.get(observation.track_id)
            if identity is None:
                identity = self._unknown_prediction(observation.track_id)

            # Priority 1: use inexpensive track/zone features first.  This is
            # intentionally before the low-priority identity submission.
            feature = self.feature_extractor.extract(
                observation,
                pet_id=identity.predicted_pet_id,
                identity_confidence=identity.similarity,
            )
            samples["v02_track_history_feature_ms"] = samples.get("v02_track_history_feature_ms", 0.0) + max(
                0.0, time.perf_counter() - stage
            ) * 1000
            stage = time.perf_counter()
            previous_behavior = self._behavior_cache.get(observation.track_id)
            should_refresh_behavior = previous_behavior is None or (
                previous_behavior is not None
                and (feature.observed_at - previous_behavior[0]).total_seconds()
                >= self.behavior_interval_seconds
            )
            if should_refresh_behavior:
                behavior, completed = self.behavior_engine.observe(feature)
                self._behavior_cache[observation.track_id] = (feature.observed_at, behavior)
            else:
                behavior = previous_behavior[1]
                completed = ()
            self.metrics.record_behavior(event_created=bool(completed))
            if identity.predicted_pet_id is None:
                self.metrics.unknown_identity_tracks += 1
            samples["v02_behavior_update_ms"] = samples.get("v02_behavior_update_ms", 0.0) + max(
                0.0, time.perf_counter() - stage
            ) * 1000

            # Persist the event start immediately, even when identity is
            # unknown.  Later writes update the same event_id.
            stage = time.perf_counter()
            active_event = self.behavior_engine.active_event(observation.track_id)
            if (
                active_event is not None
                and self.store is not None
                and active_event.event_id not in self._persisted_event_ids
            ):
                self.store.save_behavior_event(active_event)
                self._persisted_event_ids.add(active_event.event_id)

            finalized.extend(completed)
            if self.store is not None and observation.track_id not in self._seen_tracks:
                self.store.save_track(
                    observation.track_id,
                    source_id=observation.source_id,
                    started_at=observation.captured_at,
                    pet_id=identity.predicted_pet_id,
                    confidence=observation.confidence,
                )
                self._seen_tracks.add(observation.track_id)
            if self.store is not None:
                for event in completed:
                    self.store.save_behavior_event(event)
                    self._persisted_event_ids.add(event.event_id)
            samples["v02_sqlite_persistence_ms"] = samples.get("v02_sqlite_persistence_ms", 0.0) + max(
                0.0, time.perf_counter() - stage
            ) * 1000

            # Priority 3: queue DINO only after the behavior observation is
            # available.  A full/slow queue merely leaves this track unknown.
            stage = time.perf_counter()
            if self.identity_resolver.should_submit(
                observation.track_id,
                observation.captured_at,
                identity,
            ):
                submitted = self.identity_resolver.submit(
                    track_id=observation.track_id,
                    crop_jpeg=observation.crop_jpeg,
                    captured_at=observation.captured_at,
                    source_id=observation.source_id,
                    generation=self._generation,
                )
                if submitted:
                    self.metrics.record_identity(
                        latency_ms=None,
                        backend=getattr(self.identity_classifier.embedder, "backend_name", None),
                    )
            samples["v02_identity_enqueue_ms"] = samples.get("v02_identity_enqueue_ms", 0.0) + max(
                0.0, time.perf_counter() - stage
            ) * 1000

            feedback_event = completed[-1] if completed else active_event
            stage = time.perf_counter()
            if feedback_event is not None and self.store is not None:
                self._save_local_crop(feedback_event, observation, observations, warnings)
            tracks.append(
                V02TrackResult(
                    observation=observation,
                    pet_id=identity.predicted_pet_id,
                    pet_name=identity.predicted_name,
                    identity_confidence=identity.similarity,
                    behavior_state=behavior.behavior_state.value,
                    behavior_confidence=behavior.smoothed_confidence or behavior.raw_confidence,
                    events=completed,
                    identity_status="resolved" if identity.predicted_pet_id else "unknown",
                    identity_pending=self.identity_resolver.is_pending(observation.track_id),
                    feedback_event=feedback_event,
                )
            )
            samples["v02_feedback_crop_and_result_ms"] = samples.get(
                "v02_feedback_crop_and_result_ms", 0.0
            ) + max(0.0, time.perf_counter() - stage) * 1000
        stage = time.perf_counter()
        self._drain_identity_results()
        if finalized:
            self._dispatch_track_summary(tracks, warnings)
        self._schedule_feedback_learning()
        samples["v02_finalize_and_queue_notifications_ms"] = max(0.0, time.perf_counter() - stage) * 1000
        return V02FrameResult(frame, tuple(tracks), tuple(finalized), (), tuple(warnings))

    def _save_local_crop(self, event, observation, observations, warnings) -> None:
        """本機證據不依賴收件者；保存不等於同意學習。"""
        if event.event_id in self._crop_evidence_saved:
            return
        try:
            if save_track_evidence(self.store, event, observation, self._learning_policy,
                                   observations=observations):
                if len(self._crop_evidence_saved) >= self._learning_policy.max_evidence:
                    self._crop_evidence_saved.clear()
                self._crop_evidence_saved.add(event.event_id)
        except (sqlite3.Error, OSError, RuntimeError, ValueError):
            warnings.append("逐貓裁切暫未保存；監控繼續，不會自動納入學習。")

    def flush(self) -> tuple[BehaviorEvent, ...]:
        # Give fast mock/CPU identity jobs a short opportunity to finish, but
        # never wait for a slow DINO job before closing an event timeline.
        for result in self.identity_resolver.wait_for_idle(0.05):
            self._apply_identity_resolution(result)
        events = self.behavior_engine.flush()
        if self.store is not None:
            for event in events:
                self.store.save_behavior_event(event)
                self._persisted_event_ids.add(event.event_id)
        return events

    def _schedule_feedback_learning(self) -> None:
        if self._feedback_learner is None or self.identity_resolver.pending_count:
            return
        now = time.monotonic()
        if (now - self._learning_checked_at < self._learning_policy.interval_seconds
                or self._learning_future is not None and not self._learning_future.done()):
            return
        self._learning_checked_at = now
        if self._learning_future is not None:
            try:
                self._learning_future.result()
            except Exception as error:  # noqa: BLE001 - learning cannot stop media/analysis
                if self.logger:
                    self.logger(f"⚠️ 回饋特徵擴充暫未完成：{type(error).__name__}；監控繼續。")
        self._learning_future = self.inference_scheduler.submit(
            self._feedback_learner.refresh, priority=InferencePriority.LOW,
            dedupe_key="feedback-identity-learning",
        )

    def close(self, *, wait: bool = False) -> None:
        try:
            self.flush()
        finally:
            self.identity_resolver.close(wait=wait)
            if self._owns_scheduler:
                self.inference_scheduler.shutdown(wait=wait)
            if self._owns_notification_dispatcher and self.notification_dispatcher is not None:
                self.notification_dispatcher.shutdown(wait=wait)

    def performance_report(self) -> dict[str, object]:
        report: dict[str, object] = {"pipeline": self.metrics.as_dict()}
        report["identity"] = self.identity_resolver.diagnostic_snapshot()
        report["inference_scheduler"] = self.inference_scheduler.metrics.as_dict()
        report["load_controller"] = self.load_controller.as_dict()
        monitor = self.inference_scheduler.resource_monitor
        if monitor is not None and hasattr(monitor, "snapshot"):
            if hasattr(monitor, "poll"):
                monitor.poll(force=True)
            snapshot = monitor.snapshot
            report["gpu_resource"] = {
                "state": snapshot.state.value,
                "name": snapshot.name,
                "temperature_c": snapshot.temperature_c,
                "vram_used_mib": snapshot.vram_used_mib,
                "vram_total_mib": snapshot.vram_total_mib,
                "queue_length": snapshot.queue_length,
                "cuda_error_count": snapshot.cuda_error_count,
                "oom_count": snapshot.oom_count,
            }
        return report

    def reset(self, reason: str = "manual") -> None:
        self._drain_identity_results()
        pending_events = self.behavior_engine.reset()
        if self.store is not None:
            for event in pending_events:
                self.store.save_behavior_event(event)
                self._persisted_event_ids.add(event.event_id)
        self.tracker.reset(reason)
        self.history.reset()
        self.temporal_identity.reset()
        self.feature_extractor.reset()
        self._seen_tracks.clear()
        self._identity_cache.clear()
        self._stable_identity.clear()
        self._behavior_cache.clear()
        self._crop_evidence_saved.clear()
        self._generation += 1
        self.identity_resolver.next_generation()
        self._source_id = None

    def _drain_identity_results(self) -> None:
        for result in self.identity_resolver.drain_completed():
            self._apply_identity_resolution(result)

    def _apply_identity_resolution(self, result: IdentityResolution) -> None:
        if result.generation != self._generation or result.source_id != self._source_id:
            return
        prediction = result.prediction
        self._identity_cache[result.track_id] = (result.captured_at, prediction)
        active = self.behavior_engine.active_event(result.track_id)
        if active is not None and result.captured_at < active.started_at:
            return
        scope = (active.event_id, result.track_id) if active is not None else result.track_id
        conflicts = [track for track, value in self._stable_identity.items()
                     if track != result.track_id and self.behavior_engine.active_event(track) is not None
                     and value.predicted_pet_id is not None
                     and value.predicted_pet_id == prediction.predicted_pet_id]
        stable = self.temporal_identity.update(scope, prediction, conflict=bool(conflicts),
                                              captured_at=result.captured_at, frame_id=result.frame_id)
        # A collision is evidence against both tracks, not only the later one.
        # Keep the original inference history, append an unknown decision and
        # clear old votes so a stale winner cannot immediately reappear.
        if conflicts:
            self.temporal_identity.reset(scope)
        for other_track in conflicts:
            other_event = self.behavior_engine.active_event(other_track)
            self.temporal_identity.reset((other_event.event_id, other_track))
            self._identity_cache.pop(other_track, None)
            ambiguous = replace(self._stable_identity[other_track], predicted_pet_id=None,
                                predicted_name=None, stable=False, unknown_reason="track_ambiguous",
                                reason="同時活動軌跡身分衝突；雙方撤回為尚未確認，等待新證據。")
            self._persist_identity_resolution(other_track, ambiguous, result.captured_at, other_event)
        self._persist_identity_resolution(result.track_id, stable, result.captured_at, active)

    def _persist_identity_resolution(self, track_id, stable, captured_at, active) -> None:
        self._stable_identity[track_id] = stable
        if active is not None and self.store is not None:
            try:
                self.store.record_identity_evidence(active.event_id, str(track_id), {
                    **asdict(stable), "captured_at": captured_at.isoformat(),
                    "decision": "recognized_candidate" if stable.stable else "unknown",
                    "experimental": True, "calibration_verified": False,
                })
            except (sqlite3.Error, ValueError, TypeError):
                if self.logger:
                    self.logger("⚠️ 身分證據暫未保存；影音與事件主路徑繼續。")
        self.metrics.record_identity(
            latency_ms=getattr(self.identity_classifier.embedder, "last_latency_ms", None),
            backend=getattr(self.identity_classifier.embedder, "backend_name", None),
            count=False,
        )
        event = self.behavior_engine.update_identity(
            track_id,
            pet_id=stable.predicted_pet_id,
            confidence=stable.similarity,
            model_version=stable.model_version,
            resolved_at=captured_at,
        )
        if self.store is not None:
            self.store.update_behavior_event_identity(
                track_id=track_id,
                pet_id=stable.predicted_pet_id,
                confidence=stable.similarity,
                model_version=stable.model_version,
                resolved_at=captured_at,
            )
        if event is not None and self.store is not None:
            # Save after the SQL patch so the correction row retains the
            # original anonymous value instead of seeing the already-mutated
            # in-memory object as the prediction.
            self.store.save_behavior_event(event)
            self._persisted_event_ids.add(event.event_id)
        if self.store is not None:
            self.store.update_track_identity(
                track_id,
                pet_id=stable.predicted_pet_id,
                confidence=stable.similarity,
            )
            self.store.resolve_media_identity(
                track_id=track_id, pet_id=stable.predicted_pet_id,
                confidence=stable.similarity, resolved_at=captured_at,
            )

    def _dispatch_track_summary(
        self,
        tracks: list[V02TrackResult],
        warnings: list[str],
    ) -> None:
        if self.notifier is None or not self.chat_ids or self.notification_dispatcher is None:
            return
        snapshots = [track for track in tracks if track.feedback_event is not None]
        if not snapshots:
            return
        ordered = sorted(snapshots, key=lambda track: str(track.observation.track_id))[:20]
        lines = ["喵喵守護者+｜逐貓行為摘要（實驗性候選）"]
        rows: list[list[dict[str, str]]] = []
        feedback_events: list[BehaviorEvent] = []
        for index, track in enumerate(ordered, start=1):
            event = track.feedback_event
            if event is None:
                continue
            behavior = getattr(event.behavior_state, "value", str(event.behavior_state))
            motion = getattr(event.motion_state, "value", str(event.motion_state))
            identity_name = (
                track.pet_name
                if event.pet_id is not None and event.pet_id == track.pet_id and track.pet_name
                else "尚未確認身分"
            )
            duration = event.duration_seconds
            duration_text = f"｜約 {int(duration)} 秒" if duration is not None else ""
            score = event.smoothed_confidence
            score_text = f"｜模型分數 {float(score):.2f}（未校正）" if score is not None else ""
            lines.append(
                f"{index}. 🐈 {identity_name}｜行為：{self._BEHAVIOR_LABELS.get(behavior, '無法辨識')}"
                f"｜動作：{self._MOTION_LABELS.get(motion, '無法辨識')}{duration_text}{score_text}"
            )
            lines.append(f"   時間：{event.started_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
            feedback_events.append(event)
            rows.append([{
                "text": f"📋 回報軌跡 {index}",
                "callback_data": f"trk:{event.event_id}:open",
            }])
        if len(snapshots) > len(ordered):
            lines.append(f"另有 {len(snapshots) - len(ordered)} 條軌跡未列出。")
        lines.append("模型分數不是準確率；回報只記錄人工標籤，不會自動重訓。")
        text = "\n".join(lines)[:4000]
        for chat_id in self.chat_ids:
            preferences = self.feedback_preferences
            try:
                if preferences is not None and not preferences.should_send(chat_id, "cat_detected"):
                    continue
                feedback_enabled = (
                    preferences is not None
                    and chat_id.isdecimal()
                    and int(chat_id) > 0
                    and preferences.feedback_enabled(chat_id)
                )
            except (OSError, RuntimeError, ValueError, TypeError):
                feedback_enabled = False
            try:
                def send_summary(chat_id=chat_id, text=text, feedback_enabled=feedback_enabled,
                                 preferences=preferences):
                    # 圖片組合與網路傳送都在既有有界 worker，不能阻塞分析。
                    if preferences is not None and not preferences.claim_delivery(chat_id, "cat_detected"):
                        return None
                    personal_cooldown = preferences is not None and preferences.status(chat_id)["cooldown_minutes"] > 0
                    if feedback_enabled:
                        try:
                            sheet = numbered_track_sheet([item.observation for item in ordered])
                            if sheet is not None:
                                captured = ordered[0].observation.captured_at.astimezone().strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                                caption = (f"逐軌跡影像對照｜截圖時間：{captured}\n"
                                           "編號對應本則按鈕；圖片是當次觀測，不代表整段行為。\n" + text)
                                if len(caption.encode("utf-16-le")) // 2 <= 1024:
                                    return self.notifier.send_photo(
                                        chat_id, sheet, caption=caption,
                                        reply_markup={"inline_keyboard": rows} if rows else None,
                                    )
                                if not personal_cooldown:
                                    self.notifier.send_photo(
                                        chat_id, sheet,
                                        caption=f"逐軌跡影像對照｜截圖時間：{captured}\n"
                                        "摘要較長，編號對應下一則摘要按鈕；這是當次觀測，不代表整段行為。"
                                        "空白格代表沒有可用影像，請勿猜測。",
                                    )
                        except (ImportError, OSError, RuntimeError, ValueError):
                            text += "\n本次軌跡影像無法送出；無法確認時請略過標註。"
                    return self.notifier.send_message(
                        chat_id, text,
                        reply_markup={"inline_keyboard": rows} if feedback_enabled and rows else None,
                    )

                future = self.notification_dispatcher.submit(
                    send_summary
                )
                if feedback_enabled and rows and self.store is not None:
                    recipient_ref = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()

                    def save_receipts(done, *, selected_events=tuple(feedback_events), recipient_ref=recipient_ref):
                        try:
                            result = done.result()
                            if result is None or result.message_id is None:
                                return
                            for selected_event in selected_events:
                                self.store.record_behavior_event_telegram_receipt(
                                    selected_event.event_id, recipient_ref, result.message_id,
                                )
                        except Exception as exc:  # noqa: BLE001 - never block inference on receipt persistence
                            if self.logger is not None:
                                self.logger(f"⚠️ 行為通知已送出，但逐軌跡回執未保存：{type(exc).__name__}。")

                    future.add_done_callback(save_receipts)
                if future.done() and future.exception() is not None:
                    self.metrics.notifications_dropped += 1
                else:
                    self.metrics.notifications_queued += 1
            except (OSError, RuntimeError, ValueError) as exc:
                warnings.append(f"Telegram 逐貓摘要排程失敗：{type(exc).__name__}")

    @staticmethod
    def _unknown_prediction(track_id: str | int) -> IdentityPrediction:
        return IdentityPrediction(
            None,
            None,
            0.0,
            0.0,
            0.0,
            1.0,
            "identity-pending",
            track_id,
            False,
            "身分尚未完成；先記錄行為。",
        )
