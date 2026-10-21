"""唯讀檢查 Windows、Python、硬體、推論 Provider 與產品效能基線。"""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from meowkeeper.domain import PerformanceProfile, get_performance_profile

try:
    from .probe_common import ProbeItem, ProbeReport, ProbeStatus, emit_report, run_command
except ImportError:
    from probe_common import ProbeItem, ProbeReport, ProbeStatus, emit_report, run_command


def _windows_memory() -> dict[str, Any] | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    gib = 1024**3
    return {
        "total_gib": round(status.ullTotalPhys / gib, 2),
        "available_gib": round(status.ullAvailPhys / gib, 2),
        "load_percent": int(status.dwMemoryLoad),
    }


def _parse_nvidia_gpu(output: str) -> dict[str, Any] | None:
    first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
    parts = [part.strip() for part in first_line.split(",")]
    if len(parts) < 3:
        return None
    try:
        memory_total_mib = int(parts[-1])
    except ValueError:
        return None
    return {
        "name": ",".join(parts[:-2]).strip(),
        "driver_version": parts[-2],
        "memory_total_mib": memory_total_mib,
    }


def assess_memory(total_ram_gib: float | None, profile: PerformanceProfile) -> ProbeItem:
    if total_ram_gib is None:
        return ProbeItem(
            "profile_memory",
            ProbeStatus.NOT_CHECKED,
            "無法取得系統記憶體，尚未判定是否符合產品最低基線。",
            {"profile": profile.name, "minimum_ram_gib": profile.minimum_ram_gib},
        )
    if total_ram_gib < profile.minimum_ram_gib:
        return ProbeItem(
            "profile_memory",
            ProbeStatus.WARNING,
            f"系統記憶體低於 {profile.minimum_ram_gib:g} GiB 最低基線。",
            {
                "profile": profile.name,
                "detected_ram_gib": total_ram_gib,
                "minimum_ram_gib": profile.minimum_ram_gib,
                "recommended_ram_gib": profile.recommended_ram_gib,
            },
        )
    message = (
        "系統記憶體達到建議配置。"
        if total_ram_gib >= profile.recommended_ram_gib
        else "系統記憶體符合最低基線；16 GiB 可提供較佳系統餘裕。"
    )
    return ProbeItem(
        "profile_memory",
        ProbeStatus.OK,
        message,
        {
            "profile": profile.name,
            "detected_ram_gib": total_ram_gib,
            "minimum_ram_gib": profile.minimum_ram_gib,
            "recommended_ram_gib": profile.recommended_ram_gib,
        },
    )


def assess_gpu(gpu: dict[str, Any] | None, profile: PerformanceProfile) -> ProbeItem:
    if not gpu:
        return ProbeItem(
            "profile_gpu",
            ProbeStatus.NOT_CHECKED,
            "無法取得 NVIDIA GPU 資訊，尚未判定 MX250 最低基線。",
            {
                "profile": profile.name,
                "reference_gpu": profile.reference_gpu,
                "minimum_vram_mib": profile.minimum_vram_mib,
            },
        )

    name = str(gpu.get("name", ""))
    vram_mib = int(gpu.get("memory_total_mib", 0))
    reference_match = "mx250" in name.casefold()
    details = {
        "profile": profile.name,
        "detected_gpu": name,
        "detected_vram_mib": vram_mib,
        "reference_gpu": profile.reference_gpu,
        "minimum_vram_mib": profile.minimum_vram_mib,
        "reference_gpu_match": reference_match,
    }
    if vram_mib < profile.minimum_vram_mib:
        return ProbeItem(
            "profile_gpu",
            ProbeStatus.WARNING,
            "GPU VRAM 低於 MX250 profile 的 2 GiB 預算。",
            details,
        )
    if reference_match:
        return ProbeItem(
            "profile_gpu",
            ProbeStatus.OK,
            "已偵測 MX250 且 VRAM 符合最低基線；仍需實際模型效能測試。",
            details,
        )
    return ProbeItem(
        "profile_gpu",
        ProbeStatus.WARNING,
        "目前是非 MX250 開發機；資源檢查不能當成 MX250 效能證明。",
        details,
    )


def _collect_nvidia_gpu() -> tuple[ProbeItem, dict[str, Any] | None]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return (
            ProbeItem(
                "nvidia_gpu",
                ProbeStatus.NOT_CHECKED,
                "找不到 nvidia-smi；可能沒有 NVIDIA GPU，或驅動未加入 PATH。",
            ),
            None,
        )

    result = run_command(
        [
            nvidia_smi,
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    gpu = _parse_nvidia_gpu(result.stdout) if result.returncode == 0 else None
    if gpu:
        return (
            ProbeItem(
                "nvidia_gpu",
                ProbeStatus.OK,
                "已讀取 NVIDIA GPU、VRAM 與驅動資訊。",
                {"nvidia_smi": nvidia_smi, **gpu},
            ),
            gpu,
        )
    return (
        ProbeItem(
            "nvidia_gpu",
            ProbeStatus.WARNING,
            "找到 nvidia-smi，但無法解析 GPU 資訊；請檢查驅動。",
            {"nvidia_smi": nvidia_smi},
        ),
        None,
    )


def collect_report(profile_name: str = "mx250_minimum") -> ProbeReport:
    profile = get_performance_profile(profile_name)
    items: list[ProbeItem] = []

    py_ok = sys.version_info >= (3, 10)
    items.append(
        ProbeItem(
            "python",
            ProbeStatus.OK if py_ok else ProbeStatus.ERROR,
            "Python 版本符合 3.10 以上需求。" if py_ok else "Python 版本低於 3.10，請先升級。",
            {"version": platform.python_version(), "executable": sys.executable},
        )
    )

    is_windows = os.name == "nt"
    items.append(
        ProbeItem(
            "operating_system",
            ProbeStatus.OK if is_windows else ProbeStatus.WARNING,
            "已偵測 Windows 執行環境。"
            if is_windows
            else "目前不是 Windows；可執行開發測試，但不是正式目標環境。",
            {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "architecture": platform.machine(),
            },
        )
    )

    items.append(
        ProbeItem(
            "cpu",
            ProbeStatus.OK,
            "已讀取 CPU 基本資訊。",
            {
                "processor": platform.processor() or "未由作業系統提供",
                "logical_processors": os.cpu_count(),
            },
        )
    )

    memory = _windows_memory()
    total_ram_gib = float(memory["total_gib"]) if memory else None
    memory_status = (
        ProbeStatus.WARNING
        if total_ram_gib is not None and total_ram_gib < profile.minimum_ram_gib
        else ProbeStatus.OK
        if memory
        else ProbeStatus.NOT_CHECKED
    )
    items.append(
        ProbeItem(
            "memory",
            memory_status,
            "已讀取實體記憶體。"
            if memory
            else "此環境未提供 Windows 實體記憶體資訊。",
            memory or {},
        )
    )
    items.append(assess_memory(total_ram_gib, profile))

    disk = shutil.disk_usage(Path.cwd())
    gib = 1024**3
    items.append(
        ProbeItem(
            "disk",
            ProbeStatus.OK if disk.free >= 10 * gib else ProbeStatus.WARNING,
            "可用磁碟空間至少 10 GiB。"
            if disk.free >= 10 * gib
            else "可用磁碟空間低於 10 GiB，事件錄影前需先清理空間。",
            {
                "path": str(Path.cwd()),
                "total_gib": round(disk.total / gib, 2),
                "free_gib": round(disk.free / gib, 2),
            },
        )
    )

    gpu_item, gpu = _collect_nvidia_gpu()
    items.extend([gpu_item, assess_gpu(gpu, profile)])

    provider_details: dict[str, Any] = {
        "preferred_backend": profile.preferred_backend,
        "fallback_backend": profile.fallback_backend,
    }
    if importlib.util.find_spec("onnxruntime") is not None:
        try:
            import onnxruntime  # type: ignore[import-not-found]

            provider_details["onnxruntime"] = onnxruntime.get_available_providers()
        except (ImportError, OSError, RuntimeError) as exc:
            provider_details["onnxruntime_error"] = type(exc).__name__
    provider_details["openvino_installed"] = importlib.util.find_spec("openvino") is not None
    provider_details["torch_installed"] = importlib.util.find_spec("torch") is not None
    items.append(
        ProbeItem(
            "inference_providers",
            ProbeStatus.OK if provider_details.get("onnxruntime") else ProbeStatus.WARNING,
            "已列出 ONNX Runtime Provider。"
            if provider_details.get("onnxruntime")
            else "尚未確認 ONNX Runtime Provider；Phase 0 不會自動安裝大型套件。",
            provider_details,
        )
    )

    items.append(
        ProbeItem(
            "performance_profile",
            ProbeStatus.OK,
            "已載入 MX250 保守效能 profile；此項是設計目標，不是效能測試結果。",
            {
                "name": profile.name,
                "reference_gpu": profile.reference_gpu,
                "minimum_ram_gib": profile.minimum_ram_gib,
                "recommended_ram_gib": profile.recommended_ram_gib,
                "minimum_vram_mib": profile.minimum_vram_mib,
                "max_visual_models": profile.max_visual_models,
                "model_batch_size": profile.model_batch_size,
                "inference_input_size": profile.inference_input_size,
                "analysis_fps": profile.analysis_fps,
                "identity_sampling_fps": profile.identity_sampling_fps,
                "behavior_sampling_hz": profile.behavior_sampling_hz,
                "conditional_identity": profile.conditional_identity,
                "normal_yolo_fps": profile.normal_yolo_fps,
                "active_yolo_fps": profile.active_yolo_fps,
                "quiet_yolo_fps": profile.quiet_yolo_fps,
                "active_mode_seconds": profile.active_mode_seconds,
                "identity_recheck_interval_seconds": profile.identity_recheck_interval_seconds,
                "identity_max_pending_tasks": profile.identity_max_pending_tasks,
                "identity_timeout_seconds": profile.identity_timeout_seconds,
                "identity_minimum_crop_pixels": profile.identity_minimum_crop_pixels,
                "preferred_backend": profile.preferred_backend,
                "fallback_backend": profile.fallback_backend,
            },
        )
    )

    return ProbeReport("system", items)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="唯讀檢查本機系統與 AI 後端相容性。")
    parser.add_argument(
        "--profile",
        default="mx250_minimum",
        help="效能 profile 名稱，預設 mx250_minimum。",
    )
    parser.add_argument("--output", help="選擇性 JSON 報告輸出路徑。")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = collect_report(args.profile)
    except ValueError as exc:
        build_parser().error(str(exc))
    emit_report(report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
