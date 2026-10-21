"""完整關閉的程序邊界；永不依通用程序名稱批次終止。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .client import TrayApiError


def owned_process(info: dict[str, Any], root: Path) -> bool:
    """只有精確 runtime／入口，或此包 vendor 的媒體程序屬於此安裝。"""
    executable = Path(info.get("exe") or "").resolve()
    arguments = info.get("cmdline") or []
    runtimes = {
        (root / "runtime/python/python.exe").resolve(),
        (root / "runtime/python/pythonw.exe").resolve(),
        (root / ".venv/Scripts/python.exe").resolve(),
        (root / ".venv/Scripts/pythonw.exe").resolve(),
    }
    if executable in runtimes:
        entries = {(root / "tools" / name).resolve() for name in (
            "run_host.py", "run_tray.py", "launcher.py",
        )}
        return any(Path(str(arg)).is_absolute() and Path(str(arg)).resolve() in entries for arg in arguments[1:])
    vendor = (root / "vendor").resolve()
    return vendor in executable.parents and executable.name.lower() in {"go2rtc.exe", "ffmpeg.exe", "ffprobe.exe"}


def close_owned_processes(root: Path, *, exclude_pid: int | None = None, grace_seconds: float = 0) -> dict[str, Any]:
    """完整讀取再動作；拒絕存取或 PID 重用時安全失敗。"""
    import psutil

    root = root.resolve()
    own_pid = os.getpid()
    candidates: dict[int, tuple[Any, float, str, tuple[str, ...]]] = {}
    for process in psutil.process_iter():
        if process.pid in {own_pid, exclude_pid}:
            continue
        try:
            info = process.as_dict(attrs=["exe", "cmdline"], ad_value=None)
            if not owned_process(info, root):
                continue
            candidates[process.pid] = (process, process.create_time(), info["exe"], tuple(info["cmdline"] or ()))
            # Multiprocessing AI children may not carry run_host.py in argv.
            for child in process.children(recursive=True):
                if child.pid in {own_pid, exclude_pid}:
                    continue
                child_exe = child.exe()
                command = tuple(child.cmdline())
                base_python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
                verified_python_child = Path(child_exe).resolve() == base_python and any(
                    str(root).lower() in str(arg).lower() or "multiprocessing.spawn" in str(arg)
                    for arg in command
                )
                if root in Path(child_exe).resolve().parents or verified_python_child:
                    candidates[child.pid] = (child, child.create_time(), child_exe, command)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            raise TrayApiError("無法完整核對本程式程序；請以管理員權限完整關閉。") from exc
    # Stop Host/launcher first so it cannot create replacement children.
    def priority(item: tuple[Any, float, str, tuple[str, ...]]) -> int:
        names = {Path(arg).name.lower() for arg in item[3]}
        return 0 if names & {"run_host.py", "launcher.py"} else (2 if "run_tray.py" in names else 1)
    records = sorted(candidates.values(), key=priority)
    stopped = []
    for process, created, executable, command in records:
        try:
            if grace_seconds and priority((process, created, executable, command)) == 0:
                try:
                    process.wait(timeout=grace_seconds)
                    continue
                except psutil.TimeoutExpired:
                    pass
            if (process.create_time() != created or process.exe() != executable
                    or tuple(process.cmdline()) != command):
                raise TrayApiError("程序識別已改變；未終止不確定的 PID。")
            process.terminate()
            stopped.append(process)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            raise TrayApiError("權限不足，仍有喵喵守護者+ 程序未關閉。") from exc
    _, remaining = psutil.wait_procs(stopped, timeout=15)
    if remaining:
        raise TrayApiError("本程式程序未在時限內結束；Tray 保留供查看狀態。")
    return {"status": "closed", "stopped_count": len(stopped)}
