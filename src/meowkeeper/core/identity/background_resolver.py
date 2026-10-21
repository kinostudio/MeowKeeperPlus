"""低優先、可去重的背景身分解析器。"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass
from datetime import datetime, timezone
from queue import Empty, Queue

from meowkeeper.core.inference import (
    GPUInferenceScheduler,
    InferencePriority,
)

from .base import IdentityPrediction
from .quality_filter import ReferenceQualityFilter


@dataclass(frozen=True)
class IdentityResolution:
    track_id: str | int
    prediction: IdentityPrediction
    captured_at: datetime
    source_id: str
    generation: int
    submitted_at: datetime
    frame_id: str | None = None


class BackgroundIdentityResolver:
    """把 DINO／色彩身分工作移出行為主路徑。"""

    def __init__(
        self,
        classifier: object,
        *,
        scheduler: GPUInferenceScheduler | None = None,
        recheck_interval_seconds: float = 10.0,
        maximum_pending_tasks: int = 2,
        task_timeout_seconds: float = 3.0,
        minimum_crop_pixels: int = 96,
        low_confidence_threshold: float = 0.62,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        if recheck_interval_seconds <= 0 or task_timeout_seconds <= 0:
            raise ValueError("身分背景工作間隔與逾時必須大於零。")
        if maximum_pending_tasks < 1 or minimum_crop_pixels < 1:
            raise ValueError("身分背景工作佇列與 crop 尺寸設定不正確。")
        self.classifier = classifier
        self.scheduler = scheduler or GPUInferenceScheduler(max_pending_tasks=maximum_pending_tasks)
        self._owns_scheduler = scheduler is None
        self.recheck_interval_seconds = recheck_interval_seconds
        self.maximum_pending_tasks = maximum_pending_tasks
        self.task_timeout_seconds = task_timeout_seconds
        self.minimum_crop_pixels = minimum_crop_pixels
        self.low_confidence_threshold = low_confidence_threshold
        self.logger = logger
        self._quality_filter = ReferenceQualityFilter(
            minimum_width=minimum_crop_pixels,
            minimum_height=minimum_crop_pixels,
        )
        self._pending: dict[str | int, object] = {}
        self._last_submitted: dict[str | int, datetime] = {}
        self._last_resolved: dict[str | int, datetime] = {}
        self._completed: Queue[IdentityResolution] = Queue()
        self._recent_crops: dict[str | int, list] = {}
        self._lock = threading.Lock()
        self._generation = 0
        self._diagnostics: dict[str, object] = {"stage": "waiting_for_cat", "submitted": 0,
                                               "completed": 0, "quality_rejected": 0, "failed": 0}

    def diagnostic_snapshot(self) -> dict[str, object]:
        """固定大小、無身分原值的背景辨識診斷。"""
        with self._lock:
            return {**self._diagnostics, "pending": len(self._pending)}

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def is_pending(self, track_id: str | int) -> bool:
        with self._lock:
            return track_id in self._pending

    @property
    def generation(self) -> int:
        return self._generation

    def next_generation(self) -> int:
        with self._lock:
            pending = list(self._pending.values())
            self._generation += 1
            self._pending.clear()
            self._last_submitted.clear()
            self._last_resolved.clear()
            self._recent_crops.clear()
            while True:
                try:
                    self._completed.get_nowait()
                except Empty:
                    break
        for future in pending:
            task_id = getattr(future, "task_id", None)
            if task_id:
                self.scheduler.cancel(task_id)
        return self._generation

    def should_submit(
        self,
        track_id: str | int,
        captured_at: datetime,
        prediction: IdentityPrediction | None,
    ) -> bool:
        with self._lock:
            if track_id in self._pending:
                return False
            last = self._last_resolved.get(track_id)
            if last is None:
                return True
            del captured_at
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed >= self.recheck_interval_seconds:
                return True
            if prediction is not None and (prediction.is_unknown or prediction.valid_frames > 0):
                return elapsed >= min(2.0, self.recheck_interval_seconds)
            return prediction is not None and prediction.similarity < self.low_confidence_threshold

    def submit(
        self,
        *,
        track_id: str | int,
        crop_jpeg: bytes | None,
        captured_at: datetime,
        source_id: str,
        generation: int | None = None,
    ) -> bool:
        """嘗試排入一次工作；失敗只回傳 False，不阻塞行為判斷。"""

        if not crop_jpeg:
            return False
        try:
            local_quality = ReferenceQualityFilter(minimum_width=self.minimum_crop_pixels,
                                                  minimum_height=self.minimum_crop_pixels)
            quality = local_quality.assess(crop_jpeg)
        except (RuntimeError, ValueError):
            return False
        if not quality.accepted or quality.width < self.minimum_crop_pixels or quality.height < self.minimum_crop_pixels:
            with self._lock:
                self._diagnostics.update(stage="crop_too_small" if min(quality.width, quality.height)
                                         < self.minimum_crop_pixels else "crop_blurry",
                                         width=quality.width, height=quality.height,
                                         quality_rejected=int(self._diagnostics["quality_rejected"]) + 1)
            self._log("ℹ️ 身分 crop 品質不足，暫保留為神秘喵喵。")
            return False
        with self._lock:
            if track_id in self._pending or len(self._pending) >= self.maximum_pending_tasks:
                return False
            import numpy as np

            signature = local_quality._hashes[-1]
            recent = [(ts, sig) for ts, sig in self._recent_crops.get(track_id, [])
                      if 0 <= (captured_at-ts).total_seconds() <= 12]
            if any(np.count_nonzero(signature != sig) <= local_quality.duplicate_distance for _, sig in recent):
                self._diagnostics.update(stage="duplicate_frame")
                return False
            self._recent_crops[track_id] = (recent + [(captured_at, signature)])[-12:]
            while len(self._recent_crops) > 128:
                self._recent_crops.pop(next(iter(self._recent_crops)))
            task_generation = self._generation if generation is None else generation
            submitted_at = datetime.now(timezone.utc)
            self._last_submitted[track_id] = submitted_at
            dedupe_key = f"identity:{task_generation}:{track_id}"
            self._diagnostics.update(stage="queued",
                                     submitted=int(self._diagnostics["submitted"]) + 1)

            def work() -> IdentityPrediction:
                return self.classifier.classify(crop_jpeg, track_id=track_id)

            future = self.scheduler.submit(
                work,
                priority=InferencePriority.LOW,
                track_id=track_id,
                dedupe_key=dedupe_key,
                timeout_seconds=self.task_timeout_seconds,
            )
            self._pending[track_id] = future
            future.add_done_callback(
                lambda completed, tid=track_id, ts=captured_at, sid=source_id, gen=task_generation, submitted=submitted_at: self._done(
                    completed,
                    tid,
                    ts,
                    sid,
                    gen,
                    submitted,
                    hashlib.sha256(crop_jpeg).hexdigest(),
                )
            )
            return True

    def drain_completed(self, *, limit: int | None = None) -> list[IdentityResolution]:
        results: list[IdentityResolution] = []
        while limit is None or len(results) < limit:
            try:
                results.append(self._completed.get_nowait())
            except Empty:
                break
        return results

    def wait_for_idle(self, timeout_seconds: float = 0.0) -> list[IdentityResolution]:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            results = self.drain_completed()
            with self._lock:
                pending = bool(self._pending)
            if not pending or time.monotonic() >= deadline:
                return results + self.drain_completed()
            time.sleep(0.01)

    def close(self, *, wait: bool = False) -> None:
        if self._owns_scheduler:
            self.scheduler.shutdown(wait=wait)

    def _done(
        self,
        future: object,
        track_id: str | int,
        captured_at: datetime,
        source_id: str,
        generation: int,
        submitted_at: datetime,
        frame_id: str | None = None,
    ) -> None:
        try:
            prediction = future.result()  # type: ignore[union-attr]
        except (CancelledError, TimeoutError, RuntimeError, ValueError, OSError) as exc:
            with self._lock:
                self._pending.pop(track_id, None)
                self._diagnostics.update(stage="background_failed", error_type=type(exc).__name__,
                                         failed=int(self._diagnostics["failed"]) + 1)
            self._log(f"ℹ️ 身分背景工作暫停：{type(exc).__name__}；保留 unknown。")
            return
        if not isinstance(prediction, IdentityPrediction):
            with self._lock:
                self._pending.pop(track_id, None)
            self._log("ℹ️ 身分模型回傳格式不符；保留 unknown。")
            return
        with self._lock:
            stage = "candidate" if not prediction.is_unknown else "score_insufficient"
            if getattr(getattr(self.classifier, "embedder", None), "backend_name", None) == "disabled":
                stage = "model_unavailable"
            elif prediction.reason.startswith("embedding 失敗"):
                stage = "embedding_failed"
            self._diagnostics.update(stage=stage,
                                     similarity=prediction.similarity, margin=prediction.confidence_margin,
                                     completed=int(self._diagnostics["completed"]) + 1)
            self._last_resolved[track_id] = datetime.now(timezone.utc)
            self._completed.put(
                IdentityResolution(
                    track_id=track_id,
                    prediction=prediction,
                    captured_at=captured_at,
                    source_id=source_id,
                    generation=generation,
                    submitted_at=submitted_at,
                    frame_id=frame_id,
                )
            )
            self._pending.pop(track_id, None)

    def _log(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger(message)
            except Exception:  # noqa: BLE001 - logging must not affect the main path
                return
