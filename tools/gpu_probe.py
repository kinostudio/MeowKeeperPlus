"""唯讀 GPU／推論後端檢測；不安裝套件、不下載模型。"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _prepare_runtime_dirs() -> None:
    runtime_root = ROOT / "data" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    ultralytics_root = runtime_root / "ultralytics"
    matplotlib_root = runtime_root / "matplotlib"
    ultralytics_root.mkdir(parents=True, exist_ok=True)
    matplotlib_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(ultralytics_root))
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_root))

try:
    from .probe_common import ProbeItem, ProbeReport, ProbeStatus, emit_report, run_command
except ImportError:
    from probe_common import ProbeItem, ProbeReport, ProbeStatus, emit_report, run_command


def _nvidia_info() -> ProbeItem:
    import shutil

    executable = shutil.which("nvidia-smi")
    if not executable:
        return ProbeItem(
            "nvidia_gpu",
            ProbeStatus.NOT_CHECKED,
            "找不到 nvidia-smi；尚未確認 NVIDIA GPU、VRAM 與 compute capability。",
        )
    result = run_command(
        [
            executable,
            "--query-gpu=name,driver_version,memory.total,compute_cap",
            "--format=csv,noheader,nounits",
        ],
        timeout_seconds=10.0,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return ProbeItem(
            "nvidia_gpu",
            ProbeStatus.WARNING,
            "nvidia-smi 無法回傳 GPU 資訊；不把目前環境宣稱為 MX250。",
            {"path": executable, "stderr": result.stderr[-300:]},
        )
    parts = [item.strip() for item in result.stdout.splitlines()[0].split(",")]
    details: dict[str, Any] = {"path": executable, "raw": result.stdout.splitlines()[0]}
    if len(parts) >= 4:
        details.update(
            {
                "name": parts[0],
                "driver_version": parts[1],
                "vram_total_mib": _to_int(parts[2]),
                "compute_capability": parts[3],
            }
        )
    return ProbeItem("nvidia_gpu", ProbeStatus.OK, "已讀取 NVIDIA GPU 基本資訊。", details)


def _torch_info(*, run_smoke: bool) -> ProbeItem:
    if importlib.util.find_spec("torch") is None:
        return ProbeItem(
            "pytorch_cuda",
            ProbeStatus.NOT_CHECKED,
            "尚未安裝 PyTorch；不自動安裝 CUDA runtime。",
        )
    try:
        import torch  # type: ignore[import-not-found]

        details: dict[str, Any] = {
            "torch_version": torch.__version__,
            "torch_cuda_version": torch.version.cuda,
            "cuda_available": bool(torch.cuda.is_available()),
            "device_count": int(torch.cuda.device_count()),
        }
        if torch.cuda.is_available():
            details["devices"] = [torch.cuda.get_device_name(index) for index in range(torch.cuda.device_count())]
            if run_smoke:
                started = time.perf_counter()
                tensor = torch.ones((1,), device="cuda") + 1
                details["cuda_tensor_smoke"] = bool(float(tensor.item()) == 2.0)
                details["cuda_tensor_latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
            else:
                details["cuda_tensor_smoke"] = "not_checked"
        else:
            details["cuda_tensor_smoke"] = "not_checked"
        return ProbeItem(
            "pytorch_cuda",
            ProbeStatus.OK if details["cuda_available"] else ProbeStatus.WARNING,
            "PyTorch CUDA 可用；仍需實際 YOLO/DINO 測試。"
            if details["cuda_available"]
            else "目前 PyTorch 不可使用 CUDA，將以 CPU fallback。",
            details,
        )
    except (ImportError, OSError, RuntimeError, AttributeError) as exc:
        return ProbeItem(
            "pytorch_cuda",
            ProbeStatus.WARNING,
            f"PyTorch CUDA 檢測失敗：{type(exc).__name__}；可降級 CPU。",
            {"error": type(exc).__name__},
        )


def _onnx_info() -> ProbeItem:
    if importlib.util.find_spec("onnxruntime") is None:
        return ProbeItem(
            "onnxruntime",
            ProbeStatus.NOT_CHECKED,
            "尚未安裝 ONNX Runtime；不自動安裝推論套件。",
        )
    try:
        import onnxruntime  # type: ignore[import-not-found]

        providers = list(onnxruntime.get_available_providers())
        return ProbeItem(
            "onnxruntime",
            ProbeStatus.OK if providers else ProbeStatus.WARNING,
            "已讀取 ONNX Runtime providers；provider 存在不等於本次模型已使用 CUDA。",
            {"available_providers": providers},
        )
    except (ImportError, OSError, RuntimeError) as exc:
        return ProbeItem(
            "onnxruntime",
            ProbeStatus.WARNING,
            f"ONNX Runtime 檢測失敗：{type(exc).__name__}。",
            {"error": type(exc).__name__},
        )


def _yolo_test(image_path: Path | None) -> ProbeItem:
    model_path = ROOT / "models" / "yolo11n.pt"
    image_path = image_path or ROOT / "tests" / "assets" / "cat.jpg"
    if not model_path.is_file() or not image_path.is_file():
        return ProbeItem("yolo_cuda_inference", ProbeStatus.NOT_CHECKED, "缺少本地模型或測試影像，尚未執行 YOLO 實測。")
    try:
        _prepare_runtime_dirs()
        import torch  # type: ignore[import-not-found]
        from ultralytics import YOLO  # type: ignore[import-not-found]

        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        started = time.perf_counter()
        result = YOLO(str(model_path))(str(image_path), device=device, verbose=False)[0]
        latency = (time.perf_counter() - started) * 1000.0
        classes = [int(value) for value in result.boxes.cls.tolist()] if result.boxes is not None else []
        return ProbeItem(
            "yolo_cuda_inference",
            ProbeStatus.OK,
            f"YOLO 實際推論完成，後端為 {'PyTorch CUDA' if device.startswith('cuda') else 'CPU'}。",
            {
                "device": "PyTorch CUDA" if device.startswith("cuda") else "CPU",
                "latency_ms": round(latency, 3),
                "cat_detected": 15 in classes,
                "model_path": str(model_path),
            },
        )
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        return ProbeItem(
            "yolo_cuda_inference",
            ProbeStatus.WARNING,
            f"YOLO 實際推論未完成：{type(exc).__name__}；可降級 CPU 或先修復 runtime。",
            {"error": type(exc).__name__},
        )


def _dino_test(image_path: Path | None) -> ProbeItem:
    image_path = image_path or ROOT / "tests" / "assets" / "cat.jpg"
    if not image_path.is_file():
        return ProbeItem("dinov2_cuda_inference", ProbeStatus.NOT_CHECKED, "缺少測試影像，尚未執行 DINOv2 實測。")
    try:
        from meowkeeper.core.identity import DinoV2Embedder

        embedder = DinoV2Embedder()
        started = time.perf_counter()
        vector = embedder.embed(image_path.read_bytes())
        latency = (time.perf_counter() - started) * 1000.0
        return ProbeItem(
            "dinov2_cuda_inference",
            ProbeStatus.OK,
            f"DINOv2 實際推論完成，後端為 {embedder.backend_name}。",
            {
                "device": embedder.backend_name,
                "latency_ms": round(latency, 3),
                "vector_shape": list(getattr(vector, "shape", ())),
            },
        )
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        return ProbeItem(
            "dinov2_cuda_inference",
            ProbeStatus.WARNING,
            f"DINOv2 實際推論未完成：{type(exc).__name__}；身分可延後或回傳 unknown。",
            {"error": type(exc).__name__},
        )


def collect_report(*, run_model_tests: bool = False, image_path: Path | None = None) -> ProbeReport:
    items = [_nvidia_info(), _torch_info(run_smoke=run_model_tests), _onnx_info()]
    if run_model_tests:
        items.extend([_yolo_test(image_path), _dino_test(image_path)])
    else:
        items.extend(
            [
                ProbeItem("yolo_cuda_inference", ProbeStatus.NOT_CHECKED, "未指定 --run-model-tests，避免 Phase 0 自動載入大型模型。"),
                ProbeItem("dinov2_cuda_inference", ProbeStatus.NOT_CHECKED, "未指定 --run-model-tests，避免 Phase 0 自動載入大型模型。"),
            ]
        )
    return ProbeReport("gpu", items)


def _to_int(value: str) -> int | str:
    try:
        return int(float(value))
    except ValueError:
        return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="唯讀檢查 GPU、CUDA、ONNX Runtime 與可選模型實測。")
    parser.add_argument("--run-model-tests", action="store_true", help="明確要求載入本地 YOLO/DINO 模型實測。")
    parser.add_argument("--image", type=Path, help="模型實測用本地影像；預設使用 tests/assets/cat.jpg。")
    parser.add_argument("--output", help="選擇性 JSON 報告輸出路徑。")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    emit_report(collect_report(run_model_tests=args.run_model_tests, image_path=args.image), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
