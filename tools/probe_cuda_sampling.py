"""Compare GPU sampling overhead on one saved frame; never opens a camera.

This measures an isolated detector on target hardware, not the full Host's
identity/event/Telegram pipeline. It must not be reported as full E2E FPS.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--mode", choices=("sync", "cached"), required=True)
    parser.add_argument("--fps", type=int, choices=(3, 5), default=3)
    args = parser.parse_args()
    if not 10 <= args.seconds <= 180:
        parser.error("測試秒數限10到180秒。")
    sys.path.insert(0, str(args.root / "src"))
    import cv2
    import psutil

    from meowkeeper.core.inference.accelerated_yolo import AcceleratedYolo, verified_export
    from meowkeeper.core.inference.gpu_telemetry import read_gpu_sample
    # A candidate cache implementation may be injected into this standalone
    # probe namespace without modifying the installed Host or its modules.
    sampler_class = globals().get("CandidateCachedGpuSampler")
    if args.mode == "cached" and sampler_class is None:
        from meowkeeper.core.inference.gpu_telemetry import CachedGpuSampler
        sampler_class = CachedGpuSampler
    sampler = sampler_class(reader=read_gpu_sample) if args.mode == "cached" else None
    image = cv2.imread(str(args.image))
    if image is None:
        raise ValueError("參考影格不存在或無法解碼。")
    scale = min(1, 1920 / image.shape[1])
    image = cv2.resize(image, (round(image.shape[1] * scale), round(image.shape[0] * scale)))
    ok, jpeg = cv2.imencode(".jpg", image)
    if not ok:
        raise ValueError("無法準備相同的測試影格。")
    model_path = verified_export(args.root / "models/yolo11n.pt")
    if model_path is None:
        raise ValueError("模型完整性未通過。")
    initial = read_gpu_sample("cuda:0")
    if initial[1] is None or initial[1] >= 75 or initial[2] is None or initial[2] < 512:
        raise ValueError("GPU 溫度／剩餘顯存無法確認安全，未開始負載測試。")
    model = AcceleratedYolo(model_path, "ONNX Runtime CUDA")
    for _ in range(3):
        model.predict(source=image, imgsz=640, conf=.55)
    print(json.dumps({"phase": "started", "mode": args.mode, "fps_cap": args.fps,
                      "provider_node_counts": model.provider_node_counts}), flush=True)
    process = psutil.Process()
    process.cpu_percent()
    latencies, queries, samples, count = [], [], [], 0
    started = time.monotonic()
    next_frame, next_sample = started, started
    stop_reason = None
    try:
        while time.monotonic() - started < args.seconds:
            now = time.monotonic()
            if now >= next_sample:
                sampling_at = time.perf_counter()
                value = sampler.sample("cuda:0") if sampler else read_gpu_sample("cuda:0")
                queries.append((time.perf_counter() - sampling_at) * 1000)
                samples.append({"load": value[0], "temperature": value[1], "free_vram_mib": value[2],
                                "cpu_percent": process.cpu_percent() / (psutil.cpu_count() or 1)})
                next_sample = time.monotonic() + 2
                if value[1] is not None and value[1] >= 80 or value[2] is not None and value[2] < 128:
                    stop_reason = "thermal_or_memory_pressure"
                    break
            delay = next_frame - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            frame_started = time.monotonic()
            decoded = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
            model.predict(source=decoded, imgsz=640, conf=.55)
            latencies.append((time.monotonic() - frame_started) * 1000)
            count += 1
            next_frame = max(frame_started + 1 / args.fps, time.monotonic())
    finally:
        if sampler:
            sampler.close()
    elapsed = time.monotonic() - started
    valid_loads = [row["load"] for row in samples if row["load"] is not None]
    valid_temps = [row["temperature"] for row in samples if row["temperature"] is not None]
    print(json.dumps({"phase": "finished", "scope": "isolated_fixed_camera_frame_detector_only",
                      "mode": args.mode, "fps_cap": args.fps, "seconds": elapsed, "frames": count,
                      "actual_fps": count / elapsed, "input_size": 640,
                      "inference_mean_ms": statistics.mean(latencies) if latencies else None,
                      "inference_p95_ms": sorted(latencies)[max(0, int(len(latencies) * .95) - 1)] if latencies else None,
                      "sampling_block_mean_ms": statistics.mean(queries) if queries else None,
                      "gpu_mean_percent": statistics.mean(valid_loads) if valid_loads else None,
                      "gpu_max_percent": max(valid_loads) if valid_loads else None,
                      "gpu_max_temperature_c": max(valid_temps) if valid_temps else None,
                      "cpu_mean_percent": statistics.mean(row["cpu_percent"] for row in samples) if samples else None,
                      "gpu_samples": len(valid_loads), "stop_reason": stop_reason,
                      "provider_node_counts": model.provider_node_counts,
                      "no_camera_upstream_opened": True,
                      "recording_and_telegram_e2e": "not_checked", "long_term": "not_checked"}), flush=True)


if __name__ == "__main__":
    main()
