"""SQLite-backed per-model image-request quotas and safe usage history."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from meowkeeper.core.event_semantics import FAILURE_CATEGORIES, SemanticRequest, VisionFailure
from meowkeeper.storage.database import SQLiteStore

_VALID_RESET_ZONES = {"UTC", "America/Los_Angeles"}


def _us_pacific_tz_fallback(now: float) -> timezone:
    utc_dt = datetime.fromtimestamp(now, timezone.utc)
    year = utc_dt.year
    march_first = datetime(year, 3, 1, tzinfo=timezone.utc)
    second_sunday_march = 8 + (6 - march_first.weekday()) % 7
    dst_start_utc = datetime(year, 3, second_sunday_march, 10, 0, tzinfo=timezone.utc)
    nov_first = datetime(year, 11, 1, tzinfo=timezone.utc)
    first_sunday_nov = 1 + (6 - nov_first.weekday()) % 7
    dst_end_utc = datetime(year, 11, first_sunday_nov, 9, 0, tzinfo=timezone.utc)
    offset_hours = -7 if dst_start_utc <= utc_dt < dst_end_utc else -8
    return timezone(timedelta(hours=offset_hours), name="America/Los_Angeles")


class VisionRequestGuard:
    def __init__(self, store: SQLiteStore, *, clock=time.time):
        self.store = store
        self.clock = clock

    @staticmethod
    def job_key(provider_ref: str, request: SemanticRequest):
        manifest = [(board.alias, board.epoch, board.offsets_seconds, hashlib.sha256(board.jpeg).hexdigest())
                    for board in request.storyboards]
        reference_digest = (hashlib.sha256(request.scene_reference_jpeg).hexdigest()
                            if request.scene_reference_jpeg is not None else None)
        identity = [provider_ref, request.event_id, manifest, reference_digest, request.scene_reference_labels]
        if request.input_revision > 1:
            identity.append(["manual_revision", request.input_revision])
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def quota_day(now: float, reset_timezone: str) -> str:
        if reset_timezone not in _VALID_RESET_ZONES:
            raise ValueError("每日額度時區不支援。")
        try:
            zone = ZoneInfo(reset_timezone)
        except ZoneInfoNotFoundError:
            if reset_timezone == "America/Los_Angeles":
                zone = _us_pacific_tz_fallback(now)
            elif reset_timezone != "UTC":
                raise ValueError("此 Windows 執行環境缺少 Gemini 太平洋時間規則。") from None
            else:
                zone = timezone.utc
        return datetime.fromtimestamp(now, zone).date().isoformat()

    def _transaction(self, operation):
        def guarded():
            try:
                self.store.connection.execute("BEGIN IMMEDIATE")
                return operation()
            except Exception:
                self.store.connection.rollback()
                raise
        return self.store._transaction(guarded)

    def _estimate_from_history(self, provider_ref: str, now: float) -> int | None:
        with self.store._connection_lock:
            rows = self.store.connection.execute(
                "SELECT input_tokens+output_tokens AS total FROM vision_request_jobs "
                "WHERE provider_ref=? AND started_at>=? AND input_tokens IS NOT NULL "
                "AND output_tokens IS NOT NULL ORDER BY started_at DESC LIMIT 20",
                (provider_ref, now - 7 * 86400),
            ).fetchall()
        if not rows:
            return None
        return max(1, math.ceil(sum(int(row[0]) for row in rows) / len(rows)))

    def reserve(self, provider_ref: str, request: SemanticRequest, *, daily_limit: int,
                rpm_limit: int = 60, tpm_limit: int = 0, estimated_tokens: int | None = None,
                model_id: str | None = None, reset_timezone: str = "UTC", cooldown_seconds: int = 0,
                cooldown_ref: str | None = None):
        if type(daily_limit) is not int or not 1 <= daily_limit <= 10_000_000:
            raise ValueError("每日視覺請求上限不正確。")
        if type(rpm_limit) is not int or not 1 <= rpm_limit <= 10_000:
            raise ValueError("每分鐘請求上限不正確。")
        if type(tpm_limit) is not int or not 0 <= tpm_limit <= 100_000_000:
            raise ValueError("每分鐘 Token 上限不正確。")
        if estimated_tokens is not None and (type(estimated_tokens) is not int or not 0 <= estimated_tokens <= 100_000_000):
            raise ValueError("每次預估 Token 數不正確。")
        if model_id is not None and (not isinstance(model_id, str) or len(model_id) > 120):
            raise ValueError("圖片模型識別不正確。")
        if type(cooldown_seconds) is not int or not 0 <= cooldown_seconds <= 3600:
            raise ValueError("事件外送冷卻只允許 0～3600 秒。")
        if cooldown_ref is not None:
            self.store._validate_opaque_ref(cooldown_ref)
        self.store._validate_opaque_ref(provider_ref)
        key = self.job_key(provider_ref, request)
        now = self.clock()
        day = self.quota_day(now, reset_timezone)
        estimate = estimated_tokens if estimated_tokens else (self._estimate_from_history(provider_ref, now) if tpm_limit else None)

        def operation():
            connection = self.store.connection
            connection.execute("DELETE FROM vision_request_jobs WHERE started_at<?", (now - 90 * 86400,))
            connection.execute("DELETE FROM vision_request_decisions WHERE occurred_at<?", (now - 90 * 86400,))
            connection.execute("UPDATE vision_request_jobs SET state='finished',outcome='interrupted',finished_at=? "
                               "WHERE state='running' AND lease_until<=?", (now, now))

            def record_decision(category):
                decision_key = hashlib.sha256(json.dumps([key, category, day], separators=(",", ":")).encode()).hexdigest()
                connection.execute(
                    "INSERT OR IGNORE INTO vision_request_decisions"
                    "(decision_key,provider_ref,model_id,category,quota_day,occurred_at) VALUES(?,?,?,?,?,?)",
                    (decision_key, provider_ref, model_id or "", category, day, now),
                )

            if connection.execute("SELECT 1 FROM vision_request_jobs WHERE job_key=?", (key,)).fetchone():
                return "duplicate_request"
            if request.input_revision == 1 and connection.execute(
                    "SELECT 1 FROM vision_request_jobs WHERE provider_ref=? AND event_id=? LIMIT 1",
                    (provider_ref, request.event_id)).fetchone():
                return "duplicate_request"
            circuit = connection.execute("SELECT * FROM vision_provider_circuits WHERE provider_ref=?", (provider_ref,)).fetchone()
            if circuit and (circuit["needs_key_fix"] or circuit["open_until"] > now):
                record_decision("circuit_open")
                return "circuit_open"
            if connection.execute("SELECT 1 FROM vision_request_jobs WHERE provider_ref=? AND state='running'",
                                  (provider_ref,)).fetchone():
                record_decision("skipped_busy")
                return "skipped_busy"
            if cooldown_seconds and cooldown_ref and connection.execute(
                    "SELECT 1 FROM vision_request_jobs WHERE provider_ref=? AND cooldown_ref=? "
                    "AND request_dispatched=1 AND started_at>=? LIMIT 1",
                    (provider_ref, cooldown_ref, now - cooldown_seconds)).fetchone():
                record_decision("skipped_cooldown")
                return "skipped_cooldown"
            daily_count = connection.execute(
                "SELECT COUNT(*) FROM vision_request_jobs WHERE provider_ref=? AND quota_day=?",
                (provider_ref, day),
            ).fetchone()[0]
            if daily_count >= daily_limit:
                record_decision("skipped_budget")
                return "skipped_budget"
            rpm_count = connection.execute(
                "SELECT COUNT(*) FROM vision_request_jobs WHERE provider_ref=? AND started_at>=?",
                (provider_ref, now - 60),
            ).fetchone()[0]
            if rpm_count >= rpm_limit:
                record_decision("skipped_rpm")
                return "skipped_rpm"
            if tpm_limit:
                if not estimate:
                    record_decision("token_estimate_required")
                    return "token_estimate_required"
                used = connection.execute(
                    "SELECT COALESCE(SUM(CASE WHEN input_tokens IS NOT NULL AND output_tokens IS NOT NULL "
                    "THEN input_tokens+output_tokens ELSE COALESCE(estimated_tokens,0) END),0) "
                    "FROM vision_request_jobs WHERE provider_ref=? AND started_at>=?",
                    (provider_ref, now - 60),
                ).fetchone()[0]
                if used + estimate > tpm_limit:
                    record_decision("skipped_tpm")
                    return "skipped_tpm"
            connection.execute(
                "INSERT INTO vision_request_jobs(job_key,event_id,provider_ref,utc_day,state,started_at,lease_until,"
                "quota_day,model_id,estimated_tokens,cooldown_ref) VALUES(?,?,?,?, 'running',?,?,?,?,?,?)",
                (key, request.event_id, provider_ref, datetime.fromtimestamp(now, timezone.utc).date().isoformat(),
                 now, now + 45, day, model_id, estimate, cooldown_ref),
            )
            return None

        outcome = self._transaction(operation)
        if outcome:
            raise VisionFailure(outcome)
        return key

    def finish(self, key: str, outcome: str, *, retry_after: float | None = None,
               request_dispatched: bool | None = None, http_status: int | None = None,
               api_elapsed_ms: float | None = None, input_tokens: int | None = None,
               output_tokens: int | None = None):
        if outcome not in FAILURE_CATEGORIES | {"completed"}:
            raise ValueError("視覺工作結果分類不正確。")
        if retry_after is not None:
            VisionFailure("rate_limited", retry_after_seconds=retry_after)
        if request_dispatched is not None and type(request_dispatched) is not bool:
            raise ValueError("外送狀態證據不正確。")
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            raise ValueError("HTTP 狀態證據不正確。")
        if api_elapsed_ms is not None and (type(api_elapsed_ms) not in {int, float} or not math.isfinite(api_elapsed_ms) or api_elapsed_ms < 0):
            raise ValueError("服務回應時間證據不正確。")
        for value in (input_tokens, output_tokens):
            if value is not None and (type(value) is not int or not 0 <= value <= 100_000_000):
                raise ValueError("Token 使用量證據不正確。")
        now = self.clock()

        def operation():
            connection = self.store.connection
            job = connection.execute("SELECT provider_ref,state FROM vision_request_jobs WHERE job_key=?", (key,)).fetchone()
            if job is None or job["state"] != "running":
                return
            ref = job["provider_ref"]
            circuit = connection.execute("SELECT failures,open_until,needs_key_fix FROM vision_provider_circuits WHERE provider_ref=?", (ref,)).fetchone()
            failures = int(circuit["failures"]) if circuit else 0
            open_until, needs_key_fix = 0, 0
            if outcome == "completed":
                failures = 0
            elif outcome == "auth_error":
                failures += 1
                needs_key_fix = 1
            elif outcome == "rate_limited":
                failures += 1
                open_until = now + max(60, min(float(retry_after or 60), 86400))
            elif outcome in {"timeout", "unavailable", "bad_response"}:
                failures += 1
                if failures >= 3:
                    open_until = now + 60
            connection.execute(
                "UPDATE vision_request_jobs SET state='finished',outcome=?,finished_at=?,request_dispatched=?,"
                "http_status=?,api_elapsed_ms=?,input_tokens=?,output_tokens=?,"
                "estimated_tokens=CASE WHEN ? IS NOT NULL AND ? IS NOT NULL THEN NULL ELSE estimated_tokens END "
                "WHERE job_key=?",
                (outcome, now, None if request_dispatched is None else int(request_dispatched), http_status,
                 api_elapsed_ms, input_tokens, output_tokens, input_tokens, output_tokens, key),
            )
            connection.execute(
                "INSERT INTO vision_provider_circuits(provider_ref,failures,open_until,needs_key_fix) VALUES(?,?,?,?) "
                "ON CONFLICT(provider_ref) DO UPDATE SET failures=excluded.failures,open_until=excluded.open_until,"
                "needs_key_fix=excluded.needs_key_fix", (ref, failures, open_until, needs_key_fix),
            )
        self._transaction(operation)

    def key_replaced(self, provider_ref: str):
        self.store._write("DELETE FROM vision_provider_circuits WHERE provider_ref=?", (provider_ref,))

    def _recover(self, now: float):
        self.store._write("UPDATE vision_request_jobs SET state='finished',outcome='interrupted',finished_at=? "
                          "WHERE state='running' AND lease_until<=?", (now, now))
        self.store._write("DELETE FROM vision_request_jobs WHERE started_at<?", (now - 90 * 86400,))
        self.store._write("DELETE FROM vision_request_decisions WHERE occurred_at<?", (now - 90 * 86400,))

    def status(self, provider_ref: str, *, daily_limit=400, rpm_limit=12, tpm_limit=0,
               estimated_tokens_per_request=0, reset_timezone="UTC"):
        now = self.clock()
        self._recover(now)
        day = self.quota_day(now, reset_timezone)
        with self.store._connection_lock:
            daily = self.store.connection.execute(
                "SELECT COUNT(*) AS requests,SUM(CASE WHEN outcome='completed' THEN 1 ELSE 0 END) AS successful,"
                "MAX(CASE WHEN outcome='completed' THEN finished_at END) AS last_success,"
                "SUM(CASE WHEN outcome='timeout' THEN 1 ELSE 0 END) AS timeouts,"
                "SUM(CASE WHEN outcome='skipped_budget' THEN 1 ELSE 0 END) AS skipped_budget,"
                "COALESCE(SUM(input_tokens),0) AS input_tokens,COALESCE(SUM(output_tokens),0) AS output_tokens,"
                "COALESCE(SUM(estimated_tokens),0) AS estimated_tokens,"
                "SUM(CASE WHEN input_tokens IS NULL AND output_tokens IS NULL AND estimated_tokens IS NULL THEN 1 ELSE 0 END) AS unknown_tokens "
                "FROM vision_request_jobs WHERE provider_ref=? AND quota_day=?", (provider_ref, day)
            ).fetchone()
            minute = self.store.connection.execute(
                "SELECT COUNT(*) AS requests,COALESCE(SUM(input_tokens),0) AS input_tokens,"
                "COALESCE(SUM(output_tokens),0) AS output_tokens,COALESCE(SUM(estimated_tokens),0) AS estimated_tokens,"
                "SUM(CASE WHEN input_tokens IS NULL AND output_tokens IS NULL AND estimated_tokens IS NULL THEN 1 ELSE 0 END) AS unknown_tokens "
                "FROM vision_request_jobs WHERE provider_ref=? AND started_at>=?", (provider_ref, now - 60)
            ).fetchone()
            circuit = self.store.connection.execute(
                "SELECT * FROM vision_provider_circuits WHERE provider_ref=?", (provider_ref,)
            ).fetchone()
            decisions = self.store.connection.execute(
                "SELECT category,COUNT(*) AS count FROM vision_request_decisions "
                "WHERE provider_ref=? AND quota_day=? GROUP BY category", (provider_ref, day)
            ).fetchall()
        decision_counts = {str(row["category"]): int(row["count"]) for row in decisions}
        return {
            "quota_day": day, "reset_timezone": reset_timezone,
            "daily": {"used": int(daily["requests"]), "successful": int(daily["successful"] or 0),
                      "last_success_at": datetime.fromtimestamp(daily["last_success"], timezone.utc).isoformat()
                      if daily["last_success"] is not None else None,
                      "timeouts": int(daily["timeouts"] or 0),
                      "skipped_budget": int(daily["skipped_budget"] or 0) + decision_counts.get("skipped_budget", 0),
                      "skipped_rpm": decision_counts.get("skipped_rpm", 0),
                      "skipped_tpm": decision_counts.get("skipped_tpm", 0),
                      "skipped_busy": decision_counts.get("skipped_busy", 0),
                       "skipped_cooldown": decision_counts.get("skipped_cooldown", 0),
                      "circuit_open": decision_counts.get("circuit_open", 0),
                      "input_tokens_actual": int(daily["input_tokens"] or 0),
                      "output_tokens_actual": int(daily["output_tokens"] or 0),
                      "tokens_estimated": int(daily["estimated_tokens"] or 0),
                      "tokens_unknown": int(daily["unknown_tokens"] or 0),
                      "limit": daily_limit, "remaining": max(0, daily_limit-int(daily["requests"]))},
            "rpm": {"used": int(minute["requests"]), "limit": rpm_limit,
                    "remaining": max(0, rpm_limit-int(minute["requests"]))},
            "tpm": {"input_actual": int(minute["input_tokens"]), "output_actual": int(minute["output_tokens"]),
                    "estimated": int(minute["estimated_tokens"]), "unknown_requests": int(minute["unknown_tokens"] or 0),
                    "limit": tpm_limit, "estimate_per_request": estimated_tokens_per_request,
                    "basis": "actual_and_estimate" if minute["estimated_tokens"] and (minute["input_tokens"] or minute["output_tokens"])
                    else "estimate" if minute["estimated_tokens"] else "actual" if minute["input_tokens"] or minute["output_tokens"] else "no_usage_data"},
            "reserved_attempts": int(daily["requests"]),
            "circuit_open": bool(circuit and (circuit["needs_key_fix"] or circuit["open_until"] > now)),
            "key_fix_required": bool(circuit and circuit["needs_key_fix"]),
            "retry_in_seconds": max(0, int(circuit["open_until"] - now)) if circuit else 0,
        }

    def usage(self, provider_ref: str, *, hours=24, recent_limit=100, recent_offset=0):
        if type(hours) is not int or hours not in {1, 24, 168, 720}:
            raise ValueError("用量區間只允許最近1小時、24小時、7天或30天。")
        if type(recent_limit) is not int or not 1 <= recent_limit <= 200:
            raise ValueError("分析紀錄每頁只允許 1～200 筆。")
        if type(recent_offset) is not int or not 0 <= recent_offset <= 1_000_000:
            raise ValueError("分析紀錄頁面位置超出允許範圍。")
        now = self.clock()
        self._recover(now)
        since = now - hours * 3600
        with self.store._connection_lock:
            rows = self.store.connection.execute(
                "SELECT event_id,started_at,finished_at,outcome,http_status,api_elapsed_ms,input_tokens,output_tokens,"
                "estimated_tokens,model_id,request_dispatched FROM vision_request_jobs "
                "WHERE provider_ref=? AND started_at>=? ORDER BY started_at DESC LIMIT 20001", (provider_ref, since)
            ).fetchall()
            decisions = self.store.connection.execute(
                "SELECT occurred_at,model_id,category FROM vision_request_decisions "
                "WHERE provider_ref=? AND occurred_at>=? ORDER BY occurred_at DESC LIMIT 20001",
                (provider_ref, since),
            ).fetchall()
            model_usage = self.store.connection.execute(
                "SELECT model_id,COUNT(*) AS requests,SUM(CASE WHEN outcome='completed' THEN 1 ELSE 0 END) AS successful "
                "FROM vision_request_jobs WHERE provider_ref=? AND started_at>=? GROUP BY model_id ORDER BY requests DESC LIMIT 100",
                (provider_ref, since),
            ).fetchall()
            recent_total = self.store.connection.execute(
                "SELECT (SELECT COUNT(*) FROM vision_request_jobs WHERE provider_ref=? AND started_at>=?) + "
                "(SELECT COUNT(*) FROM vision_request_decisions WHERE provider_ref=? AND occurred_at>=?)",
                (provider_ref, since, provider_ref, since),
            ).fetchone()[0]
            recent_rows = self.store.connection.execute(
                "SELECT event_id,occurred_at,model,outcome,http_status,elapsed_ms,input_tokens,output_tokens,"
                "estimated_tokens,request_dispatched FROM ("
                "SELECT event_id,started_at AS occurred_at,model_id AS model,COALESCE(outcome,'running') AS outcome,"
                "http_status,api_elapsed_ms AS elapsed_ms,input_tokens,output_tokens,estimated_tokens,"
                "request_dispatched FROM vision_request_jobs WHERE provider_ref=? AND started_at>=? "
                "UNION ALL SELECT NULL AS event_id,occurred_at,model_id AS model,category AS outcome,NULL AS http_status,"
                "NULL AS elapsed_ms,NULL AS input_tokens,NULL AS output_tokens,NULL AS estimated_tokens,"
                "0 AS request_dispatched FROM vision_request_decisions WHERE provider_ref=? AND occurred_at>=?) "
                "ORDER BY occurred_at DESC LIMIT ? OFFSET ?",
                (provider_ref, since, provider_ref, since, recent_limit, recent_offset),
            ).fetchall()
        truncated = len(rows) > 20_000
        rows = rows[:20_000]
        decisions_truncated = len(decisions) > 20_000
        decisions = decisions[:20_000]
        state_counts = Counter(str(row["outcome"] or "running") for row in rows)
        decision_counts = Counter(str(row["category"]) for row in decisions)
        state_counts.update(decision_counts)
        timings = sorted(float(row["api_elapsed_ms"]) for row in rows if row["api_elapsed_ms"] is not None)
        actual_in = sum(int(row["input_tokens"] or 0) for row in rows)
        actual_out = sum(int(row["output_tokens"] or 0) for row in rows)
        actual_input_samples = sum(1 for row in rows if row["input_tokens"] is not None)
        estimates = sum(int(row["estimated_tokens"] or 0) for row in rows)
        bucket_seconds = 300 if hours == 1 else 3600 if hours == 24 else 86400
        bucket_count = math.ceil(hours * 3600 / bucket_seconds)
        start = math.floor(since / bucket_seconds) * bucket_seconds
        buckets = [{"start": datetime.fromtimestamp(start + index*bucket_seconds, timezone.utc).isoformat(),
                    "requests": 0, "success": 0, "failed": 0, "estimated_tokens": 0,
                    "input_tokens": 0, "output_tokens": 0, "elapsed_total_ms": 0.0,
                    "elapsed_samples": 0, "skipped_budget": 0, "skipped_rpm": 0,
                     "skipped_tpm": 0, "skipped_busy": 0, "skipped_cooldown": 0} for index in range(bucket_count)]
        for row in rows:
            index = int((float(row["started_at"]) - start) // bucket_seconds)
            if not 0 <= index < len(buckets):
                continue
            bucket = buckets[index]
            bucket["requests"] += 1
            outcome = row["outcome"]
            if outcome == "completed":
                bucket["success"] += 1
            elif outcome not in {None, "skipped_disabled", "skipped_no_consent"}:
                bucket["failed"] += 1
            bucket["estimated_tokens"] += int(row["estimated_tokens"] or 0)
            bucket["input_tokens"] += int(row["input_tokens"] or 0)
            bucket["output_tokens"] += int(row["output_tokens"] or 0)
            if row["api_elapsed_ms"] is not None:
                bucket["elapsed_total_ms"] += float(row["api_elapsed_ms"])
                bucket["elapsed_samples"] += 1
        for row in decisions:
            index = int((float(row["occurred_at"]) - start) // bucket_seconds)
            category = str(row["category"])
            if 0 <= index < len(buckets) and category in {"skipped_budget", "skipped_rpm", "skipped_tpm", "skipped_busy", "skipped_cooldown"}:
                buckets[index][category] += 1
        for bucket in buckets:
            samples = bucket.pop("elapsed_samples")
            total = bucket.pop("elapsed_total_ms")
            bucket["mean_elapsed_ms"] = round(total / samples, 2) if samples else None
        percentile = lambda p: timings[max(0, math.ceil(len(timings)*p)-1)] if timings else None
        recent = [{"time": datetime.fromtimestamp(row["occurred_at"], timezone.utc).isoformat(),
                   "event_id": str(row["event_id"]) if row["event_id"] is not None else None,
                   "model": row["model"], "outcome": row["outcome"], "http_status": row["http_status"],
                   "elapsed_ms": row["elapsed_ms"], "input_tokens": row["input_tokens"],
                   "output_tokens": row["output_tokens"], "estimated_tokens": row["estimated_tokens"],
                   "request_dispatched": None if row["request_dispatched"] is None else bool(row["request_dispatched"])}
                  for row in recent_rows]
        return {"window_hours": hours, "sample_count": len(rows), "decision_count": len(decisions),
                "truncated": truncated or decisions_truncated,
                "counts": dict(state_counts), "requests": len(rows),
                "successful": state_counts.get("completed", 0),
                "failed": sum(value for key, value in state_counts.items()
                              if key not in {"completed", "running", "skipped_disabled", "skipped_no_consent"}),
                "input_tokens_actual": actual_in, "output_tokens_actual": actual_out,
                "average_input_tokens": round(actual_in / actual_input_samples) if actual_input_samples else None,
                "tokens_estimated": estimates, "tokens_unknown": sum(
                    1 for row in rows if row["input_tokens"] is None and row["output_tokens"] is None and row["estimated_tokens"] is None),
                "mean_elapsed_ms": sum(timings)/len(timings) if timings else None,
                 "p95_elapsed_ms": percentile(.95), "buckets": buckets, "recent": recent,
                 "recent_offset": recent_offset, "recent_limit": recent_limit,
                 "recent_total": int(recent_total), "recent_has_more": recent_offset + len(recent) < int(recent_total),
                "model_usage": [{"model": row["model_id"] or "未知模型", "requests": int(row["requests"]),
                                 "successful": int(row["successful"] or 0)} for row in model_usage]}

    def reserve_token_probe(self, provider_ref: str, model_id: str, *, daily_limit=10, rpm_limit=1):
        """Reserve a bounded, user-triggered synthetic-image countTokens probe."""
        self.store._validate_opaque_ref(provider_ref)
        if not isinstance(model_id, str) or not 1 <= len(model_id) <= 120:
            raise ValueError("圖片模型識別不正確。")
        now = self.clock()
        probe_id = str(uuid4())

        def operation():
            connection = self.store.connection
            connection.execute("DELETE FROM vision_token_probes WHERE created_at<?", (now - 90 * 86400,))
            daily = connection.execute(
                "SELECT COUNT(*) FROM vision_token_probes WHERE provider_ref=? AND created_at>=?",
                (provider_ref, now - 86400)).fetchone()[0]
            if daily >= daily_limit:
                return "skipped_budget"
            minute = connection.execute(
                "SELECT COUNT(*) FROM vision_token_probes WHERE provider_ref=? AND created_at>=?",
                (provider_ref, now - 60)).fetchone()[0]
            if minute >= rpm_limit:
                return "skipped_rpm"
            connection.execute(
                "INSERT INTO vision_token_probes(probe_id,provider_ref,model_id,created_at) VALUES(?,?,?,?)",
                (probe_id, provider_ref, model_id, now))
            return None

        outcome = self._transaction(operation)
        if outcome:
            raise VisionFailure(outcome)
        return probe_id

    def finish_token_probe(self, probe_id: str, outcome: str, *, request_dispatched=None,
                           http_status=None, total_tokens=None, elapsed_ms=None):
        allowed = {"completed", "auth_error", "rate_limited", "unavailable", "bad_response", "timeout"}
        if outcome not in allowed:
            raise ValueError("Token 探測結果分類不正確。")
        if request_dispatched is not None and type(request_dispatched) is not bool:
            raise ValueError("Token 探測外送狀態不正確。")
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            raise ValueError("Token 探測 HTTP 狀態不正確。")
        if total_tokens is not None and (type(total_tokens) is not int or not 0 <= total_tokens <= 100_000_000):
            raise ValueError("Token 探測結果超出允許範圍。")
        if elapsed_ms is not None and (type(elapsed_ms) not in {int, float} or not math.isfinite(elapsed_ms) or elapsed_ms < 0):
            raise ValueError("Token 探測耗時不正確。")
        self.store._write(
            "UPDATE vision_token_probes SET finished_at=?,outcome=?,request_dispatched=?,http_status=?,total_tokens=?,elapsed_ms=? "
            "WHERE probe_id=? AND finished_at IS NULL",
            (self.clock(), outcome, None if request_dispatched is None else int(request_dispatched),
             http_status, total_tokens, elapsed_ms, probe_id))
