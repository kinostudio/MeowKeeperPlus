"""Read-only experimental summaries; missing observations are never sleep."""
from datetime import datetime
from typing import Any


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError("時間欄位必須是文字。")
    return datetime.fromisoformat(value[:-1]+"+00:00" if value.endswith("Z") else value)


def summarize_behavior(store: Any, start: str, end: str) -> dict[str, Any]:
    try:
        lower, upper = _timestamp(start), _timestamp(end)
        if lower.tzinfo is None or upper.tzinfo is None:
            raise ValueError
        seconds = (upper - lower).total_seconds()
        if not 0 < seconds <= 31 * 86400:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("請提供含時區且不超過 31 天的有效時間範圍。") from exc
    rows = store.behavior_summary_rows(lower.isoformat(), upper.isoformat())
    if len(rows) > 10000:
        raise ValueError("資料超過 10,000 筆，請縮短查詢日期範圍。")
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        pet = row["pet_id"]
        known = bool(pet and pet != "unknown")
        key = ("pet", pet) if known else ("track", row["track_id"])
        group = groups.setdefault(key, {
            "name": row["display_name"] or ("已登錄寵物" if known else "尚未確認身分（獨立軌跡）"),
            "identity_known": known, "pending_count": 0, "invalid_count": 0,
            "behaviors": {}, "intervals": [],
        })
        if row["ended_at"] is None:
            group["pending_count"] += 1
            continue
        try:
            begin = _timestamp(row["started_at"])
            finish = _timestamp(row["ended_at"])
            if begin.tzinfo is None or finish.tzinfo is None or finish < begin:
                raise ValueError
        except (TypeError, ValueError):
            group["invalid_count"] += 1
            continue
        begin, finish = max(lower, begin), min(upper, finish)
        duration = (finish - begin).total_seconds()
        if duration <= 0:
            continue
        group["intervals"].append((begin, finish))
        item = group["behaviors"].setdefault(row["behavior_state"], {
            "state": row["behavior_state"], "seconds": 0.0, "intersecting_event_count": 0,
            "started_event_count": 0,
        })
        item["seconds"] += duration
        item["intersecting_event_count"] += 1
        item["started_event_count"] += int(lower <= _timestamp(row["started_at"]) < upper)
    output = []
    for group in groups.values():
        intervals = sorted(group.pop("intervals"))
        overlap = False
        last_end = None
        for begin, finish in intervals:
            if last_end is not None and begin < last_end:
                overlap = True
            last_end = max(last_end, finish) if last_end else finish
        items = list(group.pop("behaviors").values())
        for item in items:
            item["calendar_percent"] = round(item["seconds"] / seconds * 100, 2) if not overlap else None
            item["mean_observed_seconds"] = item["seconds"] / item["intersecting_event_count"]
        group.update(behaviors=items, overlapping_intervals=overlap,
                     effective_monitoring_percent=None)
        output.append(group)
    return {"status": "experimental" if rows else "no_data", "start": start, "end": end,
            "calendar_seconds": seconds, "groups": output,
            "message_zh_tw": "僅統計已結束候選事件；未知時段不推算。平均為查詢範圍內時長，非完整用餐時長。有效監控分母尚未建立。"}
