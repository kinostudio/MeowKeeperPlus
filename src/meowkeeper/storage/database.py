"""V0.2 SQLite 事件儲存；只在狀態變更或批次邊界寫入。"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from datetime import time as time_of_day
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 29
DEFAULT_BUSY_TIMEOUT_MS = 5000
DEFAULT_WRITE_RETRIES = 3

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS semantic_work_jobs (
    job_id TEXT PRIMARY KEY,
    job_key TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    state TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    deadline_at REAL NOT NULL,
    result_json TEXT,
    input_json TEXT,
    projected INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_semantic_work_recovery ON semantic_work_jobs(state,deadline_at,projected);
CREATE TABLE IF NOT EXISTS semantic_reanalysis_requests (
    request_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    job_id TEXT NOT NULL UNIQUE,
    input_revision INTEGER NOT NULL,
    requested_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS telegram_media_track_refs (
    media_event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    recipient_ref TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    behavior_event_id TEXT NOT NULL REFERENCES behavior_events(event_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY(media_event_id,recipient_ref,message_id,behavior_event_id),
    UNIQUE(media_event_id,recipient_ref,message_id,ordinal)
);
CREATE TABLE IF NOT EXISTS web_semantic_reviews (
    request_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    reviewer_ref TEXT NOT NULL,
    revision INTEGER NOT NULL,
    analysis_revision INTEGER NOT NULL,
    track_id TEXT NOT NULL,
    tag_code TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('accepted','rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id,reviewer_ref,revision)
);
CREATE TRIGGER IF NOT EXISTS web_semantic_review_no_update BEFORE UPDATE ON web_semantic_reviews
BEGIN SELECT RAISE(ABORT,'semantic reviews are append-only'); END;
CREATE TABLE IF NOT EXISTS web_event_validity_reviews (
    request_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    reviewer_ref TEXT NOT NULL,
    revision INTEGER NOT NULL,
    validity TEXT NOT NULL CHECK(validity IN ('valid','invalid','unclear')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id,reviewer_ref,revision)
);
CREATE TRIGGER IF NOT EXISTS validity_review_no_update BEFORE UPDATE ON web_event_validity_reviews
BEGIN SELECT RAISE(ABORT,'validity reviews are append-only'); END;
CREATE TABLE IF NOT EXISTS source_view_states (
    source_ref TEXT PRIMARY KEY,
    epoch INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'legacy_fixed_view_unverified',
    active_view TEXT,
    reason TEXT NOT NULL DEFAULT 'legacy'
);
CREATE TABLE IF NOT EXISTS scene_views (
    view_id TEXT PRIMARY KEY,
    source_ref TEXT NOT NULL,
    name TEXT NOT NULL,
    position_json TEXT,
    reference_jpeg BLOB NOT NULL,
    zones_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS scene_objects (
    object_id TEXT PRIMARY KEY,
    view_id TEXT NOT NULL REFERENCES scene_views(view_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    polygon_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    reference_jpeg BLOB,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS scene_object_history (
    object_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    value_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(object_id,revision)
);
CREATE TRIGGER IF NOT EXISTS scene_object_history_no_update BEFORE UPDATE ON scene_object_history
BEGIN SELECT RAISE(ABORT,'scene object history is append-only'); END;
CREATE TABLE IF NOT EXISTS event_delivery_traces (
    trace_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    trace_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_delivery_event ON event_delivery_traces(event_id,created_at);
CREATE TRIGGER IF NOT EXISTS delivery_trace_no_update BEFORE UPDATE ON event_delivery_traces
BEGIN SELECT RAISE(ABORT,'delivery traces are append-only'); END;
CREATE TABLE IF NOT EXISTS vision_request_jobs (
    job_key TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    provider_ref TEXT NOT NULL,
    utc_day TEXT NOT NULL,
    state TEXT NOT NULL,
    outcome TEXT,
    started_at REAL NOT NULL,
    lease_until REAL NOT NULL,
    finished_at REAL,
    quota_day TEXT,
    model_id TEXT,
    cooldown_ref TEXT,
    request_dispatched INTEGER,
    http_status INTEGER,
    api_elapsed_ms REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS idx_vision_budget ON vision_request_jobs(provider_ref,utc_day);
CREATE INDEX IF NOT EXISTS idx_vision_active ON vision_request_jobs(state,lease_until,provider_ref);
CREATE INDEX IF NOT EXISTS idx_vision_retention ON vision_request_jobs(started_at);
CREATE INDEX IF NOT EXISTS idx_vision_window ON vision_request_jobs(provider_ref,started_at);
CREATE TABLE IF NOT EXISTS vision_request_decisions (
    decision_key TEXT PRIMARY KEY,
    provider_ref TEXT NOT NULL,
    model_id TEXT NOT NULL,
    category TEXT NOT NULL,
    quota_day TEXT NOT NULL,
    occurred_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vision_decisions_budget
ON vision_request_decisions(provider_ref,quota_day,category);
CREATE INDEX IF NOT EXISTS idx_vision_decisions_window
ON vision_request_decisions(occurred_at,provider_ref);
CREATE TABLE IF NOT EXISTS vision_token_probes (
    probe_id TEXT PRIMARY KEY,
    provider_ref TEXT NOT NULL,
    model_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    finished_at REAL,
    outcome TEXT,
    request_dispatched INTEGER,
    http_status INTEGER,
    total_tokens INTEGER,
    elapsed_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_vision_token_probes_window
ON vision_token_probes(provider_ref,created_at);
CREATE TABLE IF NOT EXISTS vision_provider_circuits (
    provider_ref TEXT PRIMARY KEY,
    failures INTEGER NOT NULL DEFAULT 0,
    open_until REAL NOT NULL DEFAULT 0,
    needs_key_fix INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS event_semantic_revisions (
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    job_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(event_id,revision)
);
CREATE INDEX IF NOT EXISTS idx_semantic_revision_status_time
ON event_semantic_revisions(status,created_at);
CREATE TRIGGER IF NOT EXISTS semantic_revision_no_update
BEFORE UPDATE ON event_semantic_revisions
BEGIN SELECT RAISE(ABORT,'semantic revisions are append-only'); END;
CREATE TABLE IF NOT EXISTS event_semantic_candidates (
    event_id TEXT PRIMARY KEY REFERENCES media_events(event_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS telegram_track_tag_drafts (
    review_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES behavior_events(event_id) ON DELETE CASCADE,
    recipient_ref TEXT NOT NULL,
    reviewer_ref TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    selected_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT 'editing',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS telegram_track_tag_history (
    update_id INTEGER PRIMARY KEY,
    callback_ref TEXT NOT NULL UNIQUE,
    review_id TEXT NOT NULL REFERENCES telegram_track_tag_drafts(review_id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS identity_inference_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES behavior_events(event_id) ON DELETE CASCADE,
    track_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS media_event_tracks (
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    track_id TEXT NOT NULL,
    PRIMARY KEY(event_id,track_id)
);
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS telegram_pet_reviews (
    review_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    recipient_ref TEXT NOT NULL,
    reviewer_ref TEXT NOT NULL,
    origin_update_id INTEGER NOT NULL UNIQUE,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS telegram_pet_review_receipts (
    review_id TEXT NOT NULL REFERENCES telegram_pet_reviews(review_id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    PRIMARY KEY(review_id,message_id)
);
CREATE TABLE IF NOT EXISTS telegram_pet_review_history (
    update_id INTEGER PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES telegram_pet_reviews(review_id) ON DELETE CASCADE,
    pet_id TEXT NOT NULL,
    behavior TEXT NOT NULL,
    motion TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS identity_feedback_evidence (
    event_id TEXT PRIMARY KEY REFERENCES behavior_events(event_id) ON DELETE CASCADE,
    crop_sha256 TEXT NOT NULL,
    crop_jpeg BLOB NOT NULL,
    captured_at TEXT NOT NULL,
    feature_json TEXT,
    feature_model_version TEXT,
    learning_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pets (
    pet_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pet_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pet_id TEXT NOT NULL REFERENCES pets(pet_id) ON DELETE CASCADE,
    model_version TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    color_vector_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    pet_id TEXT,
    source_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    last_confidence REAL,
    reset_reason TEXT
);
CREATE TABLE IF NOT EXISTS behavior_events (
    event_id TEXT PRIMARY KEY,
    pet_id TEXT,
    track_id TEXT NOT NULL,
    behavior_state TEXT NOT NULL,
    motion_state TEXT NOT NULL,
    event_tags_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    raw_confidence REAL NOT NULL,
    smoothed_confidence REAL,
    inference_method TEXT NOT NULL,
    model_version TEXT NOT NULL,
    rule_profile_version TEXT NOT NULL,
    source_id TEXT NOT NULL,
    camera_preset TEXT,
    evidence_json TEXT NOT NULL,
    human_review INTEGER NOT NULL DEFAULT 0,
    human_label TEXT,
    identity_status TEXT NOT NULL DEFAULT 'unknown',
    identity_confidence REAL NOT NULL DEFAULT 0.0,
    identity_resolved_at TEXT,
    identity_model_version TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS event_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES behavior_events(event_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    is_correct INTEGER,
    created_at TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS behavior_event_telegram_receipts (
    event_id TEXT NOT NULL REFERENCES behavior_events(event_id) ON DELETE CASCADE,
    recipient_ref TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id, recipient_ref, message_id)
);
CREATE TABLE IF NOT EXISTS behavior_event_annotations (
    event_id TEXT NOT NULL REFERENCES behavior_events(event_id) ON DELETE CASCADE,
    reviewer_ref TEXT NOT NULL,
    dimension TEXT NOT NULL CHECK (dimension IN ('cat_presence','identity','behavior','motion')),
    label TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id, reviewer_ref, dimension)
);
CREATE TABLE IF NOT EXISTS behavior_event_annotation_history (
    update_id INTEGER PRIMARY KEY,
    callback_ref TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL REFERENCES behavior_events(event_id) ON DELETE CASCADE,
    reviewer_ref TEXT NOT NULL,
    dimension TEXT NOT NULL CHECK (dimension IN ('cat_presence','identity','behavior','motion')),
    label TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS zones (
    zone_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    zone_type TEXT NOT NULL,
    polygon_json TEXT NOT NULL,
    enabled INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS source_zones (
    source_ref TEXT NOT NULL,
    zone_id TEXT NOT NULL,
    name TEXT NOT NULL,
    zone_type TEXT NOT NULL,
    polygon_json TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    PRIMARY KEY (source_ref, zone_id)
);
CREATE TABLE IF NOT EXISTS model_registry (
    model_version TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    backend TEXT NOT NULL,
    source_url TEXT,
    sha256 TEXT,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identity_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT,
    track_id TEXT NOT NULL,
    predicted_pet_id TEXT,
    corrected_pet_id TEXT,
    confidence REAL,
    created_at TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS media_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    trigger_at TEXT NOT NULL,
    clip_start_at TEXT,
    clip_end_at TEXT,
    requested_pre_seconds REAL NOT NULL,
    requested_post_seconds REAL NOT NULL,
    actual_duration_seconds REAL,
    source_id TEXT NOT NULL,
    track_id TEXT,
    pet_id TEXT NOT NULL DEFAULT 'unknown',
    confidence REAL NOT NULL DEFAULT 0.0,
    clip_path TEXT,
    clip_size_bytes INTEGER,
    clip_sha256 TEXT,
    clip_status TEXT NOT NULL DEFAULT 'pending',
    telegram_status TEXT NOT NULL DEFAULT 'disabled',
    telegram_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_behavior_pet_started ON behavior_events(pet_id, started_at);
CREATE INDEX IF NOT EXISTS idx_behavior_state_started ON behavior_events(behavior_state, started_at);
CREATE INDEX IF NOT EXISTS idx_media_trigger_at ON media_events(trigger_at);
CREATE INDEX IF NOT EXISTS idx_media_events_type_trigger_at ON media_events(event_type, trigger_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_clip_status ON media_events(clip_status);
CREATE TABLE IF NOT EXISTS telegram_receipts (
    event_id TEXT NOT NULL REFERENCES media_events(event_id),
    recipient_ref TEXT NOT NULL,
    media_kind TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id, recipient_ref, media_kind, message_id)
);
CREATE TABLE IF NOT EXISTS media_event_feedback (
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    reviewer_ref TEXT NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('cat', 'not_cat')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id, reviewer_ref)
);
CREATE TABLE IF NOT EXISTS media_event_feedback_history (
    update_id INTEGER PRIMARY KEY,
    callback_ref TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    reviewer_ref TEXT NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('cat', 'not_cat')),
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS media_event_annotations (
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    reviewer_ref TEXT NOT NULL,
    dimension TEXT NOT NULL CHECK (dimension IN ('identity','behavior')),
    label TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id, reviewer_ref, dimension)
);
CREATE TABLE IF NOT EXISTS media_event_annotation_history (
    update_id INTEGER PRIMARY KEY,
    callback_ref TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    reviewer_ref TEXT NOT NULL,
    dimension TEXT NOT NULL CHECK (dimension IN ('identity','behavior')),
    label TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS telegram_update_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    next_offset INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS telegram_restart_requests (
    request_id TEXT PRIMARY KEY,
    requester_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('awaiting_confirmation','restarting','completed','cancelled','failed','expired')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telegram_restart_status ON telegram_restart_requests(status, created_at);
CREATE TABLE IF NOT EXISTS monitoring_batches (
    batch_id TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    monitoring_seconds REAL NOT NULL CHECK(monitoring_seconds >= 0),
    analyzed_seconds REAL NOT NULL CHECK(analyzed_seconds >= 0 AND analyzed_seconds <= monitoring_seconds),
    cat_visible_seconds REAL NOT NULL CHECK(cat_visible_seconds >= 0 AND cat_visible_seconds <= analyzed_seconds)
);
CREATE INDEX IF NOT EXISTS idx_monitoring_batches_day ON monitoring_batches(day);
CREATE TABLE IF NOT EXISTS web_media_reviews (
    request_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES media_events(event_id) ON DELETE CASCADE,
    reviewer_ref TEXT NOT NULL,
    revision INTEGER NOT NULL,
    label TEXT NOT NULL CHECK(label IN ('cat','not_cat')),
    pets_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id,reviewer_ref,revision)
);
CREATE INDEX IF NOT EXISTS idx_web_reviews_event ON web_media_reviews(event_id,created_at);
"""


def _iso(value: datetime | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


class SQLiteStore:
    """小型本地資料庫；不提供逐影格寫入 API。

    24H 模式使用 WAL、busy timeout 與有限重試。migration 以可重複的
    ``_ensure_columns`` 加上 schema version 記錄完成，不會因重新啟動而重建資料。
    """

    def __init__(
        self,
        path: str | Path = "data/meowkeeper.sqlite3",
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        write_retries: int = DEFAULT_WRITE_RETRIES,
    ) -> None:
        if busy_timeout_ms < 0:
            raise ValueError("SQLite busy timeout 不可小於零。")
        if write_retries < 0:
            raise ValueError("SQLite 重試次數不可小於零。")
        self.path = Path(path)
        self._is_memory = str(self.path) == ":memory:"
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.write_retries = int(write_retries)
        self._connection_lock = threading.RLock()
        if not self._is_memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            str(self.path),
            timeout=max(0.0, self.busy_timeout_ms / 1000.0),
            check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        self._configure_connection()
        self.initialize()

    @property
    def schema_version(self) -> int:
        """目前資料庫 schema 版本；可供健康狀態與報告讀取。"""

        with self._connection_lock:
            row = self.connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def record_event_semantics(self, event_id: str, result: Any) -> None:
        """追加 AI 版本；舊候選表僅是相容讀取投影，不是歷史來源。"""
        from meowkeeper.core.event_semantics import SemanticResult

        if not isinstance(result, SemanticResult):
            raise TypeError("事件 AI 候選格式不正確。")
        job_id = result.job_id or str(uuid4())
        serialized = json.dumps(result.payload(), ensure_ascii=False, sort_keys=True)

        def operation() -> None:
            self.connection.execute("BEGIN IMMEDIATE")
            previous = self.connection.execute(
                "SELECT event_id,candidate_json FROM event_semantic_revisions WHERE job_id=?", (job_id,),
            ).fetchone()
            if previous:
                if previous["event_id"] != event_id or previous["candidate_json"] != serialized:
                    raise ValueError("分析工作識別已用於不同結果。")
                return
            revision = self.connection.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM event_semantic_revisions WHERE event_id=?",
                (event_id,),
            ).fetchone()[0]
            self.connection.execute(
                "INSERT INTO event_semantic_revisions(event_id,revision,job_id,status,candidate_json) VALUES(?,?,?,?,?)",
                (event_id, revision, job_id, result.status, serialized),
            )
            self.connection.execute(
                "INSERT INTO event_semantic_candidates(event_id,status,candidate_json) VALUES(?,?,?) "
                "ON CONFLICT(event_id) DO UPDATE SET status=excluded.status,candidate_json=excluded.candidate_json,updated_at=CURRENT_TIMESTAMP",
                (event_id, result.status, serialized),
            )
        def guarded_operation() -> None:
            try:
                operation()
            except Exception:
                self.connection.rollback()
                raise
        self._transaction(guarded_operation)

    def event_semantic_history(self, event_id: str) -> list[dict[str, Any]]:
        with self._connection_lock:
            rows = self.connection.execute(
                "SELECT revision,created_at,candidate_json FROM event_semantic_revisions "
                "WHERE event_id=? ORDER BY revision", (event_id,),
            ).fetchall()
        return [{"revision": row["revision"], "created_at": row["created_at"],
                 "result": json.loads(row["candidate_json"])} for row in rows]

    def event_semantics(self, event_id: str) -> dict[str, Any]:
        with self._connection_lock:
            row = self.connection.execute(
                "SELECT candidate_json FROM event_semantic_candidates WHERE event_id=?", (event_id,),
            ).fetchone()
        return json.loads(row[0]) if row else {"status": "not_requested", "tracks": [], "experimental": True}

    def record_telegram_receipt(
        self, event_id: str, recipient_ref: str, media_kind: str, message_id: int,
    ) -> None:
        """Only store an opaque recipient reference and a confirmed message ID."""
        if len(recipient_ref) != 64 or any(c not in "0123456789abcdef" for c in recipient_ref):
            raise ValueError("通知對象必須使用 SHA-256 參照。")
        if media_kind not in {"photo", "video", "text"} or type(message_id) is not int or message_id <= 0:
            raise ValueError("Telegram 收件回條格式不正確。")
        self._write(
            "INSERT OR IGNORE INTO telegram_receipts(event_id, recipient_ref, media_kind, message_id) VALUES (?, ?, ?, ?)",
            (event_id, recipient_ref, media_kind, message_id),
        )

    def telegram_receipts(self, event_id: str) -> list[sqlite3.Row]:
        with self._connection_lock:
            return list(self.connection.execute(
                "SELECT recipient_ref, media_kind, message_id, received_at FROM telegram_receipts WHERE event_id = ? ORDER BY received_at, message_id",
                (event_id,),
            ))

    def dashboard_activity(self, *, now: datetime) -> dict[str, Any]:
        """Bounded seven-local-day by hour aggregates; delivery stays separate."""
        local_now = now.astimezone()
        end_day = local_now.date() + timedelta(days=1)
        start_day = end_day - timedelta(days=7)
        start = int(datetime.combine(start_day, time_of_day.min).astimezone().timestamp())
        end = int(datetime.combine(end_day, time_of_day.min).astimezone().timestamp())
        with self._connection_lock:
            rows = self.connection.execute(
                "SELECT strftime('%Y-%m-%d',trigger_at,'localtime') AS day, "
                "CAST(strftime('%H',trigger_at,'localtime') AS INTEGER) AS hour, "
                "COUNT(*) AS count FROM media_events WHERE event_type='cat_detected' "
                "AND trigger_origin='detector' AND CAST(strftime('%s',trigger_at) AS INTEGER)>=? "
                "AND CAST(strftime('%s',trigger_at) AS INTEGER)<? GROUP BY day,hour",
                (start, end),
            ).fetchall()
            notifications = self.connection.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(EXISTS(SELECT 1 FROM telegram_receipts r WHERE r.event_id=e.event_id)),0) AS confirmed, "
                "COALESCE(SUM(telegram_status='failed'),0) AS failed, "
                "COALESCE(SUM(telegram_status='pending'),0) AS pending, "
                "COALESCE(SUM(telegram_status IN ('disabled','muted')),0) AS suppressed, "
                "MAX((SELECT MAX(received_at) FROM telegram_receipts r WHERE r.event_id=e.event_id)) AS last_confirmed_at "
                "FROM media_events e WHERE CAST(strftime('%s',trigger_at) AS INTEGER)>=? "
                "AND CAST(strftime('%s',trigger_at) AS INTEGER)<?",
                (start, end),
            ).fetchone()
        counts = {(str(row["day"]), int(row["hour"])): int(row["count"]) for row in rows}
        days = []
        for offset in range(7):
            day = (start_day + timedelta(days=offset)).isoformat()
            hours = [counts.get((day, hour), 0) for hour in range(24)]
            days.append({"date": day, "hours": hours, "total": sum(hours)})
        return {"window_start": start, "window_end": end, "days": days,
                "notifications": dict(notifications),
                "message_zh_tw": "最近 7 個完整日，依這台電腦的本地時間分成 24 個時段；色塊只計真實模型貓咪事件。通知送達僅以 Telegram 回條確認，按事件計數，不是收件人成功率。"}

    def has_event_review(self, event_id: str) -> bool:
        with self._connection_lock:
            return bool(self.connection.execute(
                "SELECT EXISTS(SELECT 1 FROM web_media_reviews WHERE event_id=?) OR "
                "EXISTS(SELECT 1 FROM media_event_feedback_history WHERE event_id=?) OR "
                "EXISTS(SELECT 1 FROM telegram_pet_review_history h "
                "JOIN telegram_pet_reviews r ON r.review_id=h.review_id WHERE r.event_id=?)",
                (event_id, event_id, event_id),
            ).fetchone()[0])

    @staticmethod
    def _validate_opaque_ref(value: str) -> None:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("回饋識別必須使用 SHA-256 參照。")

    def telegram_update_offset(self) -> int | None:
        with self._connection_lock:
            row = self.connection.execute(
                "SELECT next_offset FROM telegram_update_state WHERE id=1"
            ).fetchone()
        return int(row[0]) if row is not None else None

    def commit_telegram_update(self, update_id: int) -> None:
        if type(update_id) is not int or update_id < 0:
            raise ValueError("Telegram update 識別格式不正確。")
        self._write(
            "INSERT INTO telegram_update_state(id,next_offset) VALUES (1,?) "
            "ON CONFLICT(id) DO UPDATE SET next_offset=MAX(next_offset,excluded.next_offset)",
            (update_id + 1,),
        )

    def create_telegram_restart_confirmation(
        self, *, request_id: str, requester_ref: str, created_at: str,
    ) -> bool:
        """Create one short-lived reset confirmation without storing a Chat ID."""
        from uuid import UUID

        self._validate_opaque_ref(requester_ref)
        normalized_id = str(UUID(request_id))

        def operation() -> bool:
            active = self.connection.execute(
                "SELECT 1 FROM telegram_restart_requests WHERE status IN ('awaiting_confirmation','restarting') LIMIT 1"
            ).fetchone()
            if active is not None:
                return False
            self.connection.execute(
                "INSERT INTO telegram_restart_requests(request_id,requester_ref,status,created_at,updated_at) VALUES (?,?, 'awaiting_confirmation', ?, ?)",
                (normalized_id, requester_ref, created_at, created_at),
            )
            return True

        return bool(self._transaction(operation))

    def confirm_telegram_restart(self, *, request_id: str, requester_ref: str, confirmed_at: str) -> bool:
        from uuid import UUID

        self._validate_opaque_ref(requester_ref)
        normalized_id = str(UUID(request_id))

        def operation() -> bool:
            cursor = self.connection.execute(
                """UPDATE telegram_restart_requests SET status='restarting',updated_at=?
                   WHERE request_id=? AND requester_ref=? AND status='awaiting_confirmation'
                     AND julianday(created_at) >= julianday(?) - (15.0 / 1440.0)""",
                (confirmed_at, normalized_id, requester_ref, confirmed_at),
            )
            if cursor.rowcount:
                return True
            self.connection.execute(
                "UPDATE telegram_restart_requests SET status='expired',updated_at=? WHERE request_id=? AND status='awaiting_confirmation'",
                (confirmed_at, normalized_id),
            )
            return False

        return bool(self._transaction(operation))

    def confirm_telegram_restart_and_commit_update(
        self, *, request_id: str, requester_ref: str, confirmed_at: str, update_id: int,
    ) -> bool:
        """Atomically consume the Telegram confirmation update and accept reset."""
        from uuid import UUID

        self._validate_opaque_ref(requester_ref)
        if type(update_id) is not int or update_id < 0:
            raise ValueError("Telegram update 識別格式不正確。")
        normalized_id = str(UUID(request_id))

        def operation() -> bool:
            cursor = self.connection.execute(
                """UPDATE telegram_restart_requests SET status='restarting',updated_at=?
                   WHERE request_id=? AND requester_ref=? AND status='awaiting_confirmation'
                     AND julianday(created_at) >= julianday(?) - (15.0 / 1440.0)""",
                (confirmed_at, normalized_id, requester_ref, confirmed_at),
            )
            if cursor.rowcount:
                self.connection.execute(
                    "INSERT INTO telegram_update_state(id,next_offset) VALUES (1,?) "
                    "ON CONFLICT(id) DO UPDATE SET next_offset=MAX(next_offset,excluded.next_offset)",
                    (update_id + 1,),
                )
                return True
            self.connection.execute(
                "UPDATE telegram_restart_requests SET status='expired',updated_at=? WHERE request_id=? AND status='awaiting_confirmation'",
                (confirmed_at, normalized_id),
            )
            return False

        return bool(self._transaction(operation))

    def cancel_telegram_restart(self, *, request_id: str, requester_ref: str, cancelled_at: str) -> bool:
        from uuid import UUID

        self._validate_opaque_ref(requester_ref)
        cursor = self._write(
            "UPDATE telegram_restart_requests SET status='cancelled',updated_at=? WHERE request_id=? AND requester_ref=? AND status='awaiting_confirmation'",
            (cancelled_at, str(UUID(request_id)), requester_ref),
        )
        return bool(cursor.rowcount)

    def pending_telegram_restart_requests(self, *, limit: int = 8) -> list[sqlite3.Row]:
        if not 1 <= int(limit) <= 32:
            raise ValueError("待回報的 Telegram 重啟請求上限不正確。")
        with self._connection_lock:
            return list(self.connection.execute(
                "SELECT request_id,requester_ref,status,created_at,updated_at FROM telegram_restart_requests WHERE status='restarting' ORDER BY updated_at LIMIT ?",
                (int(limit),),
            ).fetchall())

    def finish_telegram_restart(self, *, request_id: str, status: str, updated_at: str) -> None:
        from uuid import UUID

        if status not in {"completed", "failed"}:
            raise ValueError("Telegram 重啟結果狀態不正確。")
        self._write(
            "UPDATE telegram_restart_requests SET status=?,updated_at=? WHERE request_id=? AND status='restarting'",
            (status, updated_at, str(UUID(request_id))),
        )

    def record_media_event_feedback(
        self, *, event_id: str, recipient_ref: str, message_id: int,
        reviewer_ref: str, label: str, update_id: int, callback_ref: str,
    ) -> str:
        """Atomically persist a receipt-bound label, dedupe keys, and poll offset."""
        for value in (recipient_ref, reviewer_ref, callback_ref):
            self._validate_opaque_ref(value)
        if label not in {"cat", "not_cat"}:
            raise ValueError("貓咪回饋標籤不正確。")
        if type(message_id) is not int or message_id <= 0 or type(update_id) is not int or update_id < 0:
            raise ValueError("Telegram 回饋識別格式不正確。")

        def operation() -> str:
            event = self.connection.execute(
                "SELECT event_type,trigger_origin FROM media_events WHERE event_id=?", (event_id,),
            ).fetchone()
            if event is None or event["event_type"] != "cat_detected" or event["trigger_origin"] != "detector":
                raise ValueError("此事件不是可供校正的模型貓咪告警。")
            receipt = self.connection.execute(
                "SELECT 1 FROM telegram_receipts WHERE event_id=? AND recipient_ref=? AND message_id=? AND media_kind IN ('photo','video')",
                (event_id, recipient_ref, message_id),
            ).fetchone()
            if receipt is None:
                raise ValueError("Telegram 回饋與已送達的事件媒體不符。")
            duplicate = self.connection.execute(
                "SELECT 1 FROM media_event_feedback_history WHERE update_id=? OR callback_ref=?",
                (update_id, callback_ref),
            ).fetchone()
            if duplicate:
                self.connection.execute(
                    "INSERT INTO telegram_update_state(id,next_offset) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET next_offset=MAX(next_offset,excluded.next_offset)",
                    (update_id + 1,),
                )
                return "duplicate"
            previous = self.connection.execute(
                "SELECT label FROM media_event_feedback WHERE event_id=? AND reviewer_ref=?",
                (event_id, reviewer_ref),
            ).fetchone()
            self.connection.execute(
                "INSERT INTO media_event_feedback_history(update_id,callback_ref,event_id,reviewer_ref,label) VALUES (?,?,?,?,?)",
                (update_id, callback_ref, event_id, reviewer_ref, label),
            )
            self.connection.execute(
                """INSERT INTO media_event_feedback(event_id,reviewer_ref,label,updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(event_id,reviewer_ref) DO UPDATE SET label=excluded.label,updated_at=excluded.updated_at""",
                (event_id, reviewer_ref, label),
            )
            if label == "not_cat":
                # Preserve immutable annotation history, but current identity/
                # behavior labels cannot remain active on a rejected cat alert.
                self.connection.execute(
                    "DELETE FROM media_event_annotations WHERE event_id=? AND reviewer_ref=?",
                    (event_id, reviewer_ref),
                )
            labels = {row[0] for row in self.connection.execute(
                "SELECT DISTINCT label FROM media_event_feedback WHERE event_id=?", (event_id,),
            )}
            self.connection.execute(
                "INSERT INTO telegram_update_state(id,next_offset) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET next_offset=MAX(next_offset,excluded.next_offset)",
                (update_id + 1,),
            )
            return "disputed" if len(labels) > 1 else ("updated" if previous else "recorded")

        return str(self._transaction(operation))

    def record_media_event_annotation(
        self, *, event_id: str, recipient_ref: str, reviewer_ref: str,
        dimension: str, label: str, update_id: int, callback_ref: str,
    ) -> str:
        """Save a human identity/behavior label only for a delivered, confirmed-cat alert.

        These labels are review data, never automatic model training or a silent
        replacement for the original inference. Poll offset and label are atomic.
        """
        for value in (recipient_ref, reviewer_ref, callback_ref):
            self._validate_opaque_ref(value)
        if dimension not in {"identity", "behavior"} or not label or len(label) > 64:
            raise ValueError("身分或行為回饋格式不正確。")
        if type(update_id) is not int or update_id < 0:
            raise ValueError("Telegram 回饋識別格式不正確。")

        def operation() -> str:
            event = self.connection.execute(
                "SELECT event_type,trigger_origin FROM media_events WHERE event_id=?", (event_id,),
            ).fetchone()
            receipt = self.connection.execute(
                "SELECT 1 FROM telegram_receipts WHERE event_id=? AND recipient_ref=? AND media_kind IN ('photo','video') LIMIT 1",
                (event_id, recipient_ref),
            ).fetchone()
            confirmed = self.connection.execute(
                "SELECT 1 FROM media_event_feedback WHERE event_id=? AND reviewer_ref=? AND label='cat'",
                (event_id, reviewer_ref),
            ).fetchone()
            if (event is None or event["event_type"] != "cat_detected"
                    or event["trigger_origin"] != "detector" or receipt is None or confirmed is None):
                raise ValueError("請先確認此已送達的貓咪告警，才能標註身分或行為。")
            duplicate = self.connection.execute(
                "SELECT 1 FROM media_event_annotation_history WHERE update_id=? OR callback_ref=?",
                (update_id, callback_ref),
            ).fetchone()
            if duplicate is not None:
                result = "duplicate"
            else:
                previous = self.connection.execute(
                    "SELECT label FROM media_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension=?",
                    (event_id, reviewer_ref, dimension),
                ).fetchone()
                self.connection.execute(
                    "INSERT INTO media_event_annotation_history(update_id,callback_ref,event_id,reviewer_ref,dimension,label) VALUES (?,?,?,?,?,?)",
                    (update_id, callback_ref, event_id, reviewer_ref, dimension, label),
                )
                self.connection.execute(
                    """INSERT INTO media_event_annotations(event_id,reviewer_ref,dimension,label)
                       VALUES (?,?,?,?) ON CONFLICT(event_id,reviewer_ref,dimension)
                       DO UPDATE SET label=excluded.label,updated_at=CURRENT_TIMESTAMP""",
                    (event_id, reviewer_ref, dimension, label),
                )
                result = "updated" if previous is not None else "recorded"
            self.connection.execute(
                "INSERT INTO telegram_update_state(id,next_offset) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET next_offset=MAX(next_offset,excluded.next_offset)",
                (update_id + 1,),
            )
            return result

        return str(self._transaction(operation))

    def record_behavior_event_telegram_receipt(
        self, event_id: str, recipient_ref: str, message_id: int,
    ) -> None:
        """Bind a V0.2 track event to a delivered Telegram message."""
        self._validate_opaque_ref(recipient_ref)
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("Telegram 收件回條格式不正確。")
        self._write(
            "INSERT OR IGNORE INTO behavior_event_telegram_receipts(event_id,recipient_ref,message_id) VALUES (?,?,?)",
            (event_id, recipient_ref, message_id),
        )

    def record_delivery_trace(self, event_id: str, trace: dict) -> None:
        from meowkeeper.core.delivery_telemetry import validate_delivery_trace

        safe = validate_delivery_trace(trace)
        self._write("INSERT OR IGNORE INTO event_delivery_traces(trace_id,event_id,trace_json) VALUES(?,?,?)",
                    (safe["trace_id"], event_id, json.dumps(safe, ensure_ascii=False)))

    def event_delivery_history(self, event_id: str) -> list[dict]:
        with self._connection_lock:
            rows = self.connection.execute(
                "SELECT trace_json FROM event_delivery_traces WHERE event_id=? ORDER BY created_at DESC,rowid DESC LIMIT 100",
                (event_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def media_feedback_tracks(self, event_id: str) -> list[sqlite3.Row]:
        """Only unambiguous source/track/time matches may label a native video."""
        with self._connection_lock:
            rows = self.connection.execute(
                """SELECT b.event_id,b.track_id FROM behavior_events b
                   JOIN media_event_tracks m ON m.track_id=b.track_id
                   JOIN media_events e ON e.event_id=m.event_id
                   WHERE e.event_id=? AND b.source_id=e.source_id
                   AND julianday(b.started_at)<=julianday(e.trigger_at)+e.requested_post_seconds/86400.0
                   AND (b.ended_at IS NULL OR julianday(b.ended_at)>=julianday(e.trigger_at)-e.requested_pre_seconds/86400.0)
                   ORDER BY b.track_id,b.started_at,b.event_id""", (event_id,),
            ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["track_id"]] = counts.get(row["track_id"], 0) + 1
        return [row for row in rows if counts[row["track_id"]] == 1][:8]

    def bind_media_track_receipt(self, behavior_id: str, recipient_ref: str, message_id: int) -> bool:
        """Authorize a track entry against this recipient's delivered native media."""
        self._validate_opaque_ref(recipient_ref)
        if type(message_id) is not int or message_id <= 0:
            return False
        with self._connection_lock:
            media = self.connection.execute(
                "SELECT event_id,received_at FROM telegram_receipts WHERE recipient_ref=? AND message_id=? AND media_kind IN ('video','photo') AND julianday(received_at) BETWEEN julianday('now')-1 AND julianday('now')",
                (recipient_ref, message_id),
            ).fetchall()
            matched = False
            for item in media:
                snapshot = self.connection.execute("SELECT behavior_event_id FROM telegram_media_track_refs WHERE media_event_id=? AND recipient_ref=? AND message_id=?",(item["event_id"],recipient_ref,message_id)).fetchall()
                allowed = {row[0] for row in snapshot} if snapshot else {row["event_id"] for row in self.media_feedback_tracks(item["event_id"])}
                matched |= behavior_id in allowed
            if not matched:
                return False
            self._write(
                "INSERT OR IGNORE INTO behavior_event_telegram_receipts(event_id,recipient_ref,message_id,received_at) VALUES(?,?,?,?)",
                (behavior_id, recipient_ref, message_id, media[0]["received_at"]),
            )
            return True

    def has_behavior_event_telegram_receipt(
        self, event_id: str, recipient_ref: str, message_id: int,
    ) -> bool:
        self._validate_opaque_ref(recipient_ref)
        with self._connection_lock:
            row = self.connection.execute(
                "SELECT 1 FROM behavior_event_telegram_receipts WHERE event_id=? AND recipient_ref=? AND message_id=?",
                (event_id, recipient_ref, message_id),
            ).fetchone()
        return row is not None

    def record_behavior_event_annotation(
        self, *, event_id: str, recipient_ref: str, reviewer_ref: str,
        message_id: int, dimension: str, label: str, update_id: int, callback_ref: str,
        learning_consent: bool = False,
    ) -> str:
        """Record a receipt-bound label for one trajectory, with immutable history."""
        if type(learning_consent) is not bool:
            raise ValueError("學習同意必須是布林值。")
        for value in (recipient_ref, reviewer_ref, callback_ref):
            self._validate_opaque_ref(value)
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("Telegram 回覆訊息格式不正確。")
        if dimension not in {"cat_presence", "identity", "behavior", "motion"} or not label or len(label) > 64:
            raise ValueError("單一軌跡回饋格式不正確。")
        if dimension == "cat_presence" and label not in {"cat", "not_cat"}:
            raise ValueError("貓咪正誤標籤不正確。")
        if dimension == "behavior" and label not in {
            "sleeping", "resting", "eating", "drinking", "playing", "grooming",
            "litter_box", "other_active", "out_of_view", "unknown",
        }:
            raise ValueError("主要行為標籤不正確。")
        if dimension == "motion" and label not in {
            "stationary", "low_motion", "walking", "running", "jumping",
        }:
            raise ValueError("動作標籤不正確。")
        if type(update_id) is not int or update_id < 0:
            raise ValueError("Telegram 回饋識別格式不正確。")

        def operation() -> str:
            event = self.connection.execute(
                "SELECT 1 FROM behavior_events WHERE event_id=?", (event_id,),
            ).fetchone()
            receipt = self.connection.execute(
                "SELECT 1 FROM behavior_event_telegram_receipts WHERE event_id=? AND recipient_ref=? AND message_id=?",
                (event_id, recipient_ref, message_id),
            ).fetchone()
            if event is None or receipt is None:
                raise ValueError("回饋與已送達的單一軌跡通知不符。")
            if dimension in {"behavior", "motion"}:
                cat_label = self.connection.execute(
                    "SELECT label FROM behavior_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension='cat_presence'",
                    (event_id, reviewer_ref),
                ).fetchone()
                if cat_label is None or cat_label[0] != "cat":
                    raise ValueError("請先確認這條軌跡是貓咪，再標註身分或行為。")
            duplicate = self.connection.execute(
                "SELECT 1 FROM behavior_event_annotation_history WHERE update_id=? OR callback_ref=?",
                (update_id, callback_ref),
            ).fetchone()
            if duplicate is not None:
                result = "duplicate"
            else:
                if dimension == "identity":
                    # An explicit named/other-cat identity vote also confirms cat presence.
                    # The immutable identity history below records this single user action.
                    self.connection.execute(
                        """INSERT INTO behavior_event_annotations(event_id,reviewer_ref,dimension,label)
                           VALUES (?,?,'cat_presence','cat') ON CONFLICT(event_id,reviewer_ref,dimension)
                           DO UPDATE SET label='cat',updated_at=CURRENT_TIMESTAMP""",
                        (event_id, reviewer_ref),
                    )
                previous = self.connection.execute(
                    "SELECT label FROM behavior_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension=?",
                    (event_id, reviewer_ref, dimension),
                ).fetchone()
                self.connection.execute(
                    "INSERT INTO behavior_event_annotation_history(update_id,callback_ref,event_id,reviewer_ref,dimension,label,learning_consent) VALUES (?,?,?,?,?,?,?)",
                    (update_id, callback_ref, event_id, reviewer_ref, dimension, label, int(learning_consent)),
                )
                self.connection.execute(
                    """INSERT INTO behavior_event_annotations(event_id,reviewer_ref,dimension,label,learning_consent)
                       VALUES (?,?,?,?,?) ON CONFLICT(event_id,reviewer_ref,dimension)
                       DO UPDATE SET label=excluded.label,learning_consent=excluded.learning_consent,updated_at=CURRENT_TIMESTAMP""",
                    (event_id, reviewer_ref, dimension, label, int(learning_consent)),
                )
                if dimension == "cat_presence" and label == "not_cat":
                    self.connection.execute(
                        "DELETE FROM behavior_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension IN ('identity','behavior','motion')",
                        (event_id, reviewer_ref),
                    )
                result = "updated" if previous is not None else "recorded"
            self.connection.execute(
                "INSERT INTO telegram_update_state(id,next_offset) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET next_offset=MAX(next_offset,excluded.next_offset)",
                (update_id + 1,),
            )
            return result

        return str(self._transaction(operation))

    def cat_feedback_summary(self) -> dict[str, Any]:
        """Report reviewed alert precision; this is not recall or global accuracy."""
        with self._connection_lock:
            rows = self.connection.execute(
                """SELECT e.event_id,e.confidence,COUNT(f.reviewer_ref) AS reviewers,
                          COUNT(DISTINCT f.label) AS distinct_labels,MIN(f.label) AS label
                   FROM media_events e LEFT JOIN media_event_feedback f ON f.event_id=e.event_id
                   WHERE e.event_type='cat_detected' AND e.trigger_origin='detector'
                   GROUP BY e.event_id"""
            ).fetchall()
            annotation_rows = self.connection.execute(
                "SELECT dimension,COUNT(DISTINCT event_id) AS events FROM media_event_annotations GROUP BY dimension"
            ).fetchall()
        ranges = ((0.0, .5), (.5, .6), (.6, .7), (.7, .8), (.8, .9), (.9, 1.000001))
        buckets = [{
            "range": f"{lo:.2f}-{min(1, hi):.2f}", "reviewed": 0,
            "consensus": 0, "cat": 0, "not_cat": 0, "disputed": 0,
            "reviewed_alert_precision": None, "precision_wilson_95": None,
        }
                   for lo, hi in ranges]
        reviewed = disputed = cat = not_cat = unreviewed = 0
        for row in rows:
            if not row["reviewers"]:
                unreviewed += 1
                continue
            reviewed += 1
            index = next((i for i, (lo, hi) in enumerate(ranges) if lo <= float(row["confidence"]) < hi), len(ranges) - 1)
            bucket = buckets[index]
            bucket["reviewed"] += 1
            if row["distinct_labels"] > 1:
                disputed += 1
                bucket["disputed"] += 1
            elif row["label"] == "cat":
                cat += 1
                bucket["cat"] += 1
            else:
                not_cat += 1
                bucket["not_cat"] += 1
        consensus = cat + not_cat
        for bucket in buckets:
            bucket_consensus = bucket["cat"] + bucket["not_cat"]
            bucket["consensus"] = bucket_consensus
            if bucket_consensus:
                bucket["reviewed_alert_precision"] = bucket["cat"] / bucket_consensus
                bucket["precision_wilson_95"] = self._wilson_interval(
                    bucket["cat"], bucket_consensus,
                )
        return {
            "status": "unverified" if consensus == 0 else "sampled",
            "metric_name": "已回饋模型告警 precision（非整體準確率）",
            "model_score_is_calibrated_probability": False,
            "detector_alerts": len(rows), "reviewed_events": reviewed,
            "unreviewed_events": unreviewed, "disputed_events": disputed,
            "confirmed_cat": cat, "confirmed_not_cat": not_cat,
            "reviewed_alert_precision": cat / consensus if consensus else None,
            "reviewed_alert_precision_wilson_95": self._wilson_interval(cat, consensus),
            "confidence_score_buckets": buckets,
            "manual_annotation_events": {row["dimension"]: int(row["events"]) for row in annotation_rows},
            "limitation_zh_tw": "只回饋已告警事件會有選樣偏差，不能估算漏報率或整體準確率；模型分數不是正確機率。",
        }

    @staticmethod
    def _wilson_interval(successes: int, total: int) -> dict[str, float] | None:
        """Wilson 95% interval makes small feedback samples visibly uncertain."""
        if total <= 0:
            return None
        z = 1.959963984540054
        proportion = successes / total
        denominator = 1.0 + (z * z / total)
        center = (proportion + z * z / (2.0 * total)) / denominator
        margin = z * ((proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total)) ** 0.5) / denominator
        return {"lower": max(0.0, center - margin), "upper": min(1.0, center + margin)}

    @property
    def journal_mode(self) -> str:
        with self._connection_lock:
            row = self.connection.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).lower() if row is not None else "unknown"

    def _configure_connection(self) -> None:
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        if not self._is_memory:
            # WAL permits readers to continue while the 24H writer commits.
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = NORMAL")

    def initialize(self) -> None:
        has_meta = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
        ).fetchone()
        if has_meta:
            version = self.connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            if version is not None and int(version[0]) > SCHEMA_VERSION:
                raise ValueError("資料庫由較新版本建立；請使用相容版本，未進行降版或覆寫。")
        self._retry(self.connection.executescript, SCHEMA)
        self._ensure_columns(
            "media_events",
            {
                "trigger_origin": "TEXT NOT NULL DEFAULT 'unknown'",
                "model_version": "TEXT",
                "model_sha256": "TEXT",
                "confidence_threshold": "REAL",
            },
        )
        self._ensure_columns(
            "behavior_events",
            {
                "identity_status": "TEXT NOT NULL DEFAULT 'unknown'",
                "identity_confidence": "REAL NOT NULL DEFAULT 0.0",
                "identity_resolved_at": "TEXT",
                "identity_model_version": "TEXT",
                # SQLite does not allow a non-constant default in ALTER TABLE;
                # existing rows can be populated on their next upsert.
                "updated_at": "TEXT",
            },
        )
        self._ensure_columns("identity_corrections", {"event_id": "TEXT"})
        self._ensure_columns("telegram_pet_review_history", {"motion": "TEXT"})
        self._ensure_columns("telegram_track_tag_drafts", {
            "identity_snapshot": "TEXT NOT NULL DEFAULT ''",
            "name_snapshot": "TEXT NOT NULL DEFAULT ''",
            "staged_identity": "INTEGER NOT NULL DEFAULT 0",
            "identity_baseline": "TEXT NOT NULL DEFAULT ''",
            "origin_update_id": "INTEGER",
            "origin_message_id": "INTEGER",
            "callback_revision": "INTEGER NOT NULL DEFAULT 0",
            "navigation_update_id": "INTEGER",
            "prompt_media_kind": "TEXT NOT NULL DEFAULT 'text'",
        })
        self._ensure_columns("telegram_track_tag_history", {"callback_revision": "INTEGER"})
        self._ensure_columns("semantic_work_jobs", {"input_json":"TEXT", "cancel_requested":"INTEGER NOT NULL DEFAULT 0", "terminal_reason":"TEXT"})
        self._ensure_columns("scene_objects", {"logical_object_id":"TEXT"})
        self._ensure_columns("vision_request_jobs", {
            "quota_day": "TEXT", "model_id": "TEXT", "request_dispatched": "INTEGER",
            "http_status": "INTEGER", "api_elapsed_ms": "REAL", "input_tokens": "INTEGER",
            "output_tokens": "INTEGER", "estimated_tokens": "INTEGER", "cooldown_ref": "TEXT",
        })
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_vision_cooldown ON vision_request_jobs(provider_ref,cooldown_ref,started_at)"
        )
        self.connection.execute("UPDATE vision_request_jobs SET quota_day=utc_day WHERE quota_day IS NULL")
        self.connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_track_review_origin_update ON telegram_track_tag_drafts(origin_update_id) WHERE origin_update_id IS NOT NULL")
        self.connection.execute("UPDATE telegram_track_tag_drafts SET state='superseded' "
                                "WHERE identity_snapshot='' AND staged_identity=0 AND state IN ('editing','confirming')")
        self._commit_with_retry()
        self._ensure_behavior_annotation_motion_dimension()
        self._ensure_columns("behavior_event_annotations", {"learning_consent": "INTEGER NOT NULL DEFAULT 0"})
        self._ensure_columns("behavior_event_annotation_history", {"learning_consent": "INTEGER NOT NULL DEFAULT 0"})
        self._ensure_columns("web_media_reviews", {"scope_json": "TEXT NOT NULL DEFAULT '{}'"})
        # 在舊行為表的 transaction migration 完成後才寫入候選歷史。
        # 舊版只有最新候選，不能補造已遺失的歷史或耗時；只保留這一版。
        self.connection.execute(
            "INSERT OR IGNORE INTO event_semantic_revisions(event_id,revision,job_id,status,candidate_json,created_at) "
            "SELECT c.event_id,1,'legacy:'||c.event_id,c.status,c.candidate_json,c.updated_at "
            "FROM event_semantic_candidates c WHERE NOT EXISTS "
            "(SELECT 1 FROM event_semantic_revisions r WHERE r.event_id=c.event_id)"
        )
        self._retry(
            self.connection.execute,
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._retry(self.connection.execute, f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._commit_with_retry()

    def _ensure_behavior_annotation_motion_dimension(self) -> None:
        """Expand the annotation CHECK constraint without losing existing labels."""
        tables = {
            "behavior_event_annotations": """
                CREATE TABLE behavior_event_annotations_v11 (
                    event_id TEXT NOT NULL REFERENCES behavior_events(event_id) ON DELETE CASCADE,
                    reviewer_ref TEXT NOT NULL,
                    dimension TEXT NOT NULL CHECK (dimension IN ('cat_presence','identity','behavior','motion')),
                    label TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (event_id, reviewer_ref, dimension)
                )
            """,
            "behavior_event_annotation_history": """
                CREATE TABLE behavior_event_annotation_history_v11 (
                    update_id INTEGER PRIMARY KEY,
                    callback_ref TEXT NOT NULL UNIQUE,
                    event_id TEXT NOT NULL REFERENCES behavior_events(event_id) ON DELETE CASCADE,
                    reviewer_ref TEXT NOT NULL,
                    dimension TEXT NOT NULL CHECK (dimension IN ('cat_presence','identity','behavior','motion')),
                    label TEXT NOT NULL,
                    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """,
        }
        with self._connection_lock:
            needs_migration = []
            for table, create_sql in tables.items():
                row = self.connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if row is not None and "'motion'" not in str(row[0]):
                    needs_migration.append((table, create_sql))
            if not needs_migration:
                return

            self.connection.execute("PRAGMA foreign_keys = OFF")
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                for table, create_sql in needs_migration:
                    temporary = f"{table}_v11"
                    self.connection.execute(create_sql)
                    self.connection.execute(
                        f"INSERT INTO {temporary} SELECT * FROM {table}"
                    )
                    self.connection.execute(f"DROP TABLE {table}")
                    self.connection.execute(
                        f"ALTER TABLE {temporary} RENAME TO {table}"
                    )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            finally:
                self.connection.execute("PRAGMA foreign_keys = ON")

    def _retry(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        """Retry only transient SQLite lock/busy errors with a bounded delay."""

        for attempt in range(self.write_retries + 1):
            try:
                return operation(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "locked" not in message and "busy" not in message:
                    raise
                try:
                    self.connection.rollback()
                except sqlite3.Error:
                    pass
                if attempt >= self.write_retries:
                    raise
                time.sleep(min(0.25, 0.05 * (2**attempt)))
        raise AssertionError("SQLite retry loop unexpectedly exited")

    def _commit_with_retry(self) -> None:
        self._retry(self.connection.commit)

    def _write(self, sql: str, parameters: Iterable[Any] = ()) -> sqlite3.Cursor:
        values = tuple(parameters)

        def operation() -> sqlite3.Cursor:
            with self._connection_lock:
                cursor = self.connection.execute(sql, values)
                self.connection.commit()
                return cursor

        # Retry the complete transaction. Retrying commit alone after a lock
        # would have rolled back the INSERT/UPDATE and silently lose the write.
        return self._retry(operation)

    def _transaction(self, operation: Any) -> Any:
        def run() -> Any:
            with self._connection_lock:
                result = operation()
                self.connection.commit()
                return result

        return self._retry(run)

    def _ensure_columns(self, table: str, columns: Mapping[str, str]) -> None:
        existing = {
            str(row[1])
            for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                self.connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SQLiteStore:  # noqa: PYI034 - Python 3.10 has no typing.Self
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def register_pet(self, profile: Any) -> None:
        profile_json = json.dumps(profile.to_dict(), ensure_ascii=False, sort_keys=True)
        self._write(
            "INSERT OR REPLACE INTO pets(pet_id, display_name, profile_json, created_at) VALUES (?, ?, ?, ?)",
            (profile.pet_id, profile.display_name, profile_json, profile.created_at_utc),
        )

    def save_embedding(
        self,
        pet_id: str,
        model_version: str,
        vector: Iterable[float],
        color_vector: Iterable[float] | None = None,
    ) -> None:
        self._write(
            "INSERT INTO pet_embeddings(pet_id, model_version, vector_json, color_vector_json, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
            (
                pet_id,
                model_version,
                json.dumps([float(item) for item in vector]),
                json.dumps([float(item) for item in color_vector]) if color_vector is not None else None,
            ),
        )

    def save_track(
        self,
        track_id: str | int,
        *,
        source_id: str,
        started_at: datetime | str,
        pet_id: str | None = None,
        ended_at: datetime | str | None = None,
        confidence: float | None = None,
        reset_reason: str | None = None,
    ) -> None:
        self._write(
            "INSERT INTO tracks(track_id, pet_id, source_id, started_at, ended_at, last_confidence, reset_reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(track_id), pet_id, source_id, _iso(started_at), _iso(ended_at), confidence, reset_reason),
        )

    def update_track_identity(
        self,
        track_id: str | int,
        *,
        pet_id: str | None,
        confidence: float,
    ) -> int:
        cursor = self._write(
            """UPDATE tracks SET pet_id = ?, last_confidence = ?
               WHERE track_id = ? AND pet_id IS NULL""",
            (pet_id, max(0.0, min(1.0, float(confidence))), str(track_id)),
        )
        return int(cursor.rowcount)

    def save_behavior_event(self, event: Any) -> None:
        self._write(
            """INSERT INTO behavior_events(
                event_id, pet_id, track_id, behavior_state, motion_state, event_tags_json,
                started_at, ended_at, raw_confidence, smoothed_confidence, inference_method,
                model_version, rule_profile_version, source_id, camera_preset, evidence_json,
                human_review, human_label, identity_status, identity_confidence,
                identity_resolved_at, identity_model_version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                pet_id=excluded.pet_id, behavior_state=excluded.behavior_state,
                motion_state=excluded.motion_state, event_tags_json=excluded.event_tags_json,
                ended_at=excluded.ended_at, raw_confidence=excluded.raw_confidence,
                smoothed_confidence=excluded.smoothed_confidence, evidence_json=excluded.evidence_json,
                human_review=excluded.human_review, human_label=excluded.human_label,
                identity_status=excluded.identity_status, identity_confidence=excluded.identity_confidence,
                identity_resolved_at=excluded.identity_resolved_at,
                identity_model_version=excluded.identity_model_version, updated_at=excluded.updated_at
            WHERE behavior_events.human_review=0""",
            (
                event.event_id,
                event.pet_id,
                str(event.track_id),
                getattr(event.behavior_state, "value", event.behavior_state),
                getattr(event.motion_state, "value", event.motion_state),
                json.dumps([getattr(tag, "value", tag) for tag in event.event_tags]),
                _iso(event.started_at),
                _iso(event.ended_at),
                event.raw_confidence,
                event.smoothed_confidence,
                event.inference_method,
                event.model_version,
                event.rule_profile_version,
                event.source_id,
                event.camera_preset,
                json.dumps(getattr(event, "evidence", {}), ensure_ascii=False, sort_keys=True),
                int(bool(event.human_review)),
                event.human_label,
                getattr(event, "identity_status", "unknown"),
                float(getattr(event, "identity_confidence", 0.0)),
                _iso(getattr(event, "identity_resolved_at", None)),
                getattr(event, "identity_model_version", None),
                datetime.now().astimezone().isoformat(),
            ),
        )

    def update_behavior_event_identity(
        self,
        *,
        track_id: str | int,
        pet_id: str | None,
        confidence: float,
        model_version: str | None,
        resolved_at: datetime,
        note: str = "背景身分解析補寫",
    ) -> int:
        """Late-bind identity on unlocked events and preserve an audit row."""

        status = "resolved" if pet_id is not None else "unknown"
        def operation() -> int:
            rows = self.connection.execute(
                """SELECT event_id, pet_id FROM behavior_events
                   WHERE track_id = ? AND human_review = 0
                   ORDER BY started_at""",
                (str(track_id),),
            ).fetchall()
            if pet_id is None:
                for row in rows:
                    self.connection.execute(
                        """UPDATE behavior_events
                           SET identity_status = 'unknown', identity_confidence = ?,
                               identity_resolved_at = NULL, identity_model_version = ?, updated_at = ?
                           WHERE event_id = ?""",
                        (
                            max(0.0, min(1.0, float(confidence))),
                            model_version,
                            _iso(resolved_at),
                            row["event_id"],
                        ),
                    )
                return len(rows)
            for row in rows:
                self.connection.execute(
                    """UPDATE behavior_events
                       SET pet_id = ?, identity_status = ?, identity_confidence = ?,
                           identity_resolved_at = ?, identity_model_version = ?, updated_at = ?
                       WHERE event_id = ?""",
                    (
                        pet_id,
                        status,
                        max(0.0, min(1.0, float(confidence))),
                        _iso(resolved_at) if pet_id is not None else None,
                        model_version,
                        _iso(resolved_at),
                        row["event_id"],
                    ),
                )
                self.connection.execute(
                    """INSERT INTO identity_corrections(
                       event_id, track_id, predicted_pet_id, corrected_pet_id,
                       confidence, created_at, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["event_id"],
                        str(track_id),
                        row["pet_id"],
                        pet_id,
                        float(confidence),
                        _iso(resolved_at),
                        note,
                    ),
                )
            return len(rows)

        return int(self._transaction(operation))

    def add_event_label(
        self,
        event_id: str,
        label: str,
        *,
        is_correct: bool | None = None,
        note: str | None = None,
    ) -> None:
        self._write(
            "INSERT INTO event_labels(event_id, label, is_correct, created_at, note) VALUES (?, ?, ?, datetime('now'), ?)",
            (event_id, label, None if is_correct is None else int(is_correct), note),
        )

    def resolve_media_identity(self, *, track_id: str | int, pet_id: str | None,
                               confidence: float, resolved_at: datetime) -> int:
        """Fill unknown media identity only; never resend or replace known identity."""
        if not pet_id or pet_id == "unknown":
            return 0

        def operation() -> int:
            if self.connection.execute(
                "SELECT 1 FROM behavior_events WHERE track_id=? AND human_review=1 LIMIT 1",
                (str(track_id),),
            ).fetchone():
                return 0
            rows = self.connection.execute(
                "SELECT event_id FROM media_events WHERE track_id=? AND pet_id='unknown'",
                (str(track_id),),
            ).fetchall()
            for row in rows:
                self.connection.execute("UPDATE media_events SET pet_id=? WHERE event_id=?", (pet_id, row[0]))
                self.connection.execute(
                    """INSERT INTO identity_corrections(event_id,track_id,predicted_pet_id,
                       corrected_pet_id,confidence,created_at,note) VALUES(?,?,NULL,?,?,?,?)""",
                    (row[0], str(track_id), pet_id, max(0.0, min(1.0, confidence)),
                     _iso(resolved_at), "影片事件背景身分補寫"),
                )
            return len(rows)

        return int(self._transaction(operation))

    def record_identity_evidence(self, event_id: str, track_id: str, snapshot: dict) -> None:
        import json

        payload = json.dumps({"kind": "identity_evidence_v1", **snapshot}, ensure_ascii=False, allow_nan=False)
        with self._connection_lock:
            previous = self.connection.execute(
                "SELECT note FROM identity_inference_history WHERE event_id=? ORDER BY id DESC LIMIT 1",
                (event_id,),
            ).fetchone()
        if previous and previous[0] == payload:
            return
        self._write("INSERT INTO identity_inference_history(event_id,track_id,note) VALUES (?,?,?)",
                    (event_id, track_id, payload))
        self._write("""DELETE FROM identity_inference_history WHERE note LIKE '%identity_evidence_v1%'
                       AND (created_at < datetime('now','-30 days') OR id NOT IN
                       (SELECT id FROM identity_inference_history WHERE note LIKE '%identity_evidence_v1%'
                        ORDER BY id DESC LIMIT 2048))""")

    def identity_evidence(self, media_event_id: str | None = None) -> list[dict]:
        import json

        with self._connection_lock:
            if media_event_id is None:
                rows = self.connection.execute(
                    "SELECT event_id,track_id,created_at,note FROM identity_inference_history ORDER BY id DESC LIMIT 100"
                ).fetchall()
            else:
                rows = self.connection.execute(
                    """SELECT i.event_id,i.track_id,i.created_at,i.note FROM identity_inference_history i
                       JOIN media_event_tracks m ON m.track_id=i.track_id
                       JOIN media_events e ON e.event_id=m.event_id
                       JOIN behavior_events b ON b.event_id=i.event_id
                       WHERE m.event_id=? AND i.note LIKE '%identity_evidence_v1%'
                       AND julianday(b.started_at)<=julianday(e.trigger_at)+e.requested_post_seconds/86400.0
                       AND (b.ended_at IS NULL OR julianday(b.ended_at)>=julianday(e.trigger_at)-e.requested_pre_seconds/86400.0)
                       ORDER BY i.id DESC LIMIT 100""", (media_event_id,),
                ).fetchall()
        results, seen = [], set()
        for row in rows:
            if row["event_id"] in seen:
                continue
            try:
                data = json.loads(row["note"])
            except (ValueError, TypeError):
                continue
            if data.get("kind") != "identity_evidence_v1":
                continue
            seen.add(row["event_id"])
            results.append({**data, "behavior_event_id": row["event_id"],
                            "track_id": row["track_id"], "computed_at": row["created_at"]})
        return results

    def add_identity_correction(
        self,
        track_id: str | int,
        *,
        event_id: str | None = None,
        predicted_pet_id: str | None,
        corrected_pet_id: str | None,
        confidence: float | None = None,
        note: str | None = None,
    ) -> None:
        self._write(
            "INSERT INTO identity_corrections(event_id, track_id, predicted_pet_id, corrected_pet_id, confidence, created_at, note) VALUES (?, ?, ?, ?, ?, datetime('now'), ?)",
            (event_id, str(track_id), predicted_pet_id, corrected_pet_id, confidence, note),
        )

    def create_media_event(
        self,
        *,
        event_id: str,
        event_type: str,
        trigger_at: datetime | str,
        source_id: str,
        track_id: str | int | None,
        pet_id: str | None,
        confidence: float,
        requested_pre_seconds: float,
        requested_post_seconds: float,
        trigger_origin: str = "detector",
        model_version: str | None = None,
        model_sha256: str | None = None,
        confidence_threshold: float | None = None,
        track_ids: tuple[str, ...] = (),
    ) -> None:
        """建立事件影片 metadata；不保存逐影格資料。"""

        now = datetime.now().astimezone().isoformat()
        self._write(
            """INSERT OR IGNORE INTO media_events(
                event_id, event_type, trigger_at, source_id, track_id, pet_id,
                confidence, trigger_origin, model_version, model_sha256, confidence_threshold,
                requested_pre_seconds, requested_post_seconds,
                clip_status, telegram_status, created_at
            ) VALUES (?, ?, ?, ?, ?,
                COALESCE(NULLIF(?, 'unknown'),
                    (SELECT pet_id FROM tracks WHERE track_id=? AND pet_id IS NOT NULL ORDER BY id DESC LIMIT 1),
                    'unknown'), ?, ?, ?, ?, ?, ?, ?, 'pending', 'disabled', ?)""",
            (
                event_id,
                event_type,
                _iso(trigger_at),
                source_id,
                str(track_id) if track_id is not None else None,
                pet_id or "unknown",
                str(track_id) if track_id is not None else None,
                max(0.0, min(1.0, float(confidence))),
                trigger_origin,
                model_version,
                model_sha256,
                confidence_threshold,
                float(requested_pre_seconds),
                float(requested_post_seconds),
                now,
            ),
        )

        for reference in set(track_ids) | ({str(track_id)} if track_id is not None else set()):
            self._write("INSERT OR IGNORE INTO media_event_tracks(event_id,track_id) VALUES (?,?)",
                        (event_id, str(reference)))

    def update_media_event(self, event_id: str, **values: object) -> None:
        """更新組片／Telegram 狀態；欄位白名單避免任意 SQL。"""

        allowed = {
            "clip_start_at",
            "clip_end_at",
            "actual_duration_seconds",
            "clip_path",
            "clip_size_bytes",
            "clip_sha256",
            "clip_status",
            "telegram_status",
            "telegram_attempts",
            "last_error",
            "completed_at",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"不允許更新 media_events 欄位：{', '.join(sorted(unknown))}")
        if not values:
            return
        converted = {
            key: _iso(value) if key in {"clip_start_at", "clip_end_at", "completed_at"} and isinstance(value, datetime) else value
            for key, value in values.items()
        }
        assignments = ", ".join(f"{key} = ?" for key in converted)
        parameters = [*converted.values(), event_id]
        self._write(f"UPDATE media_events SET {assignments} WHERE event_id = ?", parameters)

    def get_media_event(self, event_id: str) -> sqlite3.Row | None:
        with self._connection_lock:
            return self.connection.execute(
                "SELECT * FROM media_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()

    def list_media_events(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        event_type: str | None = None,
        telegram_status: str | None = None,
    ) -> list[sqlite3.Row]:
        """以固定上限分頁讀取事件；Dashboard 不得一次載入全部歷史資料。"""

        if not 1 <= int(limit) <= 100:
            raise ValueError("事件分頁大小必須介於 1 到 100。")
        if int(offset) < 0:
            raise ValueError("事件分頁 offset 不可小於零。")
        query = """SELECT e.*,
                   CASE WHEN EXISTS(SELECT 1 FROM web_media_reviews w WHERE w.event_id=e.event_id)
                     OR EXISTS(SELECT 1 FROM media_event_feedback_history f WHERE f.event_id=e.event_id)
                     OR EXISTS(SELECT 1 FROM telegram_pet_reviews r
                               JOIN telegram_pet_review_history h ON h.review_id=r.review_id
                               WHERE r.event_id=e.event_id)
                   THEN 1 ELSE 0 END AS has_human_review
                   FROM media_events e"""
        parameters: list[object] = []
        filters: list[str] = []
        if event_type:
            filters.append("e.event_type = ?")
            parameters.append(event_type)
        if telegram_status:
            filters.append("e.telegram_status = ?")
            parameters.append(telegram_status)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY e.trigger_at DESC LIMIT ? OFFSET ?"
        parameters.extend((int(limit), int(offset)))
        with self._connection_lock:
            return list(self.connection.execute(query, parameters).fetchall())

    def save_zone(self, zone: Any) -> None:
        self._write(
            "INSERT OR REPLACE INTO zones(zone_id, name, zone_type, polygon_json, enabled) VALUES (?, ?, ?, ?, ?)",
            (
                zone.zone_id,
                zone.name,
                zone.zone_type,
                json.dumps(zone.polygon),
                int(bool(zone.enabled)),
            ),
        )

    @staticmethod
    def _validate_zone_source(source_ref: str) -> None:
        import re

        if not re.fullmatch(r"source-[0-9a-f]{24}", source_ref):
            raise ValueError("區域必須綁定來源雜湊，不接受原始來源網址。")

    def replace_source_zones(self, source_ref: str, zones: Iterable[Any]) -> None:
        """Atomically replace one source's zones; legacy unbound zones stay inert."""
        self._validate_zone_source(source_ref)
        items = tuple(zones)
        if len(items) > 64 or len({zone.zone_id for zone in items}) != len(items):
            raise ValueError("每個來源最多 64 個區域，區域 ID 不可重複。")
        for zone in items:
            zone.validate()

        def operation() -> None:
            self.connection.execute("DELETE FROM source_zones WHERE source_ref=?", (source_ref,))
            self.connection.executemany(
                "INSERT INTO source_zones VALUES (?, ?, ?, ?, ?, ?)",
                [(source_ref, zone.zone_id, zone.name, zone.zone_type,
                  json.dumps(zone.polygon), int(bool(zone.enabled))) for zone in items],
            )

        self._transaction(operation)

    def load_source_zones(self, source_ref: str) -> tuple[Any, ...]:
        from meowkeeper.core.behavior.models import ZoneDefinition

        self._validate_zone_source(source_ref)
        with self._connection_lock:
            rows = self.connection.execute(
                "SELECT * FROM source_zones WHERE source_ref=? ORDER BY zone_id", (source_ref,),
            ).fetchall()
        zones = tuple(ZoneDefinition(
            row["zone_id"], row["name"], row["zone_type"],
            tuple(tuple(point) for point in json.loads(row["polygon_json"])), bool(row["enabled"]),
        ) for row in rows)
        for zone in zones:
            zone.validate()
        return zones

    def save_model_registry(self, record: Mapping[str, Any]) -> None:
        self._write(
            """INSERT OR REPLACE INTO model_registry(
                model_version, model_name, backend, source_url, sha256, status, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                record["model_version"],
                record["model_name"],
                record.get("backend", "unknown"),
                record.get("source_url"),
                record.get("sha256"),
                record.get("status", "unknown"),
                json.dumps(dict(record), ensure_ascii=False, sort_keys=True),
            ),
        )

    def daily_summary(self, *, pet_id: str, day_prefix: str) -> list[sqlite3.Row]:
        cursor = self.connection.execute(
            """SELECT behavior_state, SUM((julianday(COALESCE(ended_at, started_at)) - julianday(started_at)) * 86400.0) AS seconds,
                      COUNT(*) AS event_count
               FROM behavior_events
               WHERE pet_id = ? AND started_at LIKE ?
               GROUP BY behavior_state ORDER BY behavior_state""",
            (pet_id, f"{day_prefix}%"),
        )
        return list(cursor.fetchall())

    def record_monitoring_batch(
        self, *, batch_id: str, day: str, monitoring_seconds: float,
        analyzed_seconds: float, cat_visible_seconds: float,
    ) -> None:
        """Idempotent batch totals; no URLs, identifiers or model secrets."""
        from uuid import UUID

        UUID(batch_id)
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("每日統計日期格式不正確。")
        values = (monitoring_seconds, analyzed_seconds, cat_visible_seconds)
        if not all(math.isfinite(value) for value in values) or not 0 <= cat_visible_seconds <= analyzed_seconds <= monitoring_seconds:
            raise ValueError("每日統計時間必須有限、非負，且貓咪時間不可超過分析時間。")
        self._write(
            "INSERT OR IGNORE INTO monitoring_batches VALUES(?,?,?,?,?)",
            (batch_id, day, *values),
        )

    def daily_monitoring_summary(self, *, day: str) -> dict[str, Any]:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("每日統計日期格式不正確。")
        with self._connection_lock:
            row = self.connection.execute(
                "SELECT COUNT(*) AS batches, SUM(monitoring_seconds) AS monitored, "
                "SUM(analyzed_seconds) AS analyzed, SUM(cat_visible_seconds) AS visible "
                "FROM monitoring_batches WHERE day=?", (day,),
            ).fetchone()
        available = int(row["batches"]) > 0
        analyzed = float(row["analyzed"] or 0.0)
        return {
            "date": day, "monitoring_seconds": float(row["monitored"]) if available else None,
            "analyzed_seconds": analyzed if available else None,
            "cat_active_seconds": float(row["visible"] or 0.0) if analyzed > 0 else None,
            "feeding_seconds": None, "drinking_seconds": None, "sleep_seconds": None,
            "metric_semantics": "consecutive_observed_frames_not_behavior_activity",
        }

    def daily_detector_alert_summary(self, *, start_at: str, end_at: str) -> dict[str, int]:
        """Count persisted cat alerts by provenance; simulated events stay separate."""
        with self._connection_lock:
            row = self.connection.execute(
                """SELECT COUNT(*) AS all_count,
                          SUM(CASE WHEN trigger_origin='detector' THEN 1 ELSE 0 END) AS detector_count,
                          SUM(CASE WHEN trigger_origin='simulated' THEN 1 ELSE 0 END) AS simulated_count
                   FROM media_events
                   WHERE event_type='cat_detected'
                     AND julianday(trigger_at) >= julianday(?)
                     AND julianday(trigger_at) < julianday(?)""",
                (start_at, end_at),
            ).fetchone()
        return {
            "all_count": int(row["all_count"] or 0),
            "detector_count": int(row["detector_count"] or 0),
            "simulated_count": int(row["simulated_count"] or 0),
        }

    def behavior_summary_rows(self, start: str, end: str) -> list[sqlite3.Row]:
        """Bounded, secret-free projection; dates are validated by analytics."""
        with self._connection_lock:
            return list(self.connection.execute(
                """SELECT e.pet_id, p.display_name, e.track_id, e.behavior_state,
                          e.started_at, e.ended_at
                   FROM behavior_events e LEFT JOIN pets p ON p.pet_id=e.pet_id
                   WHERE julianday(e.started_at) < julianday(?)
                     AND (e.ended_at IS NULL OR julianday(e.ended_at) > julianday(?))
                   ORDER BY e.started_at LIMIT 10001""", (end, start)
            ).fetchall())
