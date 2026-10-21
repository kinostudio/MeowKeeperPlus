"""可選的事件代表圖分析；獨立資料契約避免媒體與事件套件循環匯入。"""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from typing import Any, Protocol
from uuid import UUID, uuid4

from meowkeeper.core.semantic_storyboard import TrackStoryboard

TAG_LABELS = {
    "walking": "行走", "running": "奔跑", "jumping": "跳躍",
    "eating": "進食", "drinking": "喝水", "grooming": "理毛",
    "sleeping": "睡覺", "resting": "休息", "playing": "玩耍",
    "litter_box": "使用貓砂", "interacting": "互動", "other_active": "活動",
    "sitting": "坐著", "lying": "躺著", "standing": "站著",
    "near_food": "靠近食盆", "near_water": "靠近飲水區", "in_litter_area": "進入貓砂區",
    "possible": "疑似", "unknown": "無法判斷",
}
TAXONOMY_VERSION = "meowkeeper-tags-v1"
SPATIAL_TAGS = frozenset({"near_food", "near_water", "in_litter_area"})
TAG_DIMENSIONS = {code: ("location" if code in SPATIAL_TAGS else
                        "posture" if code in {"sitting", "lying", "standing"} else
                        "consumption" if code in {"eating", "drinking"} else
                        "uncertainty" if code in {"possible", "unknown"} else "action") for code in TAG_LABELS}

FAILURE_CATEGORIES = frozenset({"auth_error", "rate_limited", "unavailable", "bad_response",
                                "timeout", "skipped_disabled", "skipped_no_consent",
                                "skipped_busy", "insufficient_evidence", "invalid_image"})
FAILURE_CATEGORIES |= {"skipped_budget", "circuit_open", "duplicate_request"}
FAILURE_CATEGORIES |= {"skipped_pressure"}
FAILURE_CATEGORIES |= {"skipped_rpm", "skipped_tpm", "token_estimate_required"}
FAILURE_CATEGORIES |= {"skipped_cooldown"}


def safe_http_timings(value):
    if value is None:
        return ()
    if not isinstance(value, Mapping) or set(value)-{"http_started_at","response_received_at"}:
        raise ValueError("HTTP時間證據格式不正確。")
    for timestamp in value.values():
        if not isinstance(timestamp,str) or datetime.fromisoformat(timestamp).utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("HTTP時間證據需為UTC。")
    return tuple(value.items())


class VisionFailure(RuntimeError):
    def __init__(self, category: str, http_status: int | None = None, retry_after_seconds: float | None = None,
                 *, request_dispatched: bool | None = None, api_elapsed_ms: float | None = None, http_timings=None):
        if category not in FAILURE_CATEGORIES:
            raise ValueError("視覺服務錯誤分類不正確。")
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            raise ValueError("視覺服務 HTTP 狀態不正確。")
        if retry_after_seconds is not None and (type(retry_after_seconds) not in {int, float}
                                               or not math.isfinite(retry_after_seconds)
                                               or not 0 <= retry_after_seconds <= 86400):
            raise ValueError("視覺服務重試等待不正確。")
        self.category = category
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        if request_dispatched is not None and type(request_dispatched) is not bool:
            raise ValueError("視覺服務請求證據不正確。")
        self.request_dispatched = (True if http_status is not None else
                                   False if category.startswith("skipped_") or category in {"invalid_image", "insufficient_evidence", "circuit_open", "duplicate_request", "token_estimate_required"}
                                   else request_dispatched)
        if api_elapsed_ms is not None and (type(api_elapsed_ms) not in {int, float} or not math.isfinite(api_elapsed_ms) or api_elapsed_ms < 0):
            raise ValueError("視覺服務回應耗時不正確。")
        self.api_elapsed_ms = api_elapsed_ms
        self.http_timings = safe_http_timings(http_timings)
        super().__init__("進階影像分析未完成；本機監控與影片通知不受影響。")


class ProviderReply(dict):
    """Only the local adapter supplies these attributes; model JSON cannot."""
    def __init__(self, body, *, request_dispatched=None, http_status=None, api_elapsed_ms=None, http_timings=None,
                 input_tokens=None, output_tokens=None):
        if request_dispatched is not None and type(request_dispatched) is not bool:
            raise ValueError("服務端請求證據不正確。")
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            raise ValueError("服務端HTTP證據不正確。")
        super().__init__(body)
        self.request_dispatched = request_dispatched
        self.http_status = http_status
        if api_elapsed_ms is not None and (type(api_elapsed_ms) not in {int, float} or not math.isfinite(api_elapsed_ms) or api_elapsed_ms < 0):
            raise ValueError("服務端回應耗時不正確。")
        self.api_elapsed_ms = api_elapsed_ms
        self.http_timings = safe_http_timings(http_timings)
        for value in (input_tokens, output_tokens):
            if value is not None and (type(value) is not int or not 0 <= value <= 100_000_000):
                raise ValueError("服務端 Token 使用量不正確。")
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


@dataclass(frozen=True)
class SemanticRequest:
    event_id: str
    jpeg: bytes
    track_ids: tuple[str, ...] = ()
    trigger_at: datetime | None = None
    storyboards: tuple[TrackStoryboard, ...] = ()
    track_epoch: str = "unverified"
    input_revision: int = 1
    scene_reference_jpeg: bytes | None = None
    scene_reference_labels: tuple[str, ...] = ()

    def __post_init__(self):
        if (type(self.input_revision) is not int or not 1 <= self.input_revision <= 1000000
                or not isinstance(self.track_epoch, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", self.track_epoch)):
            raise ValueError("分析輸入版本或視角識別不正確。")
        if (self.scene_reference_jpeg is None) != (not self.scene_reference_labels):
            raise ValueError("場景參考拼貼與標記清單必須同時存在。")
        if self.scene_reference_jpeg is not None and (
                not isinstance(self.scene_reference_jpeg, bytes) or not 1 <= len(self.scene_reference_jpeg) <= 512 * 1024
                or not isinstance(self.scene_reference_labels, tuple) or not 1 <= len(self.scene_reference_labels) <= 8
                or any(not isinstance(label, str) or not 1 <= len(label) <= 160 or "\n" in label or "\r" in label
                       for label in self.scene_reference_labels)):
            raise ValueError("場景參考拼貼超出安全範圍或標記格式不正確。")


@dataclass(frozen=True)
class TagEvidence:
    track_id: str
    code: str
    frame_indices: tuple[int, ...]
    offsets_seconds: tuple[float, ...]
    certainty: str
    zone_ref: str | None = None
    view_ref: str | None = None

    def __post_init__(self):
        if (not isinstance(self.track_id, str) or not 1 <= len(self.track_id) <= 128
                or self.code not in TAG_LABELS or self.certainty not in {"low", "medium", "high", "unknown"}
                or not 2 <= len(self.frame_indices) <= 8
                or self.frame_indices != tuple(sorted(set(self.frame_indices)))
                or any(type(index) is not int or not 0 <= index < 8 for index in self.frame_indices)
                or len(self.offsets_seconds) != len(self.frame_indices)
                or any(type(value) not in {int, float} or not math.isfinite(value) or not -4 <= value <= 2
                       for value in self.offsets_seconds)):
            raise ValueError("候選動作證據格式不正確。")
        if self.code in SPATIAL_TAGS:
            if (not isinstance(self.zone_ref,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}",self.zone_ref)
                    or not isinstance(self.view_ref,str) or not re.fullmatch(r"[0-9a-f-]{36}",self.view_ref)):
                raise ValueError("位置標籤缺少可信視角與區域。")
        elif self.zone_ref is not None or self.view_ref is not None:
            raise ValueError("非位置標籤不可夾帶位置欄位。")

    def payload(self):
        return {"track_id": self.track_id, "code": self.code,
                "dimension": TAG_DIMENSIONS[self.code], "taxonomy_version": TAXONOMY_VERSION,
                "evidence_frame_indices": list(self.frame_indices),
                "observed_offsets_seconds": list(self.offsets_seconds),
                "certainty": self.certainty, "time_quality": "coarse",
                "time_origin": "event_trigger", "status": "ai_candidate",
                **({"zone_ref":self.zone_ref,"view_ref":self.view_ref} if self.zone_ref else {})}


@dataclass(frozen=True)
class SemanticResult:
    status: str
    tracks: tuple[tuple[str, tuple[str, ...]], ...] = ()
    job_id: str | None = None
    timings: tuple[tuple[str, float | str], ...] = ()
    evidence: tuple[TagEvidence, ...] = ()
    request_dispatched: bool | None = None
    http_status: int | None = None
    provenance: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (len(dict(self.provenance)) != len(self.provenance)
                or any(key not in {"provider_id", "model_id", "taxonomy_version", "input_mode"}
                       or not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", value)
                       for key, value in self.provenance)):
            raise ValueError("分析來源摘要不正確。")
        if self.request_dispatched is not None and type(self.request_dispatched) is not bool:
            raise ValueError("分析請求證據不正確。")
        if self.http_status is not None and (type(self.http_status) is not int or not 100 <= self.http_status <= 599):
            raise ValueError("分析HTTP證據不正確。")
        if self.status not in {"completed", "failed", "timeout", "cancelled", "queue_full", "invalid_image"} | FAILURE_CATEGORIES:
            raise ValueError("視覺分析狀態不正確。")
        if len(self.tracks) > 20 or any(len(tags) > 6 or any(tag not in TAG_LABELS for tag in tags)
                                       for _track, tags in self.tracks):
            raise ValueError("視覺分析 TAG 不正確。")
        if len(self.evidence) > 120 or any(not isinstance(item, TagEvidence)
                                          or item.code not in dict(self.tracks).get(item.track_id, ())
                                          for item in self.evidence):
            raise ValueError("候選動作證據未綁定本次標籤。")
        if self.job_id is not None and str(UUID(self.job_id)) != self.job_id:
            raise ValueError("視覺分析工作識別不正確。")
        seen = set()
        for key, value in self.timings:
            if key in seen or self.job_id is None:
                raise ValueError("視覺分析時間紀錄重複或缺少工作識別。")
            seen.add(key)
            if key in {"queue_ms", "preparation_ms", "provider_call_ms", "api_response_ms", "validation_ms"}:
                if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                    raise ValueError("視覺分析耗時不正確。")
            elif key in {"queued_at", "started_at", "prepared_at", "provider_started_at", "http_started_at", "response_received_at", "finished_at"}:
                if not isinstance(value, str) or datetime.fromisoformat(value).utcoffset() != timezone.utc.utcoffset(None):
                    raise ValueError("視覺分析時間必須使用 UTC。")
            else:
                raise ValueError("視覺分析含未知時間欄位。")

    def payload(self) -> dict[str, Any]:
        payload = {"status": self.status, "tracks": [
            {"track_id": track, "tags": list(tags)} for track, tags in self.tracks
        ], "experimental": True}
        payload["taxonomy_version"] = TAXONOMY_VERSION
        payload["tag_dimensions"] = {tag:TAG_DIMENSIONS[tag] for _track,tags in self.tracks for tag in tags}
        if self.job_id is not None:
            payload["job_id"] = self.job_id
            payload["timings"] = dict(self.timings)
        if self.evidence:
            payload["tag_evidence"] = [item.payload() for item in self.evidence]
        if self.provenance:
            payload["analysis_source"] = dict(self.provenance)
        if self.request_dispatched is not None or self.http_status is not None:
            payload["transport"] = {"request_dispatched": self.request_dispatched, "http_status": self.http_status}
        return payload


class VisionProvider(Protocol):
    provider_id: str
    model_id: str
    capabilities: frozenset[str]
    def health_check(self, *, timeout_seconds: float = 2) -> Mapping[str, Any]: ...
    def analyze(self, request: SemanticRequest, *, timeout_seconds: float) -> Mapping[str, Any]: ...


class SemanticFuture(Future):
    def __init__(self, job_id=None):
        super().__init__()
        self.semantic_job_id = str(UUID(job_id)) if job_id else str(uuid4())


def normalize_result(raw: Mapping[str, Any], request: SemanticRequest) -> SemanticResult:
    """只接受已知軌跡與 TAG；供應商自由文字、身分推測不進資料庫或 caption。"""
    if request.storyboards:
        return normalize_storyboard_result(raw, request)
    rows = raw.get("tracks")
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValueError("視覺分析結果格式不正確。")
    allowed = set(request.track_ids) if request.track_ids else {"scene"}
    tracks: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("視覺分析軌跡格式不正確。")
        track = row.get("track_id")
        tags = row.get("tags")
        if track not in allowed or track in seen or not isinstance(tags, list) or len(tags) > 6:
            raise ValueError("視覺分析軌跡或 TAG 不正確。")
        if any(not isinstance(tag, str) or tag not in TAG_LABELS for tag in tags):
            raise ValueError("視覺分析含未知 TAG。")
        if any(tag in SPATIAL_TAGS for tag in tags) or ("unknown" in tags and len(tags) > 1):
            raise ValueError("缺少有效視角對位，或未知標籤與動作衝突。")
        tracks.append((track, tuple(dict.fromkeys(tags))))
        seen.add(track)
    return SemanticResult("completed", tuple(tracks))


def normalize_storyboard_result(raw: Mapping[str, Any], request: SemanticRequest) -> SemanticResult:
    boards = {board.alias: board for board in request.storyboards}
    if (len(boards) != len(request.storyboards)
            or {board.track_id for board in boards.values()} != set(request.track_ids)):
        raise ValueError("分析輸入軌跡映射不完整。")
    rows = raw.get("tracks")
    if not isinstance(rows, list) or len(rows) != len(boards):
        raise ValueError("分析結果缺少本次軌跡。")
    parsed = {}
    evidence = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("track_alias") not in boards:
            raise ValueError("分析結果含未知軌跡。")
        alias = row["track_alias"]
        board = boards[alias]
        tags = row.get("tags")
        if alias in parsed or not isinstance(tags, list) or len(tags) > 6:
            raise ValueError("分析軌跡或標籤重複。")
        codes = []
        for tag in tags:
            if not isinstance(tag, Mapping) or set(tag) != {"code", "certainty", "evidence_frame_indices"}:
                raise ValueError("候選動作缺少可追溯證據。")
            code, certainty, indices = tag["code"], tag["certainty"], tag["evidence_frame_indices"]
            if (code not in TAG_LABELS or code in codes or certainty not in {"low", "medium", "high", "unknown"}
                    or not isinstance(indices, list) or not 2 <= len(indices) <= len(board.offsets_seconds)
                    or any(type(index) is not int or not 0 <= index < len(board.offsets_seconds) for index in indices)
                    or indices != sorted(set(indices))):
                raise ValueError("候選動作的影格證據不正確。")
            codes.append(code)
            zone_ref = view_ref = None
            if code in SPATIAL_TAGS:
                required = {"near_food":{"food"},"near_water":{"water"},"in_litter_area":{"litter_box","litter"}}[code]
                common = None
                for index in indices:
                    valid = {(view,zone) for frame,view,zone,kind in board.zones if frame == index and kind in required}
                    common = valid if common is None else common & valid
                if not common or len(common) != 1:
                    raise ValueError("此位置標籤沒有涵蓋所有觀察點的一致區域對位。")
                view_ref,zone_ref = next(iter(common))
            evidence.append(TagEvidence(board.track_id, code, tuple(indices),
                                        tuple(board.offsets_seconds[index] for index in indices), certainty,zone_ref,view_ref))
        if "unknown" in codes and len(codes) > 1:
            raise ValueError("無法判斷不可與其他候選動作並列。")
        parsed[alias] = tuple(codes)
    return SemanticResult("completed", tuple((board.track_id, parsed[board.alias])
                                               for board in request.storyboards), evidence=tuple(evidence))


class EventSemanticService:
    """單一 worker、有界佇列、HTTP timeout 契約；預設不連接外部 API。"""

    def __init__(self, provider: VisionProvider, *, max_pending: int = 8,
                 request_timeout_seconds: float = 20,
                 result_sink: Callable[[str, SemanticResult], None] | None = None,
                 input_preparer: Callable[[SemanticRequest], SemanticRequest] | None = None,
                 journal=None, job_ttl_seconds: float = 60, dispatch_validator=None) -> None:
        if not 1 <= max_pending <= 32 or not 0 < request_timeout_seconds <= 30:
            raise ValueError("視覺分析佇列或逾時設定不正確。")
        self.provider = provider
        self.timeout = float(request_timeout_seconds)
        self.result_sink = result_sink
        self.input_preparer = input_preparer
        self.failed_saves = 0
        if not 1 <= job_ttl_seconds <= 300:
            raise ValueError("分析工作有效期限只允許1至300秒。")
        self.journal, self.job_ttl = journal, job_ttl_seconds
        self.dispatch_validator = dispatch_validator
        self.pressure_probe = None
        self._jobs_lock = threading.Lock()
        self._live_jobs = {}
        self.pressure_reason = None
        self._last_recovery = 0.0
        self._queue: Queue[tuple[SemanticRequest, Future[SemanticResult], str, float, str]] = Queue(max_pending)
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="meowkeeper-event-vision", daemon=True)
        self._worker.start()

    def submit(self, request: SemanticRequest, *, job_id=None) -> Future[SemanticResult]:
        future = SemanticFuture(job_id)
        if self.journal is not None:
            try:
                if not self.journal.register(request, future.semantic_job_id, self.provider, self.job_ttl):
                    future.set_result(SemanticResult("duplicate_request", job_id=future.semantic_job_id, request_dispatched=False))
                    return future
            except Exception:  # noqa: BLE001 - 帳本不可用時不冒險重複外送
                future.set_result(SemanticResult("failed", job_id=future.semantic_job_id, request_dispatched=False))
                return future
        # 一張已存在的 JPEG，不另建攝影機 consumer 或解碼器。
        if self._stop.is_set():
            future.set_result(SemanticResult("cancelled", job_id=future.semantic_job_id, request_dispatched=False))
        elif self.provider is None:
            future.set_result(SemanticResult("skipped_disabled", job_id=future.semantic_job_id, request_dispatched=False))
        elif not request.jpeg or len(request.jpeg) > 4 * 1024 * 1024:
            future.set_result(SemanticResult("invalid_image", job_id=future.semantic_job_id, request_dispatched=False))
        else:
            try:
                with self._jobs_lock:
                    self._live_jobs[future.semantic_job_id] = (request, future)
                self._queue.put_nowait((request, future, future.semantic_job_id, time.monotonic(),
                                       datetime.now(timezone.utc).isoformat()))
            except Full:
                future.set_result(SemanticResult("queue_full", job_id=future.semantic_job_id, request_dispatched=False))
        if future.done():
            self._persist(request.event_id, future.result())
            with self._jobs_lock:
                self._live_jobs.pop(future.semantic_job_id,None)
        return future

    def _cancelled(self, job_id):
        if self.journal is None:
            return False
        try:
            return self.journal.cancelled(job_id)
        except Exception:  # noqa: BLE001 - 無法核對取消狀態時不外送、不採用候選
            return True

    def cancel(self, event_id, job_id):
        if self.journal is None:
            raise ValueError("目前沒有可取消的分析帳本。")
        active = self.journal.request_cancel(event_id,job_id)
        result = None
        with self._jobs_lock:
            entry = self._live_jobs.get(job_id)
            if active and entry and not entry[1].running() and not entry[1].done():
                result = SemanticResult("cancelled",job_id=job_id,request_dispatched=False,provenance=self._provenance(entry[0]))
                entry[1].set_result(result)
        if result is not None:
            self._persist(event_id,result)
        return {"status":"cancel_requested" if active else "already_finished",
                "message_zh_tw":"已請求取消；尚未送出的工作會略過。已外送的請求無法撤回或保證免收費，回應會丟棄，最遲依原逾時結束。"}

    def reanalyze(self, event_id, request_id):
        if self.journal is None or self.provider is None or self._stop.is_set():
            raise ValueError("目前尚未啟用可選分析，請先確認服務與外送同意。")
        job_id, request = self.journal.prepare_manual(event_id, request_id)
        if request is not None:
            self.submit(request, job_id=job_id)
        return {"status":"accepted", "job_id":job_id,
                "message_zh_tw":"已登記手動分析；會再檢查同意、每日預算與服務狀態，不會重傳Telegram影片。請稍後重新開啟事件查看結果。"}

    def _persist(self, event_id, result):
        try:
            if self.journal is not None:
                self.journal.finish(result.job_id, result)
            if self.result_sink is not None:
                self.result_sink(event_id, result)
        except Exception:  # noqa: BLE001 - 結果已留帳本，背景可補投影
            self.failed_saves += 1

    def close(self, *, wait: bool = True) -> None:
        self._stop.set()
        if wait and threading.current_thread() is not self._worker:
            self._worker.join(timeout=1)

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            if self.journal is not None and time.monotonic()-self._last_recovery >= 1:
                self._last_recovery = time.monotonic()
                try:
                    self.journal.recover(self.result_sink or self.journal.store.record_event_semantics)
                except Exception:  # noqa: BLE001 - 有界重試，不重送API
                    self.failed_saves += 1
            try:
                request, future, job_id, queued_mono, queued_utc = self._queue.get(timeout=.1)
            except Empty:
                continue
            with self._jobs_lock:
                ready = not future.done() and future.set_running_or_notify_cancel()
            if not ready:
                if future.cancelled():
                    self._persist(request.event_id,SemanticResult("cancelled",job_id=job_id,request_dispatched=False,provenance=self._provenance(request)))
                with self._jobs_lock:
                    self._live_jobs.pop(job_id,None)
                self._queue.task_done()
                continue
            started = time.monotonic()
            started_utc = datetime.now(timezone.utc).isoformat()
            timings: list[tuple[str, float | str]] = [
                ("queued_at", queued_utc), ("started_at", started_utc),
                ("queue_ms", max(0, (started - queued_mono) * 1000)),
            ]
            dispatched, http_status = False, None
            try:
                if (self._stop.is_set() or time.monotonic()-queued_mono >= self.job_ttl
                        or self.journal is not None and not self.journal.start(job_id)):
                    result = SemanticResult("cancelled")
                    dispatched = False
                    if self.journal:
                        self.journal.reason(job_id,"service_stopped" if self._stop.is_set() else "ttl_expired")
                else:
                    if self._cancelled(job_id):
                        raise VisionFailure("insufficient_evidence")
                    self.pressure_reason = self.pressure_probe() if self.pressure_probe else None
                    if self.pressure_reason:
                        if self.journal:
                            self.journal.reason(job_id,self.pressure_reason)
                        raise VisionFailure("skipped_pressure")
                    if self.input_preparer is not None and not request.storyboards:
                        preparation_started = time.monotonic()
                        request = self.input_preparer(request)
                        timings.append(("preparation_ms", (time.monotonic() - preparation_started) * 1000))
                        timings.append(("prepared_at", datetime.now(timezone.utc).isoformat()))
                        started = time.monotonic()
                    if self.dispatch_validator is not None and not self.dispatch_validator(request):
                        raise VisionFailure("insufficient_evidence")
                    if self.journal is not None:
                        self.journal.save_input(job_id, request)
                    remaining = self.job_ttl-(time.monotonic()-queued_mono)
                    if remaining <= 0 or self._stop.is_set():
                        raise TimeoutError
                    if self._cancelled(job_id):
                        raise VisionFailure("insufficient_evidence")
                    self.pressure_reason = self.pressure_probe() if self.pressure_probe else None
                    if self.pressure_reason:
                        if self.journal:
                            self.journal.reason(job_id,self.pressure_reason)
                        raise VisionFailure("skipped_pressure")
                    if self.dispatch_validator is not None and not self.dispatch_validator(request):
                        raise VisionFailure("insufficient_evidence")
                    started = time.monotonic()
                    timings.append(("provider_started_at",datetime.now(timezone.utc).isoformat()))
                    dispatched = None  # The adapter must prove dispatch; merely calling it is not proof.
                    raw = self.provider.analyze(request, timeout_seconds=min(self.timeout, remaining))
                    if isinstance(raw, ProviderReply):
                        dispatched, http_status = raw.request_dispatched, raw.http_status
                    responded = time.monotonic()
                    timings.append(("provider_call_ms", (responded - started) * 1000))
                    if isinstance(raw, ProviderReply) and raw.api_elapsed_ms is not None:
                        timings.append(("api_response_ms", raw.api_elapsed_ms))
                        timings.extend(raw.http_timings)
                    result = (SemanticResult("timeout") if time.monotonic() - started > self.timeout or time.monotonic()-queued_mono > self.job_ttl
                              else normalize_result(raw, request))
                    if self.dispatch_validator is not None and not self.dispatch_validator(request):
                        result = SemanticResult("insufficient_evidence")
                    timings.append(("validation_ms", max(0, (time.monotonic() - responded) * 1000)))
            except VisionFailure as error:
                result = SemanticResult(error.category)
                dispatched, http_status = error.request_dispatched, error.http_status
                if http_status is not None and error.api_elapsed_ms is not None:
                    timings.append(("api_response_ms", error.api_elapsed_ms))
                    timings.extend(error.http_timings)
            except TimeoutError:
                result = SemanticResult("timeout")
            except Exception:  # noqa: BLE001 - API 或解析失敗不可中斷事件通知
                result = SemanticResult("failed")
            if self._cancelled(job_id):
                result = SemanticResult("cancelled")
            timings.append(("finished_at", datetime.now(timezone.utc).isoformat()))
            result = SemanticResult(result.status, result.tracks, job_id, tuple(timings), result.evidence,
                                    dispatched, http_status, self._provenance(request))
            # 先完成 future，使通知不等待 SQLite sink 的忙碌狀態。
            future.set_result(result)
            self._persist(request.event_id, result)
            with self._jobs_lock:
                self._live_jobs.pop(job_id,None)
            self._queue.task_done()

    def _provenance(self, request=None):
        from meowkeeper.storage.semantic_jobs import provider_identity
        provider, model = provider_identity(self.provider)
        return (("provider_id", provider), ("model_id", model), ("taxonomy_version", TAXONOMY_VERSION),
                ("input_mode", "storyboard" if request and request.storyboards else "representative" if request else "unknown"))
