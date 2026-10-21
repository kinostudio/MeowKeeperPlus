"""通知時序白名單；不保存文案、媒體網址或外部回應。"""

from __future__ import annotations

import math
from datetime import datetime
from uuid import UUID

from meowkeeper.core.event_semantics import FAILURE_CATEGORIES


def validate_delivery_trace(raw: dict) -> dict:
    required = {"trace_id", "event_trigger_at", "media_sendable_at", "wait_started_at", "wait_finished_at",
                "validation_transcode_ms", "extra_wait_ms", "wait_budget_ms", "caption_source",
                "wait_status", "uploads", "omitted_upload_count"}
    if not isinstance(raw, dict) or set(raw) - required - {"video_prepare_ms", "semantic_job_id"} or not required <= set(raw):
        raise ValueError("通知時序欄位不正確。")
    result = dict(raw)
    result["trace_id"] = str(UUID(raw["trace_id"]))
    if "semantic_job_id" in raw:
        result["semantic_job_id"] = str(UUID(raw["semantic_job_id"]))
    for key in ("event_trigger_at", "media_sendable_at", "wait_started_at", "wait_finished_at"):
        value = datetime.fromisoformat(raw[key])
        if value.tzinfo is None:
            raise ValueError("通知時序必須含時區。")
    for key in ("validation_transcode_ms", "extra_wait_ms", "wait_budget_ms", "video_prepare_ms"):
        if key not in raw:
            continue
        if type(raw[key]) not in {int, float} or not math.isfinite(raw[key]) or not 0 <= raw[key] <= 604800000:
            raise ValueError("通知耗時無法驗證。")
    if raw["wait_budget_ms"] > 10000 or raw["caption_source"] not in {"local", "ai_candidate"}:
        raise ValueError("通知等待或文案來源不正確。")
    if raw["wait_status"] not in FAILURE_CATEGORIES | {"not_requested", "completed", "candidate_empty", "failed", "cancelled", "queue_full"}:
        raise ValueError("通知候選狀態不正確。")
    if type(raw["omitted_upload_count"]) is not int or raw["omitted_upload_count"] < 0:
        raise ValueError("通知時序省略數量不正確。")
    if not isinstance(raw["uploads"], list) or len(raw["uploads"]) > 256:
        raise ValueError("通知上傳紀錄過多。")
    uploads = []
    for item in raw["uploads"]:
        if not isinstance(item, dict) or set(item) != {"recipient_ref", "attempt", "started_at", "finished_at", "elapsed_ms", "accepted"}:
            raise ValueError("通知上傳欄位不正確。")
        reference = item["recipient_ref"]
        if not isinstance(reference, str) or len(reference) != 64 or any(c not in "0123456789abcdef" for c in reference):
            raise ValueError("通知收件者摘要不正確。")
        if type(item["attempt"]) is not int or item["attempt"] < 1 or type(item["accepted"]) is not bool:
            raise ValueError("通知上傳結果不正確。")
        for key in ("started_at", "finished_at"):
            if datetime.fromisoformat(item[key]).tzinfo is None:
                raise ValueError("通知上傳時序必須含時區。")
        if type(item["elapsed_ms"]) not in {int, float} or not math.isfinite(item["elapsed_ms"]) or not 0 <= item["elapsed_ms"] <= 604800000:
            raise ValueError("通知上傳耗時無法驗證。")
        uploads.append(dict(item))
    result["uploads"] = uploads
    return result
