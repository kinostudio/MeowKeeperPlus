"""解析已完成隔離測溫的歷史證據，不把舊採樣當成目前溫度。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from tools.cpu_sensor_contract import parse_sensor_time, select_temperature

ROOT = Path(__file__).resolve().parents[1]


def summarize(report: dict, request_id: str, *, vendor: str) -> dict:
    if report.get("request_id") != request_id or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise ValueError("隔離測溫回應識別不符。")
    samples = report.get("samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= 128:
        raise ValueError("隔離測溫樣本格式不正確。")
    if vendor not in {"intel", "amd"}:
        raise ValueError("請明確指定已確認的CPU廠牌。")
    for field in ("elapsed_ms", "cpu_ms", "peak_working_set_bytes"):
        value = report.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("隔離測溫效能資料不正確。")
    if not 0 < report["elapsed_ms"] <= 60000:
        raise ValueError("隔離測溫時間不正確。")
    end = parse_sensor_time(report["finished_at"])
    selected = [select_temperature(sample, vendor=vendor, now=end) for sample in samples]
    values = [item["temperature_c"] for item in selected if item["status"] == "available"]
    return {"scope": "real_hardware_isolated_trial", "live_reading": False,
            "status": "passed" if len(values) == len(samples) else "failed",
            "hardware_name": selected[0]["hardware_name"], "sensor_type": selected[0]["sensor_type"],
            "sensor_name": selected[0]["sensor_name"], "sample_count": len(samples), "valid_samples": len(values),
            "temperatures_c": values, "minimum_c": min(values) if values else None,
            "maximum_c": max(values) if values else None, "mean_c": sum(values) / len(values) if values else None,
            "started_at": selected[0]["observed_at"], "finished_at": report["finished_at"],
            "elapsed_ms": report["elapsed_ms"], "cpu_ms": report["cpu_ms"],
            "single_core_cpu_percent_including_startup": 100 * report["cpu_ms"] / report["elapsed_ms"],
            "peak_working_set_bytes": report["peak_working_set_bytes"],
            "steady_state_budget_passed": None, "host_integrated": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_id")
    parser.add_argument("--vendor", choices=("intel", "amd"), required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{32}", args.request_id):
        raise ValueError("測溫識別碼格式不正確。")
    folder = ROOT / "reports/cpu-sensor-prototype"
    source = folder / f"cpu-snapshot-{args.request_id}.json"
    if source.stat().st_size > 262144:
        raise ValueError("測溫紀錄超過大小限制。")
    data = source.read_bytes()
    summary = summarize(json.loads(data), args.request_id, vendor=args.vendor)
    summary["raw_sha256"] = hashlib.sha256(data).hexdigest()
    (folder / f"cpu-summary-{args.request_id}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
