"""Single-pass V0.2 adapter for the existing Host ingest and clip queue."""
import hashlib
import sqlite3
import time
import uuid
from dataclasses import replace
from pathlib import Path

from meowkeeper.core.behavior import (
    BehaviorConfig,
    BehaviorEngine,
    BehaviorFeatureExtractor,
    ZoneRegistry,
)
from meowkeeper.core.detection.frame_analysis import FrameAnalysis
from meowkeeper.core.events.v02_pipeline import V02AnalysisPipeline
from meowkeeper.core.identity import (
    IdentityPrediction,
    PetRegistry,
)
from meowkeeper.core.identity.identity_classifier import LazyIdentityClassifier
from meowkeeper.core.inference.timings import StageTimings
from meowkeeper.core.semantic_storyboard import FrameEvidence, FrameEvidenceBuffer
from meowkeeper.core.tracking import BoTSORTTracker, TrackHistory


class UnknownIdentityClassifier:
    class Backend:
        model_version = "identity-disabled"
        backend_name = "disabled"

    embedder = Backend()

    def classify(self, _crop, *, track_id=None):
        return IdentityPrediction(None, None, 0.0, 0.0, 0.0, 1.0,
                                  self.embedder.model_version, track_id, False, "尚未有可用身分資料。")


class SessionTracker:
    """Scope track IDs to a run; retain cat results if later persistence fails."""
    def __init__(self, tracker):
        self.tracker = tracker
        self.prefix = uuid.uuid4().hex
        self.latest = []
        self.inference_backend = tracker.inference_backend

    def update(self, frame):
        previous_device = self.tracker.device
        observations = self.tracker.update(frame)
        if self.tracker.device != previous_device:
            # GPU fallback recreates short-term trackers; do not reuse old IDs.
            self.prefix = uuid.uuid4().hex
        self.inference_backend = self.tracker.inference_backend
        frame_src = getattr(frame, "source_url", None)
        self.latest = [replace(item, track_id=f"{self.prefix}:{item.track_id}",
                               source_id=frame_src or item.source_id)
                       for item in observations]
        return self.latest

    def reset(self, reason="manual"):
        self.tracker.reset(reason)
        self.prefix = uuid.uuid4().hex
        self.latest = []


class V02HostAnalyzer:
    def __init__(
        self, tracker, identity, *, store, zones=None, behavior_config=None, logger=None,
        notifier=None, chat_ids=(), notification_dispatcher=None, feedback_preferences=None,
        identity_thresholds=None, native_snapshot_provider=None,
    ):
        self.tracker = SessionTracker(tracker)
        self.inference_backend = tracker.inference_backend
        self.device = tracker.device
        self.logger = logger
        self.store = store
        self.semantic_buffer = None
        self._load_saved_zones = zones is None and store is not None
        self._zone_source = None
        self._scene_epoch = None
        self._scene_checked = 0.0
        self._scene_failure_reported = False
        self._semantic_privacy_blocked = False
        self._latest_semantic_epoch = None
        self._scene_view_id = None
        self._include_scene_references = False
        self._scene_reference_cache_key = None
        self._scene_reference_cache = None
        self._warning_counts = {}
        self._warning_last_log = {}
        self.timings = StageTimings()
        history = TrackHistory()
        self.pipeline = V02AnalysisPipeline(
            self.tracker, identity, history=history, store=store,
            feature_extractor=BehaviorFeatureExtractor(history, zones or ZoneRegistry()),
            behavior_engine=BehaviorEngine(behavior_config or BehaviorConfig()),
            notifier=notifier, chat_ids=tuple(chat_ids),
            notification_dispatcher=notification_dispatcher,
            feedback_preferences=feedback_preferences,
            identity_low_confidence=identity_thresholds.accepted_threshold if identity_thresholds else 0.62,
            logger=logger,
            native_snapshot_provider=native_snapshot_provider,
        )

    @classmethod
    def create(
        cls, *, model_path, confidence, store, data_root, logger,
        notifier=None, chat_ids=(), notification_dispatcher=None, feedback_preferences=None,
        tracker=None, identity_thresholds=None, native_snapshot_provider=None,
    ):
        registry = PetRegistry(Path(data_root) / "pets")
        identity = LazyIdentityClassifier(registry, thresholds=identity_thresholds)
        tracker = tracker or BoTSORTTracker(model_path=model_path, confidence_threshold=confidence)
        try:
            import threading
            threading.Thread(target=lambda: getattr(identity, "_ready", lambda: None)(),
                             name="meowkeeper-dinov2-prewarm", daemon=True).start()
        except Exception:
            pass
        return cls(
            tracker, identity, store=store, logger=logger, notifier=notifier,
            chat_ids=chat_ids, notification_dispatcher=notification_dispatcher,
            feedback_preferences=feedback_preferences,
            identity_thresholds=identity_thresholds,
            native_snapshot_provider=native_snapshot_provider,
        )

    def set_clip_service(self, clip_service: object) -> None:
        if hasattr(self, "pipeline") and self.pipeline is not None:
            self.pipeline.clip_service = clip_service

    def analyze(self, frame):
        samples: dict[str, float] = {}
        started = time.perf_counter()
        try:
            return self._analyze_frame(frame, samples)
        finally:
            samples["v02_host_adapter_total_ms"] = max(0.0, time.perf_counter() - started) * 1000
            self.timings.observe(samples)

    def _analyze_frame(self, frame, samples: dict[str, float]):
        # Persist only an opaque source reference, never RTSP credentials.
        stage = time.perf_counter()
        safe_frame = replace(frame, source_url="source-" + hashlib.sha256(frame.source_url.encode()).hexdigest()[:24])
        samples["v02_source_sanitize_ms"] = max(0.0, time.perf_counter() - stage) * 1000
        if self._load_saved_zones and self._zone_source != safe_frame.source_url:
            # Never reuse the previous camera's polygons, even when DB reads fail.
            self._zone_source = safe_frame.source_url
            self._scene_reference_cache_key = None
            self._scene_reference_cache = None
            self.pipeline.feature_extractor.zones = ZoneRegistry()
            self._scene_epoch = None
        stage = time.perf_counter()
        if self._load_saved_zones:
            from meowkeeper.core.behavior.scene_views import SceneViewRegistry
            try:
                scenes = SceneViewRegistry(self.store)
                state = scenes.state(safe_frame.source_url)
                active_view = state["active_view"] if state["state"] == "aligned" else None
                if active_view != self._scene_view_id:
                    self._scene_view_id = active_view
                    self._scene_checked = 0
                if self._scene_epoch != state["epoch"]:
                    if self._scene_epoch is not None:
                        self.tracker.prefix = uuid.uuid4().hex
                    self._scene_epoch = state["epoch"]
                    self._scene_checked = 0
                    self.pipeline.feature_extractor.zones = ZoneRegistry()
                if time.monotonic()-self._scene_checked >= 2:
                    self._scene_checked = time.monotonic()
                    self.pipeline.feature_extractor.zones = ZoneRegistry(scenes.aligned_zones(safe_frame.source_url, safe_frame.jpeg_bytes))
                self._semantic_privacy_blocked = state["state"] != "aligned" and scenes.has_excluded_regions(safe_frame.source_url)
                self._scene_failure_reported = False
            except (sqlite3.Error, ValueError, TypeError, OSError, ImportError):
                self.pipeline.feature_extractor.zones = ZoneRegistry()
                if not self._scene_failure_reported and self.logger:
                    self.logger("⚠️ 此來源區域載入失敗，暫停區域行為判斷；貓咪偵測繼續。")
                self._scene_failure_reported = True
                self._semantic_privacy_blocked = True
        samples["v02_scene_registry_ms"] = max(0.0, time.perf_counter() - stage) * 1000
        self.tracker.latest = []
        result = None
        stage = time.perf_counter()
        try:
            result = self.pipeline.process(safe_frame)
            for warning in result.warnings:
                self._report_warning(warning)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            if self.logger:
                self.logger(f"⚠️ 行為或身分處理失敗：{type(error).__name__}；保留本次貓咪偵測。")
        finally:
            self.inference_backend = self.tracker.tracker.inference_backend
            self.device = self.tracker.tracker.device
            samples["v02_pipeline_process_ms"] = max(0.0, time.perf_counter() - stage) * 1000
        observations = self.tracker.latest
        if result is not None and any("略過一張普通 AI 影格" in warning for warning in result.warnings):
            return FrameAnalysis(False, None, None, "本張依活動採樣規則略過；不代表沒有貓咪。",
                                 {"analysis_skipped": True})
        if self.semantic_buffer is not None:
            self._latest_semantic_epoch = self.tracker.prefix+":"+safe_frame.source_url
            self.semantic_buffer.observe(tuple(
                FrameEvidence(str(item.track_id), self._latest_semantic_epoch,
                              item.captured_at, item.crop_jpeg,self._zone_evidence(item))
                for item in observations if item.crop_jpeg and self._crop_allowed(item)
            ))
        stage = time.perf_counter()
        analysis = FrameAnalysis(
            bool(observations), "cat_detected" if observations else None,
            max((o.confidence for o in observations), default=None),
            "偵測到貓咪；身分及行為為實驗性候選。" if observations else "本影格未建立貓咪事件。",
            {"experimental": True, "cat_count": len(observations),
             "track_id": observations[0].track_id if len(observations) == 1 else None,
             "track_ids": tuple(str(o.track_id) for o in observations[:20]),
             "pet_id": result.tracks[0].pet_id if result is not None and len(result.tracks) == 1 else None,
             "detections": [
                 {"bbox": list(item.observation.bbox.normalized(item.observation.frame_width, item.observation.frame_height)),
                  "confidence": item.observation.confidence,
                  "track_id": str(item.observation.track_id),
                  "identity": item.pet_name,
                  "identity_confidence": item.identity_confidence,
                  "behavior": self.pipeline._BEHAVIOR_LABELS.get(item.behavior_state, "尚未判斷"),
                  "experimental": True}
                 for item in (result.tracks if result is not None else ())[:20]]},
        )
        samples["v02_overlay_and_result_build_ms"] = max(0.0, time.perf_counter() - stage) * 1000
        return analysis

    def _report_warning(self, warning):
        # Only known, locally generated categories leave the pipeline. Never
        # include arbitrary provider messages, source URLs or exception values.
        if "略過一張普通 AI 影格" in warning:
            category, label = "sampling", "ℹ️ AI 採樣節流（正常保護，非分析故障）"
        elif "來源變更" in warning:
            category, label = "source_reset", "ℹ️ 來源已變更，短期追蹤與身分投票已重置"
        elif "主要影像推論失敗" in warning:
            category, label = "inference", "⚠️ 主要影像推論失敗；請檢查 AI 子程序狀態"
        elif "裁切暫未保存" in warning:
            category, label = "crop_storage", "⚠️ 逐貓裁切保存失敗；請檢查資料目錄權限與容量"
        elif "摘要排程失敗" in warning:
            category, label = "notification", "⚠️ Telegram 逐貓摘要排程失敗；請檢查通知佇列"
        else:
            category, label = "other", "⚠️ 行為分析出現未分類狀況；請檢查本機診斷"
        self._warning_counts[category] = self._warning_counts.get(category, 0) + 1
        now = time.monotonic()
        if self.logger and now - self._warning_last_log.get(category, -60) >= 60:
            count = self._warning_counts.pop(category)
            self._warning_last_log[category] = now
            self.logger(f"{label}；本時段 {count} 次（同類每 60 秒彙整），影音仍持續。")

    def enable_semantic_frames(self, storyboard_options=None):
        if self.semantic_buffer is None:
            options = dict(storyboard_options or {})
            self._include_scene_references = bool(options.pop("include_scene_references", False))
            self.semantic_buffer = FrameEvidenceBuffer(options)

    def prepare_semantic(self, request):
        boards = ()
        if self.semantic_buffer is not None and request.trigger_at is not None:
            boards = self.semantic_buffer.collect(request.track_ids, request.trigger_at)
        scene_reference_jpeg = None
        scene_reference_labels = ()
        if boards and self._include_scene_references and self._zone_source:
            from meowkeeper.core.behavior.scene_views import SceneViewRegistry
            try:
                scenes = SceneViewRegistry(self.store)
                revision = scenes.reference_revision(self._zone_source)
                if revision != self._scene_reference_cache_key:
                    self._scene_reference_cache_key = revision
                    self._scene_reference_cache = scenes.reference_context(self._zone_source)
                if self._scene_reference_cache is not None:
                    scene_reference_jpeg, scene_reference_labels = self._scene_reference_cache
            except (sqlite3.Error, ValueError, TypeError, OSError, ImportError):
                # Optional context failure must not suppress the event storyboard.
                scene_reference_jpeg = None
                scene_reference_labels = ()
        return replace(request, storyboards=boards,
                       track_epoch=boards[0].epoch if boards else "unverified",
                       scene_reference_jpeg=scene_reference_jpeg,
                       scene_reference_labels=scene_reference_labels)

    def _crop_allowed(self, observation):
        if self._semantic_privacy_blocked:
            return False
        x1,y1,x2,y2 = observation.bbox.normalized(observation.frame_width,observation.frame_height)
        for zone in self.pipeline.feature_extractor.zones.list():
            if zone.zone_type != "excluded" or not zone.enabled:
                continue
            xs,ys = [p[0] for p in zone.polygon],[p[1] for p in zone.polygon]
            # bbox保守加寬，避免裁切padding跨進排除區；寧可略過，不猜測安全。
            if x2+.1 > min(xs) and x1-.1 < max(xs) and y2+.1 > min(ys) and y1-.1 < max(ys):
                return False
        return True

    def _zone_evidence(self, observation):
        if not self._scene_view_id:
            return ()
        center = observation.bbox.center
        point = (center[0]/observation.frame_width,center[1]/observation.frame_height)
        return tuple((self._scene_view_id,zone.zone_id,zone.zone_type) for zone in self.pipeline.feature_extractor.zones.classify_center(point) if zone.zone_type != "excluded")

    def validate_semantic(self, request):
        if not request.storyboards:
            return False
        if request.input_revision > 1:
            return True  # 明確手動重分析使用已保存的歷史裁切，不拿現在視角冒充。
        if self._semantic_privacy_blocked:
            return False
        if self._load_saved_zones and self._zone_source:
            from meowkeeper.core.behavior.scene_views import SceneViewRegistry
            try:
                state = SceneViewRegistry(self.store).state(self._zone_source)
                if state["epoch"] != self._scene_epoch:
                    return False
                if any(board.zones for board in request.storyboards) and (state["state"] != "aligned" or any(view != state["active_view"] for board in request.storyboards for _index,view,_zone,_kind in board.zones)):
                    return False
            except (sqlite3.Error,ValueError,TypeError):
                return False
        return all(board.epoch == self._latest_semantic_epoch for board in request.storyboards)

    def close(self):
        if self.semantic_buffer is not None:
            self.semantic_buffer.close()
        try:
            self.pipeline.close(wait=False)
        except sqlite3.Error:
            if self.logger:
                self.logger("⚠️ 行為資料結束寫入未完成；背景推論已釋放。")
        close_tracker = getattr(self.tracker.tracker, "close", None)
        if callable(close_tracker):
            close_tracker()
