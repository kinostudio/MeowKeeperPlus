"""CPU感測資料契約；來源、時間與物理意義均需明確。"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

STATUSES = {"available", "unavailable", "permission_required", "unsupported", "stale", "error"}
REASONS = {"driver_missing", "driver_unavailable", "dependency_missing", "sensor_missing", "sensor_read_failed"}


def parse_sensor_time(value: str) -> datetime:
    """.NET round-trip timestamps use seven fractional digits; Python 3.10 needs six."""
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("感測器時間格式不正確。")
    normalized = re.sub(r"(\.\d{6})\d+(?=(?:Z|[+-]\d{2}:\d{2})$)", r"\1", value)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def select_temperature(payload: dict, *, vendor: str, now: datetime | None = None,
                       stale_seconds: int = 15) -> dict:
    """Only known physical sensor semantics; never interpret Tctl/ACPI as Tdie."""
    result = {"device_type": "cpu", "temperature_c": None, "sensor_type": None,
              "sensor_name": None, "hardware_name": None, "provider": "libre_hardware_monitor",
              "observed_at": None, "status": "error", "error_reason": "invalid_payload",
              "confidence": "unavailable", "selection_reason": None, "candidates": []}
    if (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1
            or payload.get("provider") != "libre_hardware_monitor" or not isinstance(payload.get("status"), str)
            or payload["status"] not in STATUSES):
        return result
    try:
        stamp = parse_sensor_time(payload["observed_at"])
        if stamp.tzinfo is None:
            return result
        age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
    except (KeyError, ValueError, TypeError):
        return result
    result["observed_at"] = stamp.isoformat()
    if age < -2:
        return result
    if age > stale_seconds:
        result.update(status="stale", error_reason="sample_expired")
        return result
    status = payload["status"]
    if status != "available":
        reason = payload.get("reason")
        result.update(status=status, error_reason=reason if isinstance(reason, str) and reason in REASONS else "sensor_unavailable")
        return result
    sensors = payload.get("sensors")
    if not isinstance(sensors, list) or len(sensors) > 128:
        return result
    candidates = []
    for sensor in sensors:
        if not isinstance(sensor, dict):
            continue
        value = sensor.get("temperature_c")
        if type(value) not in (int, float) or not math.isfinite(value) or not -20 <= value <= 150:
            continue
        if any(not isinstance(sensor.get(key), str) or len(sensor[key]) > 160
               or any(ord(c) < 32 for c in sensor[key]) for key in ("sensor_name", "hardware_name", "sensor_id")):
            continue
        name = sensor["sensor_name"].lower()
        priority, kind = 9, None
        if vendor.lower() == "intel":
            if name == "cpu package":
                priority, kind = 0, "cpu_package"
            elif name in {"cpu die", "cpu die (average)"}:
                priority, kind = 1, "cpu_die"
            elif re.fullmatch(r"cpu core #?\d+", name):
                priority, kind = 2, "cpu_core"
        elif vendor.lower() == "amd":
            if name in {"core (tdie)", "cpu (tdie)", "cpu die", "cpu die (average)"}:
                priority, kind = 0, "cpu_die"
            elif re.fullmatch(r"ccd\d+ \(tdie\)", name):
                priority, kind = 1, "cpu_die"
        if kind is not None:
            candidates.append({**sensor, "priority": priority, "sensor_type": kind})
    if not candidates:
        result.update(status="unavailable", error_reason="trusted_sensor_missing")
        return result
    selected = min(candidates, key=lambda item: (item["priority"], -item["temperature_c"], item["sensor_id"]))
    result.update(status="available", error_reason=None, confidence="direct_sensor",
                  temperature_c=float(selected["temperature_c"]), sensor_type=selected["sensor_type"],
                  sensor_name=selected["sensor_name"], hardware_name=selected["hardware_name"],
                  candidates=candidates,
                  selection_reason="hottest_core_not_package" if selected["sensor_type"] == "cpu_core" else "preferred_type_then_hottest")
    return result
