"""可由 Host 與 Web 共用的 Phase 2 監控核心控制器。

這個模組只負責生命週期與元件接線；FFmpeg、YOLO、事件組片、SQLite 及
Telegram 的實作仍留在既有 core 模組。Web route 不應直接建立這些元件。
"""

from __future__ import annotations

import hashlib
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from meowkeeper.core.analytics.daily_monitoring import DailyMonitoringRecorder
from meowkeeper.core.detection.frame_analysis import (
    AnalysisConfigurationError,
    NoopFrameAnalyzer,
)
from meowkeeper.core.event_semantics import EventSemanticService, VisionProvider
from meowkeeper.core.events.analysis_pipeline import AnalysisPipeline
from meowkeeper.core.inference.adaptive_analyzer import AdaptiveAnalyzer
from meowkeeper.core.inference.live_overlay import update_live_overlay
from meowkeeper.core.inference.process_worker import IsolatedInferenceWorker, IsolatedTracker
from meowkeeper.core.media import (
    ClipAssembler,
    ClipAssemblyResult,
    DiskSegmentRingBuffer,
    EventClipRequest,
    EventClipService,
    FfmpegMediaIngest,
    LiveClipResult,
    resolve_source,
)
from meowkeeper.core.media.latest_frame import LatestFrameReader
from meowkeeper.core.media.sources import (
    FramePacket,
    MediaSourceError,
    YouTubeStreamResolver,
    is_youtube_url,
)
from meowkeeper.core.media.youtube import YouTubeSourceAdapter, YouTubeSourceConfig
from meowkeeper.core.notifications.cat_preferences import CatNotificationPreferences
from meowkeeper.core.notifications.dispatcher import NotificationDispatcher
from meowkeeper.storage.database import SQLiteStore


class CoreController:
    """Host 對 Web 暴露的最小可執行控制契約。"""

    def start_monitor(self) -> dict[str, Any]:  # pragma: no cover - interface contract
        raise NotImplementedError

    def stop_monitor(self, *, confirm: bool = False) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def restart_monitor(self, *, confirm: bool = False) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def reconnect_source(self) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def create_snapshot(self) -> Path | None:  # pragma: no cover
        raise NotImplementedError

    def get_recent_clip(self, pre_seconds: float, post_seconds: float = 0.0) -> LiveClipResult:  # pragma: no cover
        raise NotImplementedError

    def transcode_clip_for_telegram(
        self, source_path: str | Path, destination_path: str | Path,
    ) -> ClipAssemblyResult:  # pragma: no cover - interface contract
        raise NotImplementedError

    def simulate_event(self, event_type: str = "cat_detected") -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def get_metrics(self) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


class RepositoryCoreController(CoreController):
    """將既有 Phase 2 元件接成一個可啟停的單一監控 session。

    沒有來源時不會建立 FFmpeg 或 YOLO；Dashboard 仍可啟動並顯示等待設定。
    提供來源後，唯一的 ``FfmpegMediaIngest`` 同時輸出分析 JPEG 與壓縮 ring
    segments。停止時先中止 ingest，再由 worker 有界地清理事件服務。
    """

    def __init__(
        self,
        *,
        store: SQLiteStore,
        source_url: str | None = None,
        model_path: str | None = None,
        ffmpeg_path: str | None = None,
        ring_directory: str | Path = "data/cache/ring",
        event_directory: str | Path = "data/events",
        preview_directory: str | Path = "data/cache/preview",
        retention_seconds: float = 60.0,
        segment_seconds: float = 1.0,
        maximum_size_mb: int = 512,
        analysis_fps: int = 5,
        activity_fps: int = 5,
        analysis_width: int = 640,
        inference_device: str = "auto",
        identity_frame_width: int = 1280,
        preview_frame_width: int = 1920,
        confidence_threshold: float = 0.45,
        identity_accepted_threshold: float = 0.62,
        identity_minimum_margin: float = 0.05,
        event_pre_seconds: float = 5.0,
        event_post_seconds: float = 10.0,
        cooldown_seconds: float = 180.0,
        analyzer_mode: str = "yolo",
        telegram_enabled: bool = False,
        telegram_notifier: Any | None = None,
        telegram_chat_ids: tuple[str, ...] = (),
        telegram_dispatcher: NotificationDispatcher | None = None,
        cat_notification_preferences: CatNotificationPreferences | None = None,
        tailscale_status_callback: Callable[[], dict[str, Any]] | None = None,
        native_snapshot_provider: Callable[[], bytes | None] | None = None,
        vision_provider: VisionProvider | None = None,
        semantic_wait_seconds: float = 5.0,
        logger: Callable[[str], None] | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.store = store
        self.source_url = source_url.strip() if source_url and source_url.strip() else None
        self.model_path = model_path
        self._model_version = Path(model_path).name if model_path else "yolo11n.pt"
        self._model_sha256: str | None = None
        self.ffmpeg_path = ffmpeg_path
        self.ring_directory = Path(ring_directory).resolve()
        self.event_directory = Path(event_directory).resolve()
        self._capture_lock = threading.Lock()
        self.preview_directory = Path(preview_directory).resolve()
        self.retention_seconds = float(retention_seconds)
        self.segment_seconds = float(segment_seconds)
        self.maximum_size_mb = int(maximum_size_mb)
        self.analysis_fps = int(analysis_fps)
        self.activity_fps = int(activity_fps)
        self.analysis_width = int(analysis_width)
        if inference_device not in {"auto", "cpu", "intel:gpu", "onnx:cuda"}:
            raise ValueError("運算裝置選項不正確。")
        self.inference_device = inference_device
        if self.analysis_width not in {640, 960, 1280, 1920}:
            raise ValueError("YOLO 推論尺寸只支援 640／960／1280／1920。")
        if type(identity_frame_width) is not int or not 640 <= identity_frame_width <= 1280:
            raise ValueError("身分辨識共享影格寬度必須介於 640 到 1280。")
        self.identity_frame_width = identity_frame_width
        if type(preview_frame_width) is not int or not 640 <= preview_frame_width <= 1920:
            raise ValueError("預覽共享影格寬度必須介於 640 到 1920。")
        self.preview_frame_width = preview_frame_width
        self.confidence_threshold = float(confidence_threshold)
        self.identity_accepted_threshold = float(identity_accepted_threshold)
        self.identity_minimum_margin = float(identity_minimum_margin)
        self.event_pre_seconds = float(event_pre_seconds)
        self.event_post_seconds = float(event_post_seconds)
        self.cooldown_seconds = float(cooldown_seconds)
        self.analyzer_mode = analyzer_mode
        self.telegram_enabled = bool(telegram_enabled)
        self.telegram_notifier = telegram_notifier
        self.telegram_chat_ids = tuple(telegram_chat_ids)
        self.telegram_dispatcher = telegram_dispatcher
        self.cat_notification_preferences = cat_notification_preferences
        self._tailscale_status_callback = tailscale_status_callback
        self._native_snapshot_provider = native_snapshot_provider
        if not 0 <= semantic_wait_seconds <= 10:
            raise ValueError("影片通知 AI 等待時間必須介於 0 到 10 秒。")
        self.vision_provider = vision_provider
        self.semantic_wait_seconds = float(semantic_wait_seconds)
        self.logger = logger or (lambda _message: None)
        self.event_sink = event_sink
        self._settings_callback: Callable[[dict[str, Any]], Any] | None = None

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._ingest: FfmpegMediaIngest | None = None
        self._adaptive_analyzer: AdaptiveAnalyzer | None = None
        self._semantic_service: EventSemanticService | None = None
        self._ring: DiskSegmentRingBuffer | None = None
        self._clip_service: EventClipService | None = None
        self._daily_monitoring: DailyMonitoringRecorder | None = None
        self._state = "stopped"
        self._started_at: str | None = None
        self._last_error: str | None = None
        self._latest_jpeg: Path | None = None
        self._preview_epoch = uuid.uuid4().hex
        self._event_count = 0
        self._last_frame_at: float | None = None
        self._frames_since_tick = 0
        self._last_tick = time.monotonic()
        self._metrics: dict[str, Any] = {
            "source": {
                "status": "not_configured" if self.source_url is None else "stopped",
                "source_id": None,
                "stream_name": None,
                "resolution": None,
                "fps": None,
                "connections": 0,
                "last_frame_at": None,
            },
            "ffmpeg": {"status": "not_started", "restart_count": 0, "last_error": None},
            "ai": {
                "backend": "not_started",
                "device": "not_started",
                "fps": 0.0,
                "latency_ms": None,
                "model": Path(model_path).name if model_path else "yolo11n.pt",
            },
            "ring_buffer": {
                "seconds": 0.0,
                "bytes": 0,
                "capacity_seconds": self.retention_seconds,
                "segment_count": 0,
            },
            "telegram": {
                "status": "disabled" if not self.telegram_enabled else "configured",
                "enabled": self.telegram_enabled,
                "success_count": 0,
                "failure_count": 0,
            },
            "sqlite": {"status": "ok", "schema_version": store.schema_version, "journal_mode": store.journal_mode},
            "snapshot": {"status": "not_checked", "source": None, "width": None, "height": None},
        }

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def latest_jpeg_path(self) -> Path | None:
        with self._lock:
            path = self._latest_jpeg
        return path if path is not None and path.is_file() else None

    @property
    def is_running(self) -> bool:
        return self.state in {"starting", "running"}

    def callbacks(self) -> dict[str, Callable[..., Any]]:
        """供 ``CoreStateFacade`` 使用的明確 callback 邊界。"""

        callbacks = {
            "monitoring_active": lambda: self.is_running,
            "retry_inference": self.retry_inference,
            "start": self.start_monitor,
            "stop": self.stop_monitor,
            "restart": self.restart_monitor,
            "status": self.get_status,
            "recent_clip": self.get_recent_clip,
            "save_capture": self.save_capture,
            "snapshot": self.create_snapshot,
            "preview_snapshot": self.create_preview_snapshot,
            "simulate_event": self.simulate_event,
            "source_config": self.configure_source,
            "settings": self._settings_callback or self.apply_settings,
            "behavior_zones": self.behavior_zones,
            "reanalyze_event": self.reanalyze_event,
            "cancel_semantic_job": self.cancel_semantic_job,
            "semantic_runtime": self.semantic_runtime,
            "telegram_test": self.test_telegram,
            "shutdown": self.close,
        }
        if self._tailscale_status_callback is not None:
            callbacks["tailscale_status"] = self._tailscale_status_callback
        return callbacks

    def retry_inference(self) -> dict[str, Any]:
        with self._lock:
            analyzer = self._adaptive_analyzer
            accepted = analyzer is not None and analyzer.request_retry()
        return {"success": accepted, "message_zh_tw": "已要求低頻重新嘗試 AI；錄影不重啟。" if accepted
                else "目前尚未有分析器，或溫度／記憶體仍不適合重試。"}

    def behavior_zones(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from meowkeeper.core.behavior import ZoneDefinition

        with self._lock:
            if not self.source_url:
                raise ValueError("請先設定影音來源，再設定行為區域。")
            reference = "source-" + hashlib.sha256(self.source_url.encode()).hexdigest()[:24]
            if payload is not None:
                if payload.get("source_ref") != reference:
                    raise ValueError("影音來源已變更，請重新載入區域後再保存。")
                values = payload.get("zones")
                if not isinstance(values, list) or len(values) > 64:
                    raise ValueError("區域清單格式錯誤或超過 64 個。")
                zones = []
                for item in values:
                    if not isinstance(item, dict) or set(item) - {"zone_id", "name", "zone_type", "polygon", "enabled"}:
                        raise ValueError("區域欄位格式錯誤。")
                    if any(not isinstance(item.get(key), str) or not 1 <= len(item[key]) <= 100
                           for key in ("zone_id", "name", "zone_type")):
                        raise ValueError("區域 ID、名稱及類型需為 1 至 100 字元。")
                    polygon = item.get("polygon")
                    if (not isinstance(polygon, list) or not 3 <= len(polygon) <= 64
                            or any(not isinstance(point, list) or len(point) != 2
                                   or any(type(value) not in (int, float) for value in point) for point in polygon)):
                        raise ValueError("每個區域需 3 至 64 個座標點，每點包含兩個數字。")
                    if type(item.get("enabled", True)) is not bool:
                        raise ValueError("區域啟用值必須是布林值。")
                    zone = ZoneDefinition(item["zone_id"], item["name"], item["zone_type"],
                                          tuple(tuple(point) for point in polygon), item.get("enabled", True))
                    zone.validate()
                    zones.append(zone)
                self.store.replace_source_zones(reference, zones)
            from meowkeeper.core.behavior.scene_views import SceneViewRegistry
            return {"source_ref": reference,
                    "view_alignment": SceneViewRegistry(self.store).state(reference),
                    "zones": [asdict(zone) for zone in self.store.load_source_zones(reference)],
                    "analyzer_enabled": self.analyzer_mode == "v02",
                    "restart_required": payload is not None,
                    "message_zh_tw": "區域已保存，重新啟動監控後套用；只有 V0.2 實驗性分析會使用。" if payload is not None
                    else "區域僅適用目前來源；鏡頭移動或改接裝置後，請重新設定。"}

    def invalidate_scene_view(self, reason="movement"):
        from meowkeeper.core.behavior.scene_views import SceneViewRegistry
        with self._lock:
            source = self.source_url
        if source and self.store is not None:
            reference = "source-" + hashlib.sha256(source.encode()).hexdigest()[:24]
            SceneViewRegistry(self.store).invalidate(reference, reason)

    def reanalyze_event(self, event_id, request_id):
        with self._lock:
            service = self._semantic_service
        if service is None:
            raise ValueError("監控分析器尚未啟動；請先啟用可選分析與外送同意。")
        return service.reanalyze(event_id,request_id)

    def cancel_semantic_job(self, event_id, job_id):
        with self._lock:
            service = self._semantic_service
        if service is None:
            from meowkeeper.storage.semantic_jobs import SemanticJobJournal
            active = SemanticJobJournal(self.store).request_cancel(event_id,job_id)
            return {"status":"cancel_requested" if active else "already_finished"}
        return service.cancel(event_id,job_id)

    def semantic_runtime(self):
        from meowkeeper.storage.semantic_jobs import SemanticJobJournal
        result = SemanticJobJournal(self.store).runtime()
        with self._lock:
            service = self._semantic_service
        return {**result,"worker_active":service is not None,"pressure_reason":self.semantic_pressure(),
                "last_job_pressure_reason":service.pressure_reason if service else None}

    def semantic_pressure(self):
        with self._lock:
            clip,analyzer=self._clip_service,self._adaptive_analyzer
        if clip is not None and clip.media_busy:
            return "media_preparing"
        return analyzer.cloud_pressure_reason() if analyzer is not None else None

    def set_settings_callback(self, callback: Callable[[dict[str, Any]], Any] | None) -> None:
        """Install the persistence boundary owned by the Host launcher."""

        self._settings_callback = callback

    def set_native_snapshot_provider(self, provider: Callable[[], bytes | None] | None) -> None:
        """Attach an optional native-resolution snapshot source.

        The provider must read the already active local producer.  It is not
        allowed to create another RTSP/FFmpeg input connection.
        """

        with self._lock:
            self._native_snapshot_provider = provider

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            metrics = {
                key: dict(value) if isinstance(value, dict) else value
                for key, value in self._metrics.items()
            }
            metrics["telegram"]["recipient_count"] = len(self.telegram_chat_ids)
            metrics["telegram"]["queue_pending"] = self.telegram_dispatcher.pending_count if self.telegram_dispatcher else 0
            return {
                "status": self._state,
                "started_at": self._started_at,
                "capability": "connected",
                "last_error": self._last_error,
                "metrics": metrics,
                "latest_jpeg": str(self._latest_jpeg) if self._latest_jpeg else None,
                "event_count": self._event_count,
            }

    def get_metrics(self) -> dict[str, Any]:
        return self.get_status()

    def start_monitor(self) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"action": "start", "status": self._state, "message_zh_tw": "監控已在執行中，沒有建立第二個來源連線。"}
            # A previous session's JPEG must never appear as a live frame.
            self._latest_jpeg = None
            self._preview_epoch = uuid.uuid4().hex
            self._metrics["ai"]["overlay"] = None
            self._frames_since_tick = 0
            self._last_tick = time.monotonic()
            self._metrics["source"] = {
                **self._metrics["source"],
                "resolution": None,
                "width": None,
                "height": None,
                "fps": None,
                "stream_name": None,
                "last_frame_at": None,
                "preview_saved_at": None,
                "preview_epoch": self._preview_epoch,
            }
            if not self.source_url:
                self._state = "waiting_for_source"
                self._metrics["source"] = {**self._metrics["source"], "status": "not_configured", "connections": 0}
                return {"action": "start", "status": self._state, "message_zh_tw": "尚未設定來源；Dashboard 已啟動，請先設定 RTSP、YouTube 或本地影片。"}
            self._stop_event.clear()
            self._state = "starting"
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._last_error = None
            self._thread = threading.Thread(target=self._run_monitor, name="meowkeeper-monitor", daemon=True)
            self._thread.start()
            return {"action": "start", "status": "starting", "message_zh_tw": "監控核心正在啟動。"}

    def stop_monitor(self, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ValueError("停止背景核心需要 confirm=true。")
        with self._lock:
            thread = self._thread
            ingest = self._ingest
            analyzer = self._adaptive_analyzer
            self._stop_event.set()
            if self._state not in {"stopped", "waiting_for_source"}:
                self._state = "stopping"
        if ingest is not None:
            ingest.close()
        if analyzer is not None:
            # Native CUDA/OpenCV calls may not return promptly. Reclaim only
            # the isolated AI child; source, ring buffer and Host stay intact.
            analyzer.cancel_pending()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10.0)
        with self._lock:
            if self._state != "error":
                self._state = "stopped"
            self._latest_jpeg = None
            self._frames_since_tick = 0
            self._metrics["ai"]["overlay"] = None
            self._last_tick = time.monotonic()
            self._metrics["source"] = {
                **self._metrics["source"],
                "status": "stopped",
                "connections": 0,
                "resolution": None,
                "width": None,
                "height": None,
                "fps": None,
                "stream_name": None,
                "last_frame_at": None,
            }
            self._metrics["ffmpeg"] = {**self._metrics["ffmpeg"], "status": "stopped"}
        return {"action": "stop", "status": self.state, "message_zh_tw": "監控核心已停止。"}

    def restart_monitor(self, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ValueError("重新啟動背景核心需要 confirm=true。")
        self.stop_monitor(confirm=True)
        result = self.start_monitor()
        result["action"] = "restart"
        return result

    def reconnect_source(self) -> dict[str, Any]:
        return self.restart_monitor(confirm=True)

    def create_preview_snapshot(self) -> Path | None:
        """Return the bounded AI preview JPEG produced by the single ingest."""

        return self.latest_jpeg_path

    def create_snapshot(self) -> Path | None:
        """Save one native-resolution snapshot, falling back to AI preview.

        An explicit snapshot (Dashboard button, Telegram or API) should use
        the current camera resolution when the active Xiaomi/go2rtc producer
        supports it.  The low-resolution ingest JPEG remains the fallback and
        the browser preview path, so preview polling never creates an extra
        camera connection.
        """

        with self._lock:
            provider = self._native_snapshot_provider
        from meowkeeper.core.media.native_snapshot import snapshot_from_ring
        ingest = getattr(self, "_ingest", None)
        ring_jpeg = (snapshot_from_ring(getattr(self, "_ring", None))
                     if ingest is None or ingest.stream_copy_active is True else None)
        snapshot_source = "compressed_ring_native" if ring_jpeg is not None else "go2rtc_native"
        if ring_jpeg is not None or provider is not None:
            try:
                jpeg = ring_jpeg if ring_jpeg is not None else provider()
            except (OSError, RuntimeError, ValueError):
                jpeg = None
            native_dimensions = self._jpeg_dimensions(jpeg) if isinstance(jpeg, bytes) else None
            if isinstance(jpeg, bytes) and native_dimensions is not None:
                self.preview_directory.mkdir(parents=True, exist_ok=True)
                target = self.preview_directory / "snapshot.jpg"
                temporary = self.preview_directory / "snapshot.tmp.jpg"
                try:
                    temporary.write_bytes(jpeg)
                    temporary.replace(target)
                    with self._lock:
                        self._metrics["snapshot"] = {
                            "status": "native",
                            "source": snapshot_source,
                            "width": native_dimensions[0],
                            "height": native_dimensions[1],
                        }
                    return target
                except OSError as exc:
                    self._log(f"⚠️ 原生解析度截圖保存失敗：{type(exc).__name__}；改用即時預覽影格。")
        fallback = self.latest_jpeg_path
        if fallback is not None:
            try:
                dimensions = self._jpeg_dimensions(fallback.read_bytes()) if fallback.is_file() else None
            except OSError:
                dimensions = None
            with self._lock:
                self._metrics["snapshot"] = {
                    "status": "preview_fallback",
                    "source": "ingest_preview",
                    "width": dimensions[0] if dimensions else None,
                    "height": dimensions[1] if dimensions else None,
                }
        return fallback

    def get_recent_clip(self, pre_seconds: float, post_seconds: float = 0.0) -> LiveClipResult:
        service = self._clip_service
        if service is None or self._ring is None:
            return LiveClipResult(None, "not_available", None, "目前尚未啟動壓縮環形快取。")
        return service.get_recent_clip(pre_seconds, post_seconds)

    def save_capture(self, kind: str) -> dict[str, Any]:
        """將共享影格或最近 15 秒快取保存到資料根目錄，不建立新來源。"""
        if kind not in {"photo", "video"}:
            raise ValueError("保存類型只支援截圖或影片。")
        if not self._capture_lock.acquire(blocking=False):
            return {"status": "busy", "message_zh_tw": "正在保存，請等完成後再操作。"}
        temporary: Path | None = None
        try:
            root = self.event_directory.parent
            root.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(root).free < 256 * 1024 * 1024:
                return {"status": "not_available", "message_zh_tw": "磁碟剩餘不足 268.4 MB，已停止保存。"}
            duration = None
            if kind == "photo":
                source = self.create_snapshot()
                if source is None or not source.is_file():
                    return {"status": "not_available", "message_zh_tw": "尚未收到即時影格，無法保存截圖。"}
                content = source.read_bytes()
                if self._jpeg_dimensions(content) is None:
                    return {"status": "not_available", "message_zh_tw": "目前影格不是有效 JPEG，請稍後再試。"}
            else:
                if self.retention_seconds < 15:
                    return {"status": "not_available", "message_zh_tw": "請將環形快取長度設為至少 15 秒。"}
                result = self.get_recent_clip(15.0, 0.0)
                source = result.path
                duration = result.actual_duration_seconds
                if source is None or not source.is_file():
                    return {"status": "not_available", "message_zh_tw": result.error or "最近影片快取尚未準備好。"}
            directory = root / kind
            directory.mkdir(parents=True, exist_ok=True)
            suffix = ".jpg" if kind == "photo" else ".mp4"
            filename = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + suffix
            target = directory / filename
            temporary = target.with_suffix(suffix + ".tmp")
            if kind == "photo":
                temporary.write_bytes(content)
            else:
                shutil.copyfile(source, temporary)
            temporary.replace(target)
            message = f"已保存至 {target}"
            snapshot_info = {}
            if kind == "photo":
                with self._lock:
                    snapshot_info = dict(self._metrics.get("snapshot", {}))
                dimensions = self._jpeg_dimensions(content)
                if snapshot_info.get("status") == "preview_fallback":
                    label = "1080p" if dimensions == (1920, 1080) else (
                        f"{dimensions[0]}×{dimensions[1]}" if dimensions else "目前")
                    message += f"；原始截圖未取得，已使用 {label} 預覽截圖。"
                elif dimensions:
                    message += f"（原始解析度 {dimensions[0]}×{dimensions[1]}）"
            if duration is not None:
                message += f"（實際 {duration:.1f} 秒；啟動初期或關鍵幀邊界可能與 15 秒不同）"
            return {"status": "saved", "path": str(target), "filename": filename,
                    "actual_duration_seconds": duration, "snapshot": snapshot_info, "message_zh_tw": message}
        except (OSError, ValueError, RuntimeError):
            return {"status": "failed", "message_zh_tw": "保存失敗，請檢查磁碟權限、空間與快取狀態。"}
        finally:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            finally:
                self._capture_lock.release()

    def transcode_clip_for_telegram(
        self, source_path: str | Path, destination_path: str | Path,
    ) -> ClipAssemblyResult:
        """沿用目前監控工作階段的 FFmpeg assembler，不建立第二個來源。"""

        with self._lock:
            service = self._clip_service
        if service is None:
            return ClipAssemblyResult(
                None, "failed", None, False, "目前監控工作階段已停止。",
            )
        return service.assembler.transcode_for_telegram(source_path, destination_path)

    def simulate_event(self, event_type: str = "cat_detected") -> dict[str, Any]:
        service = self._clip_service
        ring = self._ring
        if service is None or ring is None or not self.is_running:
            return {"status": "not_available", "message_zh_tw": "監控尚未執行，無法建立模擬事件。"}
        now = datetime.now(timezone.utc)
        event_id = str(uuid.uuid4())
        screenshot = self.latest_jpeg_path.read_bytes() if self.latest_jpeg_path else None
        accepted = service.submit_event(
            EventClipRequest(
                event_id=event_id,
                trigger_at=now,
                event_type=event_type or "cat_detected",
                source_id="web-simulated",
                confidence=1.0,
                pre_seconds=self.event_pre_seconds,
                post_seconds=self.event_post_seconds,
                screenshot_bytes=screenshot,
                priority=10,
                trigger_origin="simulated",
            )
        )
        payload = {"status": "accepted" if accepted else "rejected", "event_id": event_id}
        self._publish_event(payload | {"event_type": event_type, "trigger_at": now.isoformat()})
        return payload

    def set_source(self, source_url: str | None) -> None:
        with self._lock:
            if self.is_running:
                raise RuntimeError("監控執行期間不可直接更換來源，請先停止監控。")
            self.source_url = source_url.strip() if source_url and source_url.strip() else None
            self._latest_jpeg = None
            self._frames_since_tick = 0
            self._last_tick = time.monotonic()
            self._metrics["source"] = {
                **self._metrics["source"],
                "status": "stopped" if self.source_url else "not_configured",
                "connections": 0,
                "resolution": None,
                "width": None,
                "height": None,
                "fps": None,
                "last_frame_at": None,
            }

    def configure_source(self, source_url: str | None) -> dict[str, Any]:
        """更新下一次監控要使用的來源，不在 Web 請求中偷偷建立第二條來源。"""

        try:
            self.set_source(source_url)
        except (RuntimeError, ValueError) as exc:
            return {"status": "rejected", "message_zh_tw": str(exc)}
        return {
            "status": "accepted",
            "configured": bool(self.source_url),
            "message_zh_tw": "來源設定已接入核心；請按下開始監控或重新啟動 Host。",
        }

    def apply_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        """Apply safe scalar settings for the next monitor session.

        A running ingest is never rebuilt from a Web request.  The Dashboard
        persists the values and asks for a Host restart; updating these fields
        here keeps status/config responses coherent until that restart.
        """

        with self._lock:
            if "analysis_fps" in values:
                self.analysis_fps = int(values["analysis_fps"])
            if "analysis_width" in values:
                self.analysis_width = int(values["analysis_width"])
            if "inference_device" in values:
                self.inference_device = values["inference_device"]
            if "activity_fps" in values:
                self.activity_fps = int(values["activity_fps"])
            if "identity_frame_width" in values:
                self.identity_frame_width = int(values["identity_frame_width"])
            if "preview_frame_width" in values:
                self.preview_frame_width = int(values["preview_frame_width"])
            if "confidence_threshold" in values:
                self.confidence_threshold = float(values["confidence_threshold"])
            if "identity_accepted_threshold" in values:
                self.identity_accepted_threshold = float(values["identity_accepted_threshold"])
            if "identity_minimum_margin" in values:
                self.identity_minimum_margin = float(values["identity_minimum_margin"])
            if "event_pre_seconds" in values:
                self.event_pre_seconds = float(values["event_pre_seconds"])
            if "event_post_seconds" in values:
                self.event_post_seconds = float(values["event_post_seconds"])
            if "cooldown_seconds" in values:
                self.cooldown_seconds = float(values["cooldown_seconds"])
            if "retention_seconds" in values:
                self.retention_seconds = float(values["retention_seconds"])
            if "ring_maximum_size_mb" in values:
                self.maximum_size_mb = int(values["ring_maximum_size_mb"])
            self._metrics["ring_buffer"] = {
                **self._metrics["ring_buffer"],
                "capacity_seconds": self.retention_seconds,
            }
        return {"status": "accepted", "restart_required": True}

    def test_telegram(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """只在使用者明確按下測試時送出一則文字，不影響影像主路徑。"""

        del _payload

        if not self.telegram_enabled or self.telegram_notifier is None or not self.telegram_chat_ids:
            return {
                "status": "disabled",
                "message_zh_tw": "Telegram 尚未啟用；請設定環境變數並明確啟用通知。",
            }
        sent = 0
        try:
            for chat_id in self.telegram_chat_ids:
                self.telegram_notifier.send_message(
                    chat_id,
                    "喵喵守護者+ 測試通知成功。後續貓咪事件會依冷卻規則推送。",
                )
                sent += 1
        except Exception as exc:  # noqa: BLE001 - diagnostic call must not stop Host
            return {"status": "failed", "sent": sent, "message_zh_tw": f"Telegram 測試失敗：{type(exc).__name__}。"}
        return {"status": "sent", "sent": sent, "message_zh_tw": f"Telegram 測試通知已傳送至 {sent} 位使用者。"}

    def close(self) -> None:
        if self.is_running or self._thread is not None:
            try:
                self.stop_monitor(confirm=True)
            except Exception as exc:  # noqa: BLE001 - shutdown must continue
                self._log(f"⚠️ 核心關閉時發生例外：{type(exc).__name__}")
        if self._daily_monitoring is not None:
            self._daily_monitoring.close()
            self._daily_monitoring = None

    def _run_monitor(self) -> None:
        ingest: FfmpegMediaIngest | None = None
        reader: LatestFrameReader | None = None
        clip_service: EventClipService | None = None
        semantic_service: EventSemanticService | None = None
        analyzer = None
        try:
            youtube_adapter = (
                YouTubeSourceAdapter(
                    YouTubeSourceConfig(self.source_url or "", max_height=720),
                    ffmpeg_path=self.ffmpeg_path,
                )
                if is_youtube_url(self.source_url or "")
                else None
            )
            source_resolver = youtube_adapter or YouTubeStreamResolver()
            resolved = resolve_source(self.source_url or "", resolver=source_resolver)
            source_id = (
                f"youtube:{youtube_adapter.media_info.video_id}"
                if youtube_adapter and youtube_adapter.media_info and youtube_adapter.media_info.video_id
                else ("youtube:unknown" if youtube_adapter else ("local_file" if resolved.resolver == "local_file" else resolved.safe_host))
            )
            source_parts = urlsplit(self.source_url or "")
            stream_name = None
            if source_parts.hostname == "127.0.0.1" and source_parts.path.startswith("/xiaomi_camera_"):
                source_id = "Xiaomi（本機 go2rtc）"
                stream_name = source_parts.path.rsplit("/", 1)[-1]
            ring = DiskSegmentRingBuffer(
                self.ring_directory,
                retention_seconds=self.retention_seconds,
                segment_seconds=self.segment_seconds,
                maximum_size_mb=self.maximum_size_mb,
            )
            from meowkeeper.storage.semantic_jobs import SemanticJobJournal
            semantic_service = EventSemanticService(
                self.vision_provider, result_sink=self.store.record_event_semantics,
                journal=SemanticJobJournal(self.store),
            )
            clip_service = EventClipService(
                ring,
                ClipAssembler(ffmpeg_path=self.ffmpeg_path),
                output_directory=self.event_directory,
                store=self.store,
                notifier=self.telegram_notifier,
                chat_ids=self.telegram_chat_ids,
                telegram_enabled=self.telegram_enabled,
                telegram_dispatcher=self.telegram_dispatcher,
                cat_notification_preferences=self.cat_notification_preferences,
                cooldown_seconds=self.cooldown_seconds,
                logger=self._log,
                semantic_service=semantic_service,
                semantic_wait_seconds=self.semantic_wait_seconds,
            )
            analyzer = AdaptiveAnalyzer(self._create_analyzer(), fps=self.analysis_fps,
                                        input_size=self.analysis_width, logger=self._log)
            semantic_service.pressure_probe = self.semantic_pressure
            if semantic_service is not None and self.vision_provider is not None:
                enable_frames = getattr(analyzer, "enable_semantic_frames", None)
                if callable(enable_frames):
                    enable_frames(getattr(self.vision_provider, "storyboard_options", None))
                    semantic_service.input_preparer = analyzer.prepare_semantic
                    # AdaptiveAnalyzer delegates to the same underlying V0.2 adapter.
                    semantic_service.dispatch_validator = getattr(analyzer,"validate_semantic",None)
            with self._lock:
                self._adaptive_analyzer = analyzer
                self._semantic_service = semantic_service
            pipeline = AnalysisPipeline(analyzer, cooldown_seconds=self.cooldown_seconds)
            if self._daily_monitoring is None:
                self._daily_monitoring = DailyMonitoringRecorder(self.store, logger=self._log)
            daily_recorder = self._daily_monitoring
            ingest = FfmpegMediaIngest(
                self.source_url or "",
                ring,
                resolver=source_resolver,
                ffmpeg_path=self.ffmpeg_path,
                fps=self.analysis_fps,
                width=max(self.preview_frame_width, self.analysis_width,
                          self.identity_frame_width if self.analyzer_mode == "v02" else 0),
            )
            with self._lock:
                self._ring = ring
                self._clip_service = clip_service
                self._ingest = ingest
                self._state = "running"
                self._metrics["source"] = {
                    **self._metrics["source"],
                    "status": "running",
                    "source_id": source_id,
                    "stream_name": stream_name,
                    "connections": 1,
                }
                self._metrics["ffmpeg"] = {**self._metrics["ffmpeg"], "status": "running"}
                self._metrics["ai"] = {
                    **self._metrics["ai"],
                    "backend": getattr(analyzer, "inference_backend", "noop"),
                    "device": getattr(analyzer, "device", "cpu"),
                    "inference_input_size": self.analysis_width,
                    "shared_frame_max_width": ingest.width,
                }
            received_count = 0
            received_since = time.monotonic()

            def receive_frame(frame: FramePacket) -> None:
                nonlocal received_count, received_since
                self._save_latest_jpeg(frame)
                now = time.monotonic()
                received_count += 1
                with self._lock:
                    self._last_frame_at = frame.captured_at.timestamp()
                    self._metrics["source"]["last_frame_at"] = frame.captured_at.isoformat()
                    if now - received_since >= 1:
                        self._metrics["source"]["fps"] = round(received_count / (now - received_since), 2)
                        received_count = 0
                        received_since = now

            reader = LatestFrameReader(ingest.iter_frames(resolved=resolved),
                                       on_frame=receive_frame, close_source=ingest.close)
            for frame in reader:
                if self._stop_event.is_set():
                    break
                analysis_call_at = time.perf_counter()
                event = pipeline.process(frame)
                analysis_call_ms = (time.perf_counter() - analysis_call_at) * 1000
                analysis = pipeline.last_analysis
                count = analysis.details.get("cat_count") if analysis is not None else None
                daily_recorder.observe(
                    frame.captured_at, cat_present=count > 0 if type(count) is int and count >= 0 else None,
                )
                self._update_runtime_metrics(frame, ingest, ring, analyzer)
                with self._lock:
                    self._metrics["ai"]["dropped_frames"] = reader.dropped
                    self._metrics["ai"]["frame_reader"] = reader.snapshot()
                    self._metrics["ai"]["analysis_call_ms"] = analysis_call_ms
                    if analysis is not None and not analysis.details.get("analysis_skipped"):
                        self._metrics["ai"]["overlay"] = update_live_overlay(
                            self._metrics["ai"].get("overlay"),
                            analysis.details.get("detections", []),
                            source_epoch=self._preview_epoch,
                            captured_at=frame.captured_at,
                        )
                    adaptive_status = analyzer.snapshot()
                    activity_mode = (adaptive_status.get("activity_sampling") or {}).get("mode", "normal")
                    self._metrics["ai"].update({
                        "backend": analyzer.inference_backend,
                        "device": analyzer.device,
                        "activity_mode": activity_mode,
                        "inference_input_size": adaptive_status["effective_input_size"],
                        "adaptive": adaptive_status,
                    })
                if event is None:
                    continue
                event_id = str(uuid.uuid4())
                accepted = clip_service.submit_event(
                    EventClipRequest(
                        event_id=event_id,
                        trigger_at=frame.captured_at,
                        event_type=event.analysis.event_type or "cat_detected",
                        source_id=source_id,
                        track_id=event.analysis.details.get("track_id"),
                        track_ids=tuple(event.analysis.details.get("track_ids", ()))[:20],
                        pet_id=event.analysis.details.get("pet_id"),
                        confidence=event.analysis.confidence or 0.0,
                        trigger_origin="detector",
                        model_version=self._model_version,
                        model_sha256=self._model_sha256,
                        confidence_threshold=self.confidence_threshold,
                        pre_seconds=self.event_pre_seconds,
                        post_seconds=self.event_post_seconds,
                        screenshot_bytes=frame.jpeg_bytes,
                    )
                )
                with self._lock:
                    self._event_count += 1
                self._publish_event(
                    {
                        "event_id": event_id,
                        "event_type": event.analysis.event_type or "cat_detected",
                        "trigger_at": frame.captured_at.isoformat(),
                        "confidence": event.analysis.confidence,
                        "accepted": accepted,
                        "message_zh_tw": event.analysis.message_zh_tw,
                    }
                )
        except (MediaSourceError, AnalysisConfigurationError, OSError, ValueError) as exc:
            with self._lock:
                self._last_error = str(exc)[:300]
                self._state = "error"
                self._metrics["source"] = {
                    **self._metrics["source"],
                    "status": "error",
                    "connections": 0,
                    "resolution": None,
                    "width": None,
                    "height": None,
                    "fps": None,
                    "last_frame_at": None,
                }
                self._metrics["ffmpeg"] = {**self._metrics["ffmpeg"], "status": "error", "last_error": type(exc).__name__}
            self._log(f"❌ 監控核心停止：{type(exc).__name__}。")
        except Exception as exc:  # noqa: BLE001 - worker must not kill Host
            with self._lock:
                self._last_error = type(exc).__name__
                self._state = "error"
                self._metrics["source"] = {
                    **self._metrics["source"],
                    "status": "error",
                    "connections": 0,
                    "resolution": None,
                    "width": None,
                    "height": None,
                    "fps": None,
                    "last_frame_at": None,
                }
            self._log(f"❌ 監控核心發生未預期錯誤：{type(exc).__name__}。")
        finally:
            with self._lock:
                self._adaptive_analyzer = None
                self._semantic_service = None
            if reader is not None:
                reader.close()
            if self._daily_monitoring is not None:
                self._daily_monitoring.end_session()
            if ingest is not None:
                ingest.close()
            if analyzer is not None and hasattr(analyzer, "close"):
                try:
                    analyzer.close()
                except (OSError, RuntimeError, ValueError):
                    self._log("⚠️ 實驗性分析器結束時有未完成資料，請查看診斷。")
            if clip_service is not None:
                clip_service.wait_for_idle(5.0)
                clip_service.close(wait=True)
            if semantic_service is not None:
                semantic_service.close(wait=True)
            with self._lock:
                self._ingest = None
                self._ring = None
                self._clip_service = None
                self._thread = None
                # Keep the last real frame after a finite local/VOD source
                # reaches EOF so an explicit snapshot can still be inspected.
                # An explicit stop, source change, error or the next start
                # clears it; this never turns a stale frame into live preview.
                if self._stop_event.is_set() or self._state == "error":
                    self._latest_jpeg = None
                if self._state not in {"error", "waiting_for_source"}:
                    self._state = "stopped"
                self._metrics["source"] = {
                    **self._metrics["source"],
                    "connections": 0,
                    "resolution": None,
                    "width": None,
                    "height": None,
                    "fps": None,
                    "last_frame_at": None,
                }
                if self._state == "stopped":
                    self._metrics["ffmpeg"] = {**self._metrics["ffmpeg"], "status": "stopped"}

    def _create_analyzer(self) -> Any:
        if self.analyzer_mode.lower() not in {"yolo", "v02"}:
            return NoopFrameAnalyzer()
        try:
            if not self.model_path:
                from meowkeeper.runtime import default_model_path

                self.model_path = default_model_path()
            if not self.model_path:
                raise AnalysisConfigurationError("找不到預設 YOLO 模型。")
            model_file = Path(self.model_path)
            self._model_version = model_file.name
            try:
                self._model_sha256 = hashlib.sha256(model_file.read_bytes()).hexdigest()
            except OSError:
                self._model_sha256 = None
            if self.analyzer_mode.lower() == "v02":
                from meowkeeper.core.events.v02_host_analyzer import V02HostAnalyzer
                from meowkeeper.core.identity.base import IdentityThresholds
                th = IdentityThresholds(
                    accepted_threshold=self.identity_accepted_threshold,
                    minimum_margin=self.identity_minimum_margin,
                )
                try:
                    return V02HostAnalyzer.create(
                        model_path=self.model_path, confidence=self.confidence_threshold,
                        store=self.store, data_root=self.event_directory.parent, logger=self._log,
                        notifier=self.telegram_notifier if self.telegram_enabled else None,
                        chat_ids=self.telegram_chat_ids if self.telegram_enabled else (),
                        notification_dispatcher=self.telegram_dispatcher,
                        feedback_preferences=self.cat_notification_preferences,
                        tracker=IsolatedTracker(self.model_path, confidence_threshold=self.confidence_threshold,
                                                input_size=self.analysis_width, device=self.inference_device),
                        identity_thresholds=th,
                    )
                except (OSError, RuntimeError, ValueError, ImportError) as error:
                    self._log(f"⚠️ V0.2 尚未可用，降級單一 YOLO 偵測：{type(error).__name__}。")
            return IsolatedInferenceWorker("detect", {
                "model_path": self.model_path,
                "confidence_threshold": self.confidence_threshold,
                "input_size": self.analysis_width,
                "device": self.inference_device,
            })
        except (AnalysisConfigurationError, ImportError, OSError) as exc:
            self._log(f"⚠️ YOLO 尚未可用，已安全使用 noop 分析：{type(exc).__name__}。")
            with self._lock:
                self._metrics["ai"] = {**self._metrics["ai"], "backend": "unavailable", "device": "cpu", "warning": str(exc)[:200]}
            return NoopFrameAnalyzer()

    def _save_latest_jpeg(self, frame: FramePacket) -> None:
        target = self.preview_directory / "latest.jpg"
        temporary = self.preview_directory / f"latest.{uuid.uuid4().hex}.tmp.jpg"
        operation = "建立快取目錄"
        try:
            self.preview_directory.mkdir(parents=True, exist_ok=True)
            pending = getattr(self, "_preview_pending_cleanup", None)
            if pending is not None:
                try:
                    pending.unlink(missing_ok=True)
                    self._preview_pending_cleanup = None
                except OSError:
                    pass  # A stale cleanup lock must not prevent fresh frames.
            operation = "寫入新影格"
            temporary.write_bytes(frame.jpeg_bytes)
            operation = "原子替換預覽"
            for attempt in range(3):
                try:
                    temporary.replace(target)
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(.01 * (attempt + 1))
        except OSError as exc:
            now = time.monotonic()
            self._preview_write_failures = getattr(self, "_preview_write_failures", 0) + 1
            with self._lock:
                self._metrics["source"]["preview_cache_status"] = "write_failed"
                self._metrics["source"]["preview_cache_error_operation"] = operation
            if now - getattr(self, "_preview_error_logged_at", -60) >= 30:
                self._preview_error_logged_at = now
                code = getattr(exc, "winerror", None) or exc.errno
                self._log(f"⚠️ 即時預覽快取：{operation}失敗，{type(exc).__name__}（系統碼 {code}）；連續失敗 {self._preview_write_failures} 張。舊預覽可能停留，影音接收仍持續；系統碼不能單獨證明是權限或防毒占用。")
            return
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                self._preview_pending_cleanup = temporary
        with self._lock:
            self._latest_jpeg = target
            self._metrics["source"]["preview_saved_at"] = frame.captured_at.isoformat()
            self._metrics["source"]["preview_cache_status"] = "ready"
            self._metrics["source"]["preview_cache_error_operation"] = None
        if getattr(self, "_preview_write_failures", 0):
            self._log(f"ℹ️ 即時預覽快取已恢復；先前連續 {self._preview_write_failures} 張寫入失敗，現在已保存新影格。")
            self._preview_write_failures = 0

    @staticmethod
    def _jpeg_dimensions(jpeg_bytes: bytes) -> tuple[int, int] | None:
        """Read JPEG dimensions without decoding a second copy of the frame.

        The live preview is already produced by the single FFmpeg ingest.  A
        small marker scan lets the Dashboard report the dimensions of that
        exact preview frame without opening another decoder or source.
        """

        if len(jpeg_bytes) < 4 or jpeg_bytes[:2] != b"\xff\xd8":
            return None
        index = 2
        sof_markers = {
            *range(0xC0, 0xC4),
            *range(0xC5, 0xC8),
            *range(0xC9, 0xCC),
            *range(0xCD, 0xD0),
        }
        while index + 3 < len(jpeg_bytes):
            if jpeg_bytes[index] != 0xFF:
                index += 1
                continue
            while index < len(jpeg_bytes) and jpeg_bytes[index] == 0xFF:
                index += 1
            if index >= len(jpeg_bytes):
                break
            marker = jpeg_bytes[index]
            index += 1
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if index + 2 > len(jpeg_bytes):
                break
            segment_length = int.from_bytes(jpeg_bytes[index : index + 2], "big")
            if segment_length < 2 or index + segment_length > len(jpeg_bytes):
                break
            if marker in sof_markers and segment_length >= 7:
                height = int.from_bytes(jpeg_bytes[index + 3 : index + 5], "big")
                width = int.from_bytes(jpeg_bytes[index + 5 : index + 7], "big")
                if width > 0 and height > 0:
                    return width, height
            index += segment_length
        return None

    def _update_runtime_metrics(self, frame: FramePacket, ingest: FfmpegMediaIngest, ring: DiskSegmentRingBuffer, analyzer: Any) -> None:
        ring_status = ring.status()
        estimate = ring.estimated_bitrate() if hasattr(ring, "estimated_bitrate") else None
        identity_status = None
        analysis_pipeline = getattr(analyzer, "pipeline", None)
        resolver = getattr(analysis_pipeline, "identity_resolver", None)
        if resolver is not None and hasattr(resolver, "diagnostic_snapshot"):
            identity_status = resolver.diagnostic_snapshot()
        with self._lock:
            # AI may finish an older frame after ingest has received a newer
            # one. Never regress the ingest watermark to analysis time.
            self._last_frame_at = max(self._last_frame_at or 0, frame.captured_at.timestamp())
            if identity_status is not None:
                self._metrics["identity"] = identity_status
            self._metrics["source"] = {
                **self._metrics["source"],
                "last_frame_at": datetime.fromtimestamp(self._last_frame_at, timezone.utc).isoformat(),
                "estimated_bitrate_bps": estimate["bps"] if estimate else None,
                "bitrate_sample_seconds": estimate["sample_seconds"] if estimate else None,
                "bitrate_basis": "compressed_cache" if estimate else None,
            }
            dimensions = self._jpeg_dimensions(frame.jpeg_bytes)
            if dimensions is not None:
                width, height = dimensions
                self._metrics["source"] = {
                    **self._metrics["source"],
                    "resolution": f"{width}×{height}",
                    "width": width,
                    "height": height,
                }
            self._metrics["ffmpeg"] = {
                **self._metrics["ffmpeg"],
                "status": "running",
                "restart_count": ingest.restart_count,
                "last_error": ingest.last_error,
            }
            self._metrics["ring_buffer"] = {
                **self._metrics["ring_buffer"],
                "seconds": ring_status.duration_seconds,
                "bytes": ring_status.size_bytes,
                "capacity_seconds": ring_status.retention_seconds,
                "segment_count": ring_status.segment_count,
            }
            if isinstance(analyzer, AdaptiveAnalyzer):
                self._metrics["ai"]["fps"] = analyzer.measured_fps()

    def _publish_event(self, payload: dict[str, Any]) -> None:
        sink = self.event_sink
        if sink is None:
            return
        try:
            sink(payload)
        except Exception as exc:  # noqa: BLE001 - event UI cannot stop core
            self._log(f"⚠️ Web 事件通知失敗：{type(exc).__name__}。")

    def _log(self, message: str) -> None:
        try:
            self.logger(message)
        except Exception:  # noqa: BLE001 - logging cannot stop core
            self.logger = lambda _message: None


__all__ = ["CoreController", "RepositoryCoreController"]
