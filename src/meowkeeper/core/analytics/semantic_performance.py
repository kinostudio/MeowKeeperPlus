"""有界的SQLite統計；只計已保存且可確認的請求，不以排隊推測外送。"""

import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from meowkeeper.core.event_semantics import FAILURE_CATEGORIES


def distribution(values):
    values = sorted(value for value in values if type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 604800000)
    if not values:
        return {"sample_count": 0, "mean_ms": None, "p50_ms": None, "p95_ms": None}
    return {"sample_count": len(values), "mean_ms": sum(values)/len(values),
            "p50_ms": values[math.ceil(len(values)*.5)-1], "p95_ms": values[math.ceil(len(values)*.95)-1]}


def summarize_semantic_performance(store, *, limit=100, hours=None, start=None, end=None,
                                   provider=None, model=None, validity_filter=None, input_mode=None):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("效能統計筆數必須介於1至200。")
    if hours is not None and (type(hours) is not int or hours not in {1, 24, 168, 720}):
        raise ValueError("統計區間只允許1小時、24小時、7天或30天。")
    if (start is None) != (end is None) or start is not None and hours is not None:
        raise ValueError("自訂區間需同時提供開始與結束，且不能混用最近小時。")
    if start is not None:
        try:
            start, end = (datetime.fromisoformat(value[:-1]+"+00:00" if value.endswith("Z") else value) for value in (start,end))
            if start.tzinfo is None or end.tzinfo is None or not timedelta(0) < end-start <= timedelta(days=31):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("請選擇含時區、最多31天的有效時間區間。") from None
    for value in (provider, model):
        if value is not None and (not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}",value)):
            raise ValueError("服務或模型篩選名稱不正確。")
    if validity_filter is not None and validity_filter not in {"valid","invalid","unclear","conflict","unreviewed"}:
        raise ValueError("人工覆核篩選不正確。")
    if input_mode is not None and input_mode not in {"storyboard","representative","unknown"}:
        raise ValueError("分析輸入篩選不正確。")
    filtered = any(value is not None for value in (provider,model,validity_filter,input_mode))
    full_window = hours is not None or start is not None
    eligibility = "SELECT event_id FROM media_events WHERE event_type='cat_detected' AND trigger_origin='detector'"
    params = []
    if start is not None:
        eligibility += " AND julianday(trigger_at)>=julianday(?) AND julianday(trigger_at)<julianday(?)"
        params.extend((start.isoformat(),end.isoformat()))
    elif hours is not None:
        eligibility += " AND julianday(trigger_at)>=julianday(?)"
        params.append((datetime.now(timezone.utc)-timedelta(hours=hours)).isoformat())
    else:
        eligibility += " ORDER BY julianday(trigger_at) DESC,rowid DESC LIMIT ?"
        params.append(limit)
    max_analyses, max_deliveries = (20000, 5000) if full_window else (2000, 500)
    with store._connection_lock:
        event_count = store.connection.execute(f"SELECT COUNT(*) FROM ({eligibility})", params).fetchone()[0]
        semantic_rows = store.connection.execute(
            f"WITH eligible AS ({eligibility}) SELECT CASE WHEN length(candidate_json)<=262144 THEN candidate_json END,e.event_id,m.trigger_at FROM event_semantic_revisions r JOIN eligible e ON e.event_id=r.event_id JOIN media_events m ON m.event_id=e.event_id ORDER BY r.created_at DESC,r.rowid DESC LIMIT ?", [*params, max_analyses+1],
        ).fetchall()
        delivery_rows = store.connection.execute(
            f"WITH eligible AS ({eligibility}) SELECT CASE WHEN length(trace_json)<=262144 THEN trace_json END FROM event_delivery_traces r JOIN eligible e ON e.event_id=r.event_id ORDER BY r.created_at DESC,r.rowid DESC LIMIT ?", [*params, max_deliveries+1],
        ).fetchall()
        validity_rows = store.connection.execute(
            f"WITH eligible AS ({eligibility}) SELECT r.event_id,r.validity FROM web_event_validity_reviews r JOIN eligible e ON e.event_id=r.event_id WHERE r.revision=(SELECT MAX(v.revision) FROM web_event_validity_reviews v WHERE v.event_id=r.event_id AND v.reviewer_ref=r.reviewer_ref) LIMIT 40001", params,
        ).fetchall()
    truncated = len(semantic_rows) > max_analyses or len(delivery_rows) > max_deliveries
    truncated |= len(validity_rows) > 40000
    validity = {}
    for row in validity_rows[:40000]:
        validity.setdefault(row[0],set()).add(row[1])
    invalid = 0
    analyses, traces = [], []
    for rows, target, maximum in ((semantic_rows, analyses, max_analyses), (delivery_rows, traces, max_deliveries)):
        for row in rows[:maximum]:
            try:
                value = json.loads(row[0])
                if not isinstance(value, dict):
                    raise TypeError
                if target is traces:
                    from meowkeeper.core.delivery_telemetry import validate_delivery_trace
                    value = validate_delivery_trace(value)
                else:
                    states_for_event = validity.get(row[1],set())
                    value["_validity"] = next(iter(states_for_event)) if len(states_for_event) == 1 else "conflict" if states_for_event else "unreviewed"
                    value["_event_id"] = row[1]
                    value["_trigger_at"] = row[2]
                target.append(value)
            except (TypeError, ValueError):
                invalid += 1
    options = {key:sorted({value.get("analysis_source",{}).get(key,"unknown") for value in analyses
                          if isinstance(value.get("analysis_source",{}),dict)
                          and isinstance(value.get("analysis_source",{}).get(key,"unknown"),str)})
               for key in ("provider_id","model_id")}
    if filtered:
        analyses = [value for value in analyses
                    if all(wanted is None or value.get("analysis_source",{}).get(key,"unknown") == wanted
                           for key,wanted in (("provider_id",provider),("model_id",model),("input_mode",input_mode)))
                    and (validity_filter is None or value["_validity"] == validity_filter)]
        matched_jobs = {value.get("job_id") for value in analyses if isinstance(value.get("job_id"),str)}
        traces = [value for value in traces if value.get("semantic_job_id") in matched_jobs]
    daily = {}
    states = Counter()
    confirmed = successes = unknown = not_sent = skipped = 0
    metrics = {key: [] for key in ("queue_ms", "preparation_ms", "api_response_ms", "validation_ms",
                                  "video_prepare_ms", "extra_wait_ms", "telegram_upload_ms")}
    jobs = {}
    groups = {}
    quality = {state:{"analysis_count":0,"successful_api_ms":[]} for state in ("valid","invalid","unclear","conflict","unreviewed")}
    allowed = FAILURE_CATEGORIES | {"completed", "failed", "cancelled", "queue_full", "timeout", "invalid_image"}
    for result in analyses:
        status = result.get("status")
        if not isinstance(status, str) or status not in allowed:
            invalid += 1
            continue
        tracks = result.get("tracks", [])
        if not isinstance(tracks, list) or any(not isinstance(row, dict) or not isinstance(row.get("tags"), list) for row in tracks):
            invalid += 1
            continue
        states[status] += 1
        try:
            day = datetime.fromisoformat(result["_trigger_at"]).astimezone(timezone.utc).date().isoformat()
        except (ValueError,TypeError):
            day = "unknown"
        trend = daily.setdefault(day,{"analysis_count":0,"confirmed":0,"success":0,"api_ms":[]})
        trend["analysis_count"] += 1
        assessment = quality[result["_validity"]]
        assessment["analysis_count"] += 1
        transport = result.get("transport", {})
        sent = transport.get("request_dispatched") if isinstance(transport, dict) else None
        if sent is not None and type(sent) is not bool:
            invalid += 1
            continue
        if sent is True:
            confirmed += 1
            successes += status == "completed"
            trend["confirmed"] += 1
            trend["success"] += status == "completed"
        elif sent is False:
            not_sent += 1
        else:
            unknown += 1
        if status.startswith("skipped_") or status in {"queue_full", "invalid_image", "insufficient_evidence", "circuit_open", "duplicate_request"}:
            skipped += 1
        timing = result.get("timings", {})
        if isinstance(timing, dict):
            for key in ("queue_ms", "preparation_ms", "validation_ms"):
                metrics[key].append(timing.get(key))
            if sent is True:
                metrics["api_response_ms"].append(timing.get("api_response_ms"))
                trend["api_ms"].append(timing.get("api_response_ms"))
                if status == "completed":
                    assessment["successful_api_ms"].append(timing.get("api_response_ms"))
        if isinstance(result.get("job_id"), str):
            jobs[result["job_id"]] = result
        source = result.get("analysis_source", {})
        key = (source.get("provider_id", "unknown"), source.get("model_id", "unknown")) if isinstance(source, dict) else ("unknown", "unknown")
        if all(isinstance(value, str) and len(value) <= 120 for value in key):
            group = groups.setdefault(key, {"count":0, "confirmed":0, "success":0, "unknown":0, "api_ms":[]})
            group["count"] += 1
            group["confirmed"] += sent is True
            group["success"] += sent is True and status == "completed"
            group["unknown"] += sent is None
            if sent is True and isinstance(timing, dict):
                group["api_ms"].append(timing.get("api_response_ms"))
    linked = missed = late = late_unknown = unlinked = accepted = upload_attempts = omitted = 0
    local_captions = ai_captions = 0
    replays = {seconds: {"waits":[], "enriched":0, "unknown":0} for seconds in (0, 5, 10)}
    for trace in traces:
        for key in ("video_prepare_ms", "extra_wait_ms"):
            metrics[key].append(trace.get(key))
        local_captions += trace.get("caption_source") == "local"
        ai_captions += trace.get("caption_source") == "ai_candidate"
        reference = trace.get("semantic_job_id")
        result = jobs.get(reference) if isinstance(reference, str) else None
        for seconds, replay in replays.items():
            if result is None:
                replay["unknown"] += 1
                continue
            try:
                ready = datetime.fromisoformat(trace["media_sendable_at"])
                done = datetime.fromisoformat(result["timings"]["finished_at"])
                if ready.tzinfo is None or done.tzinfo is None:
                    raise ValueError
                remaining = max(0, (done-ready).total_seconds())
                if remaining > 604800:
                    raise ValueError
                replay["waits"].append(min(seconds, remaining)*1000)
                replay["enriched"] += result.get("status") == "completed" and any(tag not in {"unknown","possible"} for row in result.get("tracks", []) for tag in row.get("tags",[])) and remaining <= seconds
            except (KeyError, TypeError, ValueError):
                replay["unknown"] += 1
        if result is None and trace.get("wait_status") != "not_requested":
            unlinked += 1
        if result and result.get("status") == "completed" and any(tag not in {"unknown","possible"} for row in result.get("tracks", []) if isinstance(row, dict) for tag in row.get("tags",[])):
            linked += 1
            if trace.get("caption_source") == "local":
                missed += 1
                try:
                    late += datetime.fromisoformat(result["timings"]["finished_at"]) > datetime.fromisoformat(trace["wait_finished_at"])
                except (TypeError, ValueError, KeyError):
                    late_unknown += 1  # 缺少時刻不能推定晚到。
        uploads = trace.get("uploads", [])
        if isinstance(uploads, list):
            for upload in uploads:
                if isinstance(upload, dict) and type(upload.get("accepted")) is bool:
                    upload_attempts += 1
                    accepted += upload["accepted"]
                    metrics["telegram_upload_ms"].append(upload.get("elapsed_ms"))
        if type(trace.get("omitted_upload_count")) is int:
            omitted += trace["omitted_upload_count"]
    incomplete = truncated or invalid > 0
    return {"status": "partial" if incomplete else "ok" if event_count else "no_data",
            "scope": "time_window" if full_window else "recent_events", "window_hours": hours,
            "event_count": event_count, "matched_event_count":len({value["_event_id"] for value in analyses}),
            "filters_applied":filtered,"filter_options":options,
            "range_start":start.isoformat() if start else None,"range_end":end.isoformat() if end else None,
            "sample_limit": None if full_window else limit, "analysis_count": sum(states.values()),
            "daily_trend":[{"date_utc":day,"analysis_count":group["analysis_count"],"confirmed_request_count":group["confirmed"],
                            "success_rate":group["success"]/group["confirmed"] if group["confirmed"] and not incomplete else None,
                            "api_response":distribution(group["api_ms"])} for day,group in sorted(daily.items())],
            "provider_models": [{"provider_id":provider, "model_id":model, "analysis_count":group["count"],
                                  "confirmed_request_count":group["confirmed"], "unknown_request_count":group["unknown"],
                                  "success_rate":group["success"]/group["confirmed"] if group["confirmed"] and not incomplete else None,
                                  "api_response":distribution(group["api_ms"])} for (provider, model), group in sorted(groups.items())],
            "quality_groups":{state:{"analysis_count":group["analysis_count"],"successful_response":distribution(group["successful_api_ms"])} for state,group in quality.items()},
            "usage_cost":None,
            "grace_replay": [{"seconds":seconds, "sample_count":len(replay["waits"]), "unknown_count":replay["unknown"],
                              "candidate_available_count":replay["enriched"], "extra_wait":distribution(replay["waits"])} for seconds, replay in replays.items()],
            "delivery_trace_count": len(traces), "truncated": truncated, "invalid_record_count": invalid,
            "confirmed_request_count": confirmed, "unknown_request_count": unknown, "not_dispatched_count": not_sent,
            "skipped_count": skipped, "status_counts": dict(states), "valid_success_count": successes,
            "api_success_rate": successes/confirmed if confirmed and not incomplete else None,
            "ai_caption_count": ai_captions, "local_caption_count": local_captions,
            "candidate_caption_linked_count": linked, "candidate_caption_missed_count": missed,
            "candidate_caption_miss_rate": missed/linked if linked and not incomplete else None,
            "late_candidate_count": late, "upload_attempt_count": upload_attempts,
            "late_unknown_count": late_unknown,
            "unlinked_caption_count": unlinked,
            "accepted_upload_count": accepted, "omitted_upload_count": omitted,
            "upload_acceptance_rate": accepted/upload_attempts if upload_attempts and not omitted and not incomplete else None,
            "metrics": {key: distribution(values) for key, values in metrics.items()},
            "message_zh_tw": "統計最近事件的已保存紀錄；未知送出證據不算API分母，略過不算失敗。上傳接受率按嘗試計算，不是手機已讀。不同工作可能並行，耗時不得直接相加。"}
