"""唯讀硬體資訊與可用感測器；缺少感測器不以零取代。"""
from __future__ import annotations

import csv
import platform
import shutil
import subprocess
from functools import lru_cache


def gpu_sample() -> dict:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {}
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, shell=False, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        rows = list(csv.reader(result.stdout.splitlines()))
        if result.returncode != 0 or len(rows) != 1 or len(rows[0]) != 3:
            return {}  # 多 GPU 不任意把其中一張視為整台 GPU。
        name, load, temperature = rows[0]
        return {"gpu_name": name.strip(), "system_gpu_percent": float(load), "gpu_temperature_c": float(temperature)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


@lru_cache(maxsize=1)
def hardware() -> dict:
    import psutil
    cpu = platform.processor() or "未提供"
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    gpu_names = []
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}") as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    child = winreg.EnumKey(key, i)
                    if not child.isdigit():
                        continue
                    try:
                        with winreg.OpenKey(key, child) as adapter:
                            gpu_names.append(str(winreg.QueryValueEx(adapter, "DriverDesc")[0]))
                    except OSError:
                        pass
        except OSError:
            pass
    return {"computer_name": platform.node(), "cpu_model": cpu, "logical_cores": psutil.cpu_count(),
            "ram_bytes": psutil.virtual_memory().total,
            "gpu_name": "、".join(dict.fromkeys(gpu_names)) or gpu_sample().get("gpu_name", "未提供（感測器不可用）")}


def cpu_temperature() -> float | None:
    import psutil
    provider = getattr(psutil, "sensors_temperatures", None)
    if not callable(provider):
        return None
    try:
        values = [sensor.current for group, sensors in provider().items()
                  if group.lower() in {"coretemp", "k10temp", "cpu_thermal"} for sensor in sensors]
        return max(values) if values else None
    except (OSError, RuntimeError):
        return None


def format_system_report(history: dict, *, info: dict | None = None) -> str:
    info = info or hardware()
    lines = ["🖥 電腦與硬體", f"電腦：{info['computer_name']}", f"CPU：{info['cpu_model']}",
             f"邏輯核心：{info['logical_cores']}", f"RAM：{info['ram_bytes'] / 10**9:.1f} GB", f"GPU：{info['gpu_name']}", "",
             f"近一小時：{history.get('sample_count', 0)} 筆，涵蓋 {history.get('coverage_seconds', 0) / 60:.0f} 分鐘（不足 60 分鐘為部分資料）",
             "用量／溫度順序：最近採樣（每 60 秒）／一小時平均／採樣峰值"]
    for label, field, unit in (("CPU", "system_cpu_percent", "%"), ("RAM", "system_ram_percent", "%"),
                               ("GPU", "system_gpu_percent", "%"), ("CPU 溫度", "cpu_temperature_c", "°C"),
                               ("GPU 溫度", "gpu_temperature_c", "°C")):
        values = history.get("statistics", {}).get(field, {})
        def display(kind, values=values, unit=unit):
            value = values.get(kind)
            return f"{value:.1f}{unit}" if value is not None else "未提供"
        lines.extend((f"{label}", f"  目前：{display('current')}",
                      f"  平均：{display('average')} · 峰值：{display('peak')}", ""))
    io = history.get("disk_io", {})
    def amount(key):
        value = io.get(key)
        return f"{value / 10**6:.1f} MB" if value is not None else "未提供"
    lines.extend(("💾 近一小時磁碟 I/O（已採樣區間）", f"讀取：{amount('read_bytes')}",
                  f"寫入：{amount('write_bytes')}", "這是整台電腦流量，不是程式磁碟占用。"))
    lines.append("感測器缺失不代表 0；GPU 數據為可辨識的 NVIDIA 裝置，Intel 用量／Windows CPU 溫度目前可能無法取得。")
    return "\n".join(lines)
