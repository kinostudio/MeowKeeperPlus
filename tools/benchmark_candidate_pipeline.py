"""Measure the real single-source Host analysis path on an isolated data root.

This candidate-only probe uses RepositoryCoreController, the existing go2rtc
RTSP stream, and the normal FFmpeg -> latest-frame reader -> V0.2/YOLO -> event
path. Telegram delivery is deliberately disabled. It never reads production
SQLite/configuration or writes camera frames outside the supplied output root.
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction + 0.999999) - 1))
    return round(float(ordered[index]), 2)


def _numeric_summary(values: list[float]) -> dict[str, Any]:
    return {
        "samples": len(values),
        "average": round(statistics.fmean(values), 2) if values else None,
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": round(max(values), 2) if values else None,
    }


def _acceptance_failures(
    *,
    first_frame_elapsed: float | None,
    first_inference_elapsed: float | None,
    host_state: str | None,
    source_status: str | None,
    source_connections: int | None,
    actual_backend: str | None,
    actual_device: str | None,
    measured_fps: float | None,
    queue_metrics: dict[str, Any] | None,
    worker: dict[str, Any] | None,
    device_requested: str,
) -> list[str]:
    """Require real completed inference, not just a configured backend label."""
    expected_backend = "ONNX Runtime CUDA" if device_requested == "onnx:cuda" else "OpenVINO GPU"
    expected_device = "cuda:0" if device_requested == "onnx:cuda" else "intel:gpu"
    failures: list[str] = []
    if first_frame_elapsed is None:
        failures.append("no_source_frame")
    if first_inference_elapsed is None:
        failures.append("no_completed_inference_before_startup_deadline")
    if host_state == "error":
        failures.append("host_error")
    if source_status != "running" or not isinstance(source_connections, int) or source_connections < 1:
        failures.append("source_not_active_at_end")
    if actual_backend != expected_backend or actual_device != expected_device:
        failures.append("requested_accelerator_not_active_at_end")
    completed = int((queue_metrics or {}).get("completed") or 0)
    if completed < 3:
        failures.append("fewer_than_three_completed_inferences")
    if (not isinstance(measured_fps, (int, float)) or not math.isfinite(float(measured_fps))
            or measured_fps <= 0):
        failures.append("no_completed_analysis_fps")
    if device_requested == "onnx:cuda":
        node_counts = (worker or {}).get("provider_node_counts") or {}
        if int(node_counts.get("CUDAExecutionProvider") or 0) <= 0:
            failures.append("no_cuda_execution_node_evidence")
    return failures


def _json_safe(value: Any) -> Any:
    """Keep reports valid JSON even when optional telemetry contains NaN/Inf."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _candidate_preflight_failures(candidate_root: Path) -> list[str]:
    """Reject incomplete source snapshots before opening a live camera stream."""
    required = (Path("configs") / "botsort_reid.yaml",)
    return [str(relative) for relative in required
            if not (candidate_root / relative).is_file()]


def _measurement_snapshot(controller: Any) -> dict[str, Any]:
    state = controller.get_metrics()
    metrics = state.get("metrics", {})
    ai = metrics.get("ai", {})
    runtime_analyzer = getattr(controller, "_adaptive_analyzer", None)
    try:
        live_adaptive = runtime_analyzer.snapshot() if callable(getattr(runtime_analyzer, "snapshot", None)) else None
    except Exception:  # noqa: BLE001 - diagnostics cannot affect the candidate Host
        live_adaptive = None
    adaptive = live_adaptive or ai.get("adaptive") or {}
    worker = adaptive.get("worker")
    queue_metrics = adaptive.get("host_scheduler_queue_metrics")
    measured_fps = adaptive.get("measured_fps")
    if measured_fps is None:
        # CoreController publishes the analyzer's trailing-window FPS at the
        # top-level AI metrics. AdaptiveAnalyzer.snapshot currently uses this
        # metric for its bottleneck decision but does not include it in its
        # own returned policy dictionary.
        measured_fps = ai.get("fps")
    source = metrics.get("source", {})
    reader = ai.get("frame_reader") or {}
    return {
        "host_state": state.get("status"),
        "source": {
            "status": source.get("status"),
            "resolution": source.get("resolution"),
            "fps": source.get("fps"),
            "connections": source.get("connections"),
            "last_frame_at": source.get("last_frame_at"),
        },
        "ffmpeg": {
            "status": metrics.get("ffmpeg", {}).get("status"),
            "restart_count": metrics.get("ffmpeg", {}).get("restart_count"),
        },
        "ai": {
            "backend": getattr(runtime_analyzer, "inference_backend", None) or ai.get("backend"),
            "device": getattr(runtime_analyzer, "device", None) or ai.get("device"),
            "input_size": adaptive.get("effective_input_size", ai.get("inference_input_size")),
            "fps_cap": adaptive.get("effective_fps", adaptive.get("fps")),
            "measured_fps": measured_fps,
            "latency_average_ms": adaptive.get("latency_average_ms"),
            "latency_p50_ms": adaptive.get("latency_p50_ms"),
            "latency_p95_ms": adaptive.get("latency_p95_ms"),
            "frame_budget_ms": adaptive.get("frame_budget_ms"),
            "currently_over_budget": adaptive.get("currently_over_budget"),
            "bottleneck_status": adaptive.get("bottleneck_status"),
            "worker_cpu_percent": adaptive.get("worker_cpu_percent"),
            "gpu_telemetry_status": adaptive.get("gpu_telemetry_status"),
            "gpu_sampling": adaptive.get("gpu_sampling"),
            "host_scheduler_timings": adaptive.get("host_scheduler_timings"),
            "host_scheduler_queue_metrics": adaptive.get("host_scheduler_queue_metrics"),
            "host_adapter_stage_timings": adaptive.get("host_adapter_stage_timings"),
            "v02_pipeline_stage_timings": adaptive.get("v02_pipeline_stage_timings"),
            "stage_timing_contract": adaptive.get("stage_timing_contract"),
            "backend_warning": adaptive.get("backend_warning"),
            "inference_completed_count": (queue_metrics or {}).get("completed"),
            "worker_ready": worker.get("ready") if isinstance(worker, dict) else None,
            "worker": worker,
        },
        "frame_reader": reader,
        "event_count": state.get("event_count"),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    candidate_root = Path(__file__).resolve().parents[1]
    missing_candidate_files = _candidate_preflight_failures(candidate_root)
    if missing_candidate_files:
        raise RuntimeError(
            "候選完整管線缺少必要檔案，未啟動攝影機測試："
            + "、".join(missing_candidate_files)
        )

    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    local_appdata = output_root / "localappdata"
    local_appdata.mkdir(parents=True, exist_ok=True)
    config_dirs = {
        "ultralytics": local_appdata / "MeowKeeperPlus" / "ultralytics",
        "matplotlib": local_appdata / "MeowKeeperPlus" / "matplotlib",
        "openvino_cache": output_root / "openvino_cache",
    }
    for directory in config_dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-check"
        probe.write_text("candidate-only", encoding="utf-8")
        probe.unlink()
    os.environ["LOCALAPPDATA"] = str(local_appdata)
    os.environ["YOLO_CONFIG_DIR"] = str(config_dirs["ultralytics"])
    os.environ["MPLCONFIGDIR"] = str(config_dirs["matplotlib"])
    os.environ["OV_CACHE_DIR"] = str(config_dirs["openvino_cache"])

    import psutil

    from meowkeeper.app.core_controller import RepositoryCoreController
    from meowkeeper.storage.database import SQLiteStore

    store = SQLiteStore(output_root / "candidate.sqlite3")
    log_counts: dict[str, int] = {}
    safe_log_codes: dict[str, int] = {}

    def count_log(message: str) -> None:
        category = "warning" if "⚠️" in message else "error" if "❌" in message else "info"
        log_counts[category] = log_counts.get(category, 0) + 1
        if "指定的加速後端" in message:
            code = "accelerator_fallback"
        elif "V0.2 尚未可用" in message:
            code = "v02_initialization_fallback"
        elif "主要影像推論失敗" in message:
            code = "primary_inference_failure"
        elif "AI 分析失敗" in message:
            code = "ai_analysis_failure"
        elif "worker" in message.casefold() and "逾時" in message:
            code = "inference_worker_timeout"
        else:
            match = re.search(r"(?:失敗|錯誤)：\s*([A-Za-z][A-Za-z0-9_]*)", message)
            code = f"exception_{match.group(1)}" if match else "other"
        safe_log_codes[code] = safe_log_codes.get(code, 0) + 1

    controller = RepositoryCoreController(
        store=store,
        source_url=args.source_url,
        model_path=str(args.model),
        ffmpeg_path=str(args.ffmpeg),
        ring_directory=output_root / "ring",
        event_directory=output_root / "events",
        preview_directory=output_root / "preview",
        retention_seconds=60,
        segment_seconds=1,
        maximum_size_mb=512,
        analysis_fps=5,
        activity_fps=5,
        analysis_width=args.input_size,
        inference_device=args.device,
        identity_frame_width=1280,
        preview_frame_width=1920,
        confidence_threshold=0.55,
        event_pre_seconds=5,
        event_post_seconds=10,
        cooldown_seconds=600,
        analyzer_mode="v02",
        telegram_enabled=False,
        logger=count_log,
    )

    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    root_process = psutil.Process(os.getpid())
    root_process.cpu_percent(interval=None)
    start_result = controller.start_monitor()
    start = time.monotonic()
    last_sample = 0.0
    snapshots: list[dict[str, Any]] = []
    first_frame_elapsed: float | None = None
    first_inference_elapsed: float | None = None
    stop_reason: str | None = None
    cpu_samples: list[float] = []
    rss_samples: list[float] = []
    try:
        startup_deadline = start + args.startup_timeout
        while time.monotonic() < startup_deadline:
            elapsed = time.monotonic() - start
            current = _measurement_snapshot(controller)
            if current["source"].get("last_frame_at") and first_frame_elapsed is None:
                first_frame_elapsed = round(elapsed, 3)
            if current["host_state"] == "error":
                break
            if first_frame_elapsed is not None and (current["ai"].get("inference_completed_count") or 0) >= 1:
                first_inference_elapsed = round(elapsed, 3)
                break
            worker = current["ai"].get("worker") or {}
            if worker.get("last_failure_type") in {"StartupTimeout", "WorkerRestartLimit"}:
                break
            time.sleep(0.25)
        measure_start = time.monotonic()
        if first_inference_elapsed is None and stop_reason is None:
            stop_reason = "啟動期限內未完成第一張 AI 推論；本次不計入效能樣本。"
        while first_inference_elapsed is not None and time.monotonic() - measure_start < args.seconds:
            elapsed = time.monotonic() - measure_start
            current = _measurement_snapshot(controller)
            telemetry = current["ai"].get("gpu_sampling") or {}
            temperature = telemetry.get("temperature_c")
            free_memory = telemetry.get("memory_free_mib")
            if args.device == "onnx:cuda" and temperature is not None and temperature >= 78:
                stop_reason = "安全停止：NVIDIA GPU 溫度達 78°C 預警值。"
                break
            if args.device == "onnx:cuda" and free_memory is not None and free_memory < 160:
                stop_reason = "安全停止：NVIDIA GPU 剩餘顯存低於 160 MiB。"
                break
            if elapsed - last_sample >= args.sample_interval:
                current["measurement_elapsed_seconds"] = round(elapsed, 2)
                tree = [root_process]
                try:
                    tree.extend(root_process.children(recursive=True))
                except (psutil.Error, OSError):
                    pass
                cpu_now = 0.0
                rss_now = 0
                for process in tree:
                    try:
                        cpu_now += process.cpu_percent(interval=None)
                        rss_now += process.memory_info().rss
                    except (psutil.Error, OSError):
                        continue
                cpu_samples.append(cpu_now / max(psutil.cpu_count() or 1, 1))
                rss_samples.append(float(rss_now))
                snapshots.append(current)
                last_sample = elapsed
            if current["host_state"] == "error":
                break
            time.sleep(0.25)
        final_snapshot = _measurement_snapshot(controller)
    finally:
        controller.stop_monitor(confirm=True)
        controller.close()
        store.close()

    ai_final = final_snapshot["ai"]
    expected_backend = "ONNX Runtime CUDA" if args.device == "onnx:cuda" else "OpenVINO GPU"
    expected_device = "cuda:0" if args.device == "onnx:cuda" else "intel:gpu"
    acceptance_failures = _acceptance_failures(
        first_frame_elapsed=first_frame_elapsed,
        first_inference_elapsed=first_inference_elapsed,
        host_state=final_snapshot["host_state"],
        source_status=final_snapshot["source"].get("status"),
        source_connections=final_snapshot["source"].get("connections"),
        actual_backend=ai_final.get("backend"),
        actual_device=ai_final.get("device"),
        measured_fps=ai_final.get("measured_fps"),
        queue_metrics=ai_final.get("host_scheduler_queue_metrics"),
        worker=ai_final.get("worker"),
        device_requested=args.device,
    )
    passed = not acceptance_failures
    report = {
        "status": ("passed" if passed and stop_reason is None else
                   "safety_stopped" if stop_reason and stop_reason.startswith("安全停止：") else "failed"),
        "stop_reason": stop_reason,
        "candidate": True,
        "mode": "live_source_single_ingest_v02",
        "device_requested": args.device,
        "expected_backend": expected_backend,
        "expected_device": expected_device,
        "source": "existing localhost go2rtc stream; source URL intentionally omitted",
        "model_sha256": __import__("hashlib").sha256(args.model.read_bytes()).hexdigest(),
        "model_name": args.model.name,
        "analysis_size_px": args.input_size,
        "analysis_fps_cap": 5,
        "analyzer_mode": "v02",
        "telegram_delivery": "disabled; no external messages sent",
        "started_at_utc": started_utc,
        "measurement_seconds": args.seconds,
        "candidate_process_tree_cpu_percent_of_logical_capacity": _numeric_summary(cpu_samples),
        "candidate_process_tree_rss_bytes": _numeric_summary(rss_samples),
        "first_frame_seconds": first_frame_elapsed,
        "first_inference_seconds": first_inference_elapsed,
        "start_result_status": start_result.get("status"),
        "acceptance_failures": acceptance_failures,
        "isolated_config_directories": {name: str(path) for name, path in config_dirs.items()},
        "samples": snapshots,
        "final": final_snapshot,
        "local_log_category_counts": log_counts,
        "safe_log_diagnostic_codes": safe_log_codes,
        "artifacts": {
            "database": "candidate.sqlite3",
            "ring_directory": "ring",
            "event_directory": "events",
            "preview_directory": "preview",
        },
    }
    report_path = output_root / "result.json"
    report_path.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return {"status": report["status"], "report_path": str(report_path),
            "backend": final_snapshot["ai"].get("backend"),
            "device": final_snapshot["ai"].get("device"),
            "fps": final_snapshot["ai"].get("measured_fps"),
            "latency_average_ms": final_snapshot["ai"].get("latency_average_ms"),
            "latency_p95_ms": final_snapshot["ai"].get("latency_p95_ms"),
            "bottleneck_status": final_snapshot["ai"].get("bottleneck_status"),
            "first_frame_seconds": first_frame_elapsed,
            "sample_count": len(snapshots),
            "event_count": final_snapshot.get("event_count"),
            "log_categories": log_counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--device", choices=("onnx:cuda", "intel:gpu"), required=True)
    parser.add_argument("--input-size", type=int, choices=(640, 960, 1280, 1920), default=960,
                        help="AI 輸入長邊；完整管線仍使用同一來源與 5 FPS 上限。")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--startup-timeout", type=int, default=150,
                        help="source + first completed inference deadline; cold accelerator startup is excluded from measurement duration.")
    parser.add_argument("--sample-interval", type=float, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 30 <= args.seconds <= 120 or not 10 <= args.startup_timeout <= 180:
        parser.error("--seconds must be 30..120 and --startup-timeout 10..180 to keep a bounded diagnostic run")
    if not args.model.is_file() or not args.ffmpeg.is_file():
        parser.error("the existing model and FFmpeg binary must already exist")
    if not args.source_url.startswith("rtsp://127.0.0.1:8554/"):
        parser.error("only an existing localhost go2rtc RTSP stream is allowed")
    missing_candidate_files = _candidate_preflight_failures(Path(__file__).resolve().parents[1])
    if missing_candidate_files:
        parser.error(
            "候選完整管線缺少必要檔案，未啟動攝影機測試："
            + "、".join(missing_candidate_files)
        )
    multiprocessing.freeze_support()
    print(json.dumps(run(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
