"""隔離量測 960／1280／1920 AI 尺寸，使用本地測試圖，不連攝影機。"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

import cv2
import psutil

# Resolve the candidate tree explicitly so the bundled/embedded Windows Python
# can run this probe without changing its global site-packages or ._pth file.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meowkeeper.core.inference.accelerated_yolo import AcceleratedYolo, verified_export
from meowkeeper.core.inference.gpu_telemetry import CachedGpuSampler
from meowkeeper.runtime import default_model_file

SIZES = (960, 1280, 1920)
THERMAL_STOP_C = 80
MINIMUM_FREE_VRAM_MIB = 128


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"samples": 0, "average_ms": None, "p50_ms": None, "p90_ms": None,
                "p95_ms": None, "p99_ms": None, "max_ms": None}
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]

    return {
        "samples": len(ordered),
        "average_ms": round(statistics.fmean(ordered), 2),
        "p50_ms": round(statistics.median(ordered), 2),
        "p90_ms": round(percentile(.90), 2),
        "p95_ms": round(percentile(.95), 2),
        "p99_ms": round(percentile(.99), 2),
        "max_ms": round(ordered[-1], 2),
    }


def _metric_distribution(values: list[float], unit: str) -> dict:
    if not values:
        return {"samples": 0, "mean": None, "max": None, "unit": unit}
    return {"samples": len(values), "mean": round(statistics.fmean(values), 2),
            "max": round(max(values), 2), "unit": unit}


def _round_schedule(sizes: list[int], rounds: int) -> list[list[int]]:
    """Alternate direction each round so a size is not always tested first or last."""
    ordered = list(sizes)
    return [ordered if index % 2 == 0 else list(reversed(ordered)) for index in range(rounds)]


def _align_schedule_deadline(deadline: float, now: float,
                             period: float) -> tuple[float, int, float]:
    """Skip expired slots and rebase to actual start so lateness cannot create bursts."""
    lag = max(0.0, now - deadline)
    missed = int(lag // period)
    aligned = deadline + missed * period
    start_lag = max(0.0, now - aligned)
    return (now if start_lag else aligned), missed, start_lag


def _advance_schedule_deadline(deadline: float, finished_at: float,
                               period: float) -> tuple[float, int]:
    """After work overruns the next slot, rebase to completion instead of catching up."""
    next_deadline = deadline + period
    if finished_at <= next_deadline:
        return next_deadline, 0
    # One late frame is still serviced; count only later whole slots that had to be skipped.
    missed = int((finished_at - next_deadline) // period)
    return finished_at, missed


def _aggregate_size_runs(size: int, fps: int, runs: list[tuple[dict, dict]]) -> dict:
    """Pool per-frame samples before computing percentiles; never average run P95s."""
    timing_values: dict[str, list[float]] = {}
    telemetry_values: dict[str, list[float]] = {}
    schedule_lags: list[float] = []
    cpu_values: list[float] = []
    rss_values: list[float] = []
    throttle_reasons: dict[str, int] = {}
    for stage, raw in runs:
        schedule_lags.extend(raw["schedule_lag_ms"])
        cpu_values.extend(raw["cpu_values"])
        rss_values.extend(raw["rss_values"])
        for name, values in raw["timings"].items():
            timing_values.setdefault(name, []).extend(values)
        for name, values in raw["telemetry_values"].items():
            telemetry_values.setdefault(name, []).extend(values)
        for reason, count in stage["observed_throttle_reasons"].items():
            throttle_reasons[reason] = throttle_reasons.get(reason, 0) + count

    elapsed = sum(stage["duration_seconds"] for stage, _ in runs)
    frames = sum(stage["completed_frames"] for stage, _ in runs)
    gpu_load = telemetry_values.get("gpu_kernel_busy_percent", [])
    return {
        "input_size_long_edge_px": size,
        "target_fps": fps,
        "completed_rounds": len(runs),
        "duration_seconds": round(elapsed, 2),
        "actual_fps": round(frames / max(elapsed, .001), 3),
        "completed_frames": frames,
        "missed_schedule_slots": sum(stage["missed_schedule_slots"] for stage, _ in runs),
        "schedule_lag_ms": _distribution(schedule_lags),
        "timings": {name: _distribution(values) for name, values in timing_values.items()},
        "cpu_worker_process_normalized_percent": _metric_distribution(
            cpu_values, "% of logical CPU capacity"),
        "process_rss_bytes": _metric_distribution(rss_values, "bytes"),
        "gpu_kernel_busy_percent": _metric_distribution(gpu_load, "% whole GPU device"),
        "memory_controller_busy_percent": _metric_distribution(
            telemetry_values.get("memory_controller_busy_percent", []), "% whole GPU device"),
        "gpu_temperature_c": _metric_distribution(telemetry_values.get("temperature_c", []), "°C"),
        "dedicated_gpu_memory_used_mib": _metric_distribution(
            telemetry_values.get("dedicated_memory_used_mib", []), "MiB whole GPU device"),
        "gpu_free_memory_mib": _metric_distribution(telemetry_values.get("memory_free_mib", []), "MiB"),
        "graphics_clock_mhz": _metric_distribution(telemetry_values.get("graphics_clock_mhz", []), "MHz"),
        "memory_clock_mhz": _metric_distribution(telemetry_values.get("memory_clock_mhz", []), "MHz"),
        "gpu_power_draw_w": _metric_distribution(telemetry_values.get("power_draw_w", []), "W"),
        "observed_throttle_reasons": throttle_reasons,
        "soft_40_percent_load_target": "within_target" if gpu_load and statistics.fmean(gpu_load) <= 40
        else "above_target" if gpu_load else "not_measured",
        "stop_reasons": [stage["stop_reason"] for stage, _ in runs if stage["stop_reason"]],
    }


def _telemetry_ready(sampler: CachedGpuSampler, device: str, timeout=5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sampler.sample(device)
        sample = sampler.snapshot()
        if sample["status"] == "available":
            return sample
        time.sleep(.05)
    return sampler.snapshot()


def _initial_telemetry(sampler: CachedGpuSampler, model: AcceleratedYolo) -> dict:
    """Only attach NVIDIA measurements to a CUDA run; never mislabel them as Intel GPU load."""
    if model.device.startswith("cuda"):
        return _telemetry_ready(sampler, model.device)
    scope = ("Intel GPU utilization and memory are not measured by the NVIDIA sampler."
             if model.backend == "OpenVINO GPU" else "GPU telemetry is not applicable to the CPU backend.")
    return {"status": "not_measured", "gpu_model": None, "scope": scope}


def _nvidia_sample_required(model: AcceleratedYolo) -> bool:
    return model.device.startswith("cuda")


def _check_safety(sample: dict, *, require_temperature: bool) -> str | None:
    temperature = sample.get("temperature_c")
    if temperature is None and require_temperature:
        return "CUDA 溫度無法取得，為安全起見停止量測。"
    if temperature is not None and temperature >= THERMAL_STOP_C:
        return f"GPU 已達 {THERMAL_STOP_C}°C 安全停止值。"
    free_mib = sample.get("memory_free_mib")
    if free_mib is not None and free_mib < MINIMUM_FREE_VRAM_MIB:
        return f"GPU 可用顯存低於 {MINIMUM_FREE_VRAM_MIB} MiB 安全停止值。"
    return None


def _run_stage(model: AcceleratedYolo, sampler: CachedGpuSampler, *, image_bytes: bytes,
               size: int, fps: int, seconds: int, process: psutil.Process) -> tuple[dict, dict]:
    period = 1 / fps
    started = time.monotonic()
    deadline = started
    frames = 0
    missed_slots = 0
    schedule_lags: list[float] = []
    stage_values: dict[str, list[float]] = {}
    cpu_values: list[float] = []
    rss_values: list[float] = []
    telemetry_values: dict[str, list[float]] = {
        "gpu_kernel_busy_percent": [], "memory_controller_busy_percent": [],
        "temperature_c": [], "dedicated_memory_used_mib": [], "memory_free_mib": [],
        "graphics_clock_mhz": [], "memory_clock_mhz": [], "power_draw_w": [],
    }
    throttle_reason_counts: dict[str, int] = {}
    seen_sequences: set[int] = set()
    stop_reason = None
    last_sequence = None
    process.cpu_percent(interval=None)

    while time.monotonic() - started < seconds:
        wait = deadline - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        frame_started = time.monotonic()
        if frame_started - started >= seconds:
            break
        deadline, skipped, lag = _align_schedule_deadline(deadline, frame_started, period)
        missed_slots += skipped
        frame_started_perf = time.perf_counter()
        lag_ms = lag * 1000
        schedule_lags.append(lag_ms)

        decode_started = time.perf_counter()
        sampler_image = None
        try:
            import numpy as np
            sampler_image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception as exc:  # noqa: BLE001 - keep local report value-free
            stop_reason = f"測試影格解碼失敗：{type(exc).__name__}。"
            break
        decoded = time.perf_counter()
        if sampler_image is None:
            stop_reason = "本地測試影格無法解碼。"
            break
        try:
            model.predict(source=sampler_image, imgsz=size, conf=.45)
        except Exception as exc:  # noqa: BLE001 - no raw third-party paths or payloads in report
            stop_reason = f"推論階段失敗：{type(exc).__name__}。"
            break
        finished = time.perf_counter()
        stage_values.setdefault("jpeg_decode_ms", []).append((decoded - decode_started) * 1000)
        for name, value in model.last_timings.items():
            if type(value) in (int, float) and math.isfinite(value):
                stage_values.setdefault(name, []).append(float(value))
        stage_values.setdefault("isolated_total_ms", []).append((finished - frame_started_perf) * 1000)
        frames += 1

        if _nvidia_sample_required(model):
            sampler.sample(model.device)
            sample = sampler.snapshot()
            sequence = sample.get("sample_sequence")
            if sequence is not None and sequence != last_sequence:
                last_sequence = sequence
                if sample["status"] == "available" and sequence not in seen_sequences:
                    seen_sequences.add(sequence)
                    for metric, samples in telemetry_values.items():
                        value = sample.get(metric)
                        if type(value) in (int, float) and math.isfinite(value):
                            samples.append(float(value))
                    for reason in sample.get("throttle_active_reasons") or []:
                        throttle_reason_counts[reason] = throttle_reason_counts.get(reason, 0) + 1
                    cpu_values.append(process.cpu_percent() / (psutil.cpu_count() or 1))
                    rss_values.append(float(process.memory_info().rss))
        else:
            # Keep process CPU/RSS comparable for CPU and OpenVINO runs without
            # starting nvidia-smi, whose whole-card values would be unrelated.
            sample = {"status": "not_measured"}
            cpu_values.append(process.cpu_percent() / (psutil.cpu_count() or 1))
            rss_values.append(float(process.memory_info().rss))

        stop_reason = _check_safety(sample, require_temperature=model.device.startswith("cuda"))
        if stop_reason:
            break
        elapsed = time.monotonic() - started
        if elapsed >= seconds:
            break
        # Do not launch catch-up bursts after slow frames; keep a maximum-rate,
        # latest-frame-style schedule instead of hiding overload in a queue.
        deadline, skipped = _advance_schedule_deadline(deadline, time.monotonic(), period)
        missed_slots += skipped

    elapsed = max(time.monotonic() - started, .001)
    gpu_load = telemetry_values["gpu_kernel_busy_percent"]
    stage = {
        "input_size_long_edge_px": size,
        "target_fps": fps,
        "duration_seconds": round(elapsed, 2),
        "actual_fps": round(frames / elapsed, 3),
        "completed_frames": frames,
        "missed_schedule_slots": missed_slots,
        "schedule_lag_ms": _distribution(schedule_lags),
        "timings": {name: _distribution(values) for name, values in stage_values.items()},
        "cpu_worker_process_normalized_percent": _metric_distribution(cpu_values, "% of logical CPU capacity"),
        "process_rss_bytes": _metric_distribution(rss_values, "bytes"),
        "gpu_kernel_busy_percent": _metric_distribution(gpu_load, "% whole GPU device"),
        "memory_controller_busy_percent": _metric_distribution(
            telemetry_values["memory_controller_busy_percent"], "% whole GPU device"),
        "gpu_temperature_c": _metric_distribution(telemetry_values["temperature_c"], "°C"),
        "dedicated_gpu_memory_used_mib": _metric_distribution(
            telemetry_values["dedicated_memory_used_mib"], "MiB whole GPU device"),
        "gpu_free_memory_mib": _metric_distribution(telemetry_values["memory_free_mib"], "MiB"),
        "graphics_clock_mhz": _metric_distribution(telemetry_values["graphics_clock_mhz"], "MHz"),
        "memory_clock_mhz": _metric_distribution(telemetry_values["memory_clock_mhz"], "MHz"),
        "gpu_power_draw_w": _metric_distribution(telemetry_values["power_draw_w"], "W"),
        "observed_throttle_reasons": throttle_reason_counts,
        "soft_40_percent_load_target": "within_target" if gpu_load and statistics.fmean(gpu_load) <= 40
        else "above_target" if gpu_load else "not_measured",
        "stop_reason": stop_reason,
    }
    raw = {
        "schedule_lag_ms": schedule_lags,
        "timings": stage_values,
        "cpu_values": cpu_values,
        "rss_values": rss_values,
        "telemetry_values": telemetry_values,
    }
    return stage, raw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60,
                        help="每個尺寸的量測秒數（預設60；少於60秒不代表長期穩定性）")
    parser.add_argument("--fps", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--backend", choices=["cuda", "intel", "cpu"], default="cuda")
    parser.add_argument("--sizes", type=int, nargs="+", choices=SIZES, default=list(SIZES))
    parser.add_argument("--rounds", type=int, default=3,
                        help="尺寸交錯往返的輪數（預設3；每輪反轉尺寸順序）")
    parser.add_argument("--output", type=Path,
                        default=Path("data/diagnostics/gpu-staged-benchmark.json"))
    args = parser.parse_args()
    if not 5 <= args.seconds <= 3600:
        parser.error("每個尺寸的測試秒數須介於 5 到 3600。")
    if len(set(args.sizes)) != len(args.sizes):
        parser.error("尺寸不可重複。")
    if not 1 <= args.rounds <= 10:
        parser.error("交錯測試輪數須介於1到10。")

    model_path = default_model_file()
    export = verified_export(model_path)
    image_path = model_path.parent.parent / "tests" / "assets" / "cat.jpg"
    if export is None:
        parser.error("缺少已驗證 ONNX 匯出模型；未自動下載或修改模型。")
    image = cv2.imread(str(image_path))
    if image is None:
        parser.error("缺少公開本機測試影格 tests/assets/cat.jpg；未連接攝影機。")
    image = cv2.resize(image, (1920, 1080))
    ok, encoded = cv2.imencode(".jpg", image)
    if not ok:
        parser.error("無法建立隔離測試影格。")
    image_bytes = encoded.tobytes()

    expected_backend = {"cuda": "ONNX Runtime CUDA", "intel": "OpenVINO GPU", "cpu": "CPU"}[args.backend]
    model = AcceleratedYolo(export, expected_backend)
    if model.backend != expected_backend:
        parser.error("實際後端與指定後端不同；停止測試。")
    if args.backend == "cuda" and not model.provider_node_counts.get("CUDAExecutionProvider"):
        parser.error("沒有 CUDA kernel 節點證據；停止測試。")

    sampler = CachedGpuSampler()
    process = psutil.Process()
    report = {
        "schema_version": 1,
        "scope": "isolated_static_test_image_no_camera_no_formal_host",
        "requested_backend": expected_backend,
        "actual_backend": model.backend,
        "benchmark_process_id": os.getpid(),
        "device": model.device,
        "execution_devices": list(getattr(model, "execution_devices", [])),
        "provider_node_counts": model.provider_node_counts,
        "input_frame": "tests/assets/cat.jpg resized to 1920x1080; local public fixture only",
        "target_fps": args.fps,
        "rounds_requested": args.rounds,
        "round_order": _round_schedule(args.sizes, args.rounds),
        "soft_long_term_gpu_load_target_percent": 40,
        "gpu_telemetry_scope": (
            "NVIDIA whole-device telemetry; may include other processes" if model.device.startswith("cuda")
            else "Intel GPU utilization/memory not measured; no NVIDIA values are attributed to Intel"
            if model.backend == "OpenVINO GPU" else "GPU telemetry not applicable to CPU backend"
        ),
        "safety_stop": {"gpu_temperature_c_at_or_above": THERMAL_STOP_C,
                         "free_vram_mib_below": MINIMUM_FREE_VRAM_MIB},
        "stages": [],
        "aggregate_by_size": [],
        "mx250_acceptance": "not_checked",
        "long_term_acceptance": "not_checked",
        "overall_status": "in_progress",
    }
    try:
        initial_sample = _initial_telemetry(sampler, model)
        report["initial_gpu_telemetry"] = initial_sample
        reason = _check_safety(initial_sample, require_temperature=model.device.startswith("cuda"))
        if reason:
            report["overall_status"] = "stopped_safety_precondition"
            report["stop_reason"] = reason
        else:
            warmup = []
            runs_by_size: dict[int, list[tuple[dict, dict]]] = {size: [] for size in args.sizes}
            for round_index, size_order in enumerate(report["round_order"], start=1):
                for size in size_order:
                    before = _telemetry_ready(sampler, model.device, timeout=1.0)
                    reason = _check_safety(before, require_temperature=model.device.startswith("cuda"))
                    if reason:
                        report["overall_status"] = "stopped_safety_precondition"
                        report["stop_reason"] = reason
                        break
                    warmup_durations = []
                    for _ in range(3):
                        warmup_started = time.perf_counter()
                        model.predict(source=image, imgsz=size, conf=.45)
                        warmup_durations.append((time.perf_counter() - warmup_started) * 1000)
                        if _nvidia_sample_required(model):
                            sampler.sample(model.device)
                            warmup_sample = sampler.snapshot()
                        else:
                            warmup_sample = {"status": "not_measured"}
                        reason = _check_safety(warmup_sample, require_temperature=model.device.startswith("cuda"))
                        if reason:
                            break
                    warmup.append({"round": round_index, "input_size_long_edge_px": size,
                                   "samples": len(warmup_durations),
                                   "warmup_mean_ms": round(statistics.fmean(warmup_durations), 2)
                                   if warmup_durations else None})
                    report["warmup"] = warmup
                    if reason:
                        report["overall_status"] = "stopped_safety_precondition"
                        report["stop_reason"] = reason
                        break
                    stage, raw = _run_stage(model, sampler, image_bytes=image_bytes, size=size,
                                            fps=args.fps, seconds=args.seconds, process=process)
                    stage["round"] = round_index
                    report["stages"].append(stage)
                    runs_by_size[size].append((stage, raw))
                    if stage["stop_reason"]:
                        report["overall_status"] = "stopped_safety_or_stage_error"
                        report["stop_reason"] = stage["stop_reason"]
                        break
                if report["overall_status"] != "in_progress":
                    break
            report["aggregate_by_size"] = [
                _aggregate_size_runs(size, args.fps, runs_by_size[size])
                for size in args.sizes if runs_by_size[size]
            ]
            if report["overall_status"] == "in_progress":
                report["overall_status"] = "completed_short_isolated_matrix"
    finally:
        sampler.close()

    report["hardware_acceptance_scope"] = (
        "isolated_target_gpu_short_test_only" if "MX250" in str(initial_sample.get("gpu_model"))
        else "isolated_non_target_gpu_only" if initial_sample.get("gpu_model")
        else "gpu_hardware_not_identified"
    )
    report["output_is_local_only"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "initial_gpu_telemetry"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
