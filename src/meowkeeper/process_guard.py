"""Cross-process locks and safe MeowKeeper process inspection.

The Windows launcher can be invoked by a shortcut, Task Scheduler, Tray, or
an older installation at the same time.  This module deliberately identifies
processes by repository path *and* command line before it reports or stops
anything; a generic ``python.exe`` is never a valid cleanup target.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    parent_pid: int
    name: str
    executable: str | None
    command_line: tuple[str, ...]
    role: str | None
    local_address: str | None = None

    @property
    def command_text(self) -> str:
        return " ".join(self.command_line)

    @property
    def is_runtime_python(self) -> bool:
        executable = (self.executable or "").replace("/", "\\").lower()
        return executable.endswith(("\\.venv\\scripts\\python.exe", "\\.venv\\scripts\\pythonw.exe"))


def _normalise(value: object) -> str:
    return str(value or "").replace("/", "\\").rstrip("\\").lower()


def _root_in_text(root: Path, values: Iterable[str]) -> bool:
    root_text = _normalise(root)
    return bool(root_text) and root_text in _normalise(" ".join(values))


def _role_for_command(root: Path, command_line: Iterable[str], executable: str | None) -> str | None:
    values = tuple(str(item) for item in command_line)
    if not _root_in_text(root, values) and not _root_in_text(root, (executable or "" ,)):
        return None
    text = _normalise(" ".join(values))
    executable_text = _normalise(executable)
    # A command wrapper is still a repository-owned launcher when its full
    # command line points at this checkout.  Identifying the wrapper lets the
    # safe cleanup path remove abandoned ``cmd.exe``/PowerShell helpers while
    # leaving unrelated shells and Python processes untouched.
    if "start-meowkeeperplus" in text:
        return "launcher"
    if "run_host.py" in text:
        return "host"
    if "run_tray.py" in text:
        return "tray"
    if "launcher.py" in text:
        return "launcher"
    if "run_monitor.py" in text:
        return "monitor"
    if executable_text.endswith("\\go2rtc.exe") and _root_in_text(root, (executable or "" ,)):
        return "go2rtc"
    return None


def inspect_process(pid: int, root: Path) -> ProcessInfo | None:
    """Return a safe process identity record, or ``None`` if inaccessible."""

    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        process = psutil.Process(int(pid))
        try:
            executable = process.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            executable = None
        try:
            command_line = tuple(process.cmdline())
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            command_line = ()
        try:
            name = process.name()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            name = "unknown"
        try:
            parent_pid = int(process.ppid())
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            parent_pid = 0
    except (psutil.Error, OSError, ValueError):
        return None
    role = _role_for_command(root, command_line, executable)
    return ProcessInfo(int(pid), parent_pid, name, executable, command_line, role)


def iter_meowkeeper_processes(root: Path) -> list[ProcessInfo]:
    """Enumerate only processes whose identity contains this repository."""

    try:
        import psutil  # type: ignore[import-not-found]

        pids = psutil.pids()
    except (ImportError, OSError):
        return []
    records: list[ProcessInfo] = []
    for pid in pids:
        record = inspect_process(pid, root)
        if record is not None and record.role is not None:
            records.append(record)
    return records


def is_project_python(record: ProcessInfo, root: Path) -> bool:
    executable = _normalise(record.executable)
    venv = _normalise(root / ".venv" / "Scripts")
    return bool(executable and venv and executable.startswith(venv + "\\") and executable.endswith(("python.exe", "pythonw.exe")))


def redact_command_line(record: ProcessInfo) -> list[str]:
    """Return command arguments suitable for reports and logs."""

    result: list[str] = []
    url_credentials = re.compile(r"(://)([^/@\s]+):([^/@\s]+)@")
    for index, value in enumerate(record.command_line):
        text = str(value)
        text = url_credentials.sub(r"\1***:***@", text)
        if any(flag in text.lower() for flag in ("token=", "password=", "secret=", "stream-key")):
            text = re.sub(r"(=|\s+)[^\s]+$", r"\1***", text)
        result.append(text)
    return result


def port_owner(port: int, host: str = "127.0.0.1") -> list[ProcessInfo]:
    """Return process records owning a listening TCP port when available."""

    try:
        import psutil  # type: ignore[import-not-found]

        connections = psutil.net_connections(kind="tcp")
    except (ImportError, OSError):
        return []
    owners: list[ProcessInfo] = []
    for connection in connections:
        if connection.status != getattr(psutil, "CONN_LISTEN", "LISTEN") or not connection.laddr:
            continue
        address = connection.laddr
        if int(getattr(address, "port", 0)) != int(port):
            continue
        address_host = str(getattr(address, "ip", ""))
        if host not in {"0.0.0.0", "::", ""} and address_host not in {host, "0.0.0.0", "::"}:
            continue
        pid = connection.pid
        if pid:
            # The caller must map the PID to a repository with inspect_process;
            # this function intentionally returns raw owner PIDs only through
            # a temporary generic record when a root cannot yet be known.
            try:
                process = psutil.Process(pid)
                executable = process.exe()
                command_line = tuple(process.cmdline())
                parent_pid = int(process.ppid())
                name = process.name()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue
            owners.append(ProcessInfo(int(pid), parent_pid, name, executable, command_line, None, address_host))
    return owners


def _descendants(records: list[ProcessInfo], roots: set[int]) -> set[int]:
    result = set(roots)
    changed = True
    while changed:
        changed = False
        for record in records:
            if record.pid not in result and record.parent_pid in result:
                result.add(record.pid)
                changed = True
    return result


def safe_stop_legacy_processes(root: Path, *, keep_pids: set[int] | None = None) -> list[dict[str, Any]]:
    """Stop only old MeowKeeper processes using a non-project Python.

    A process is a candidate only after its executable and command line have
    both identified it as a MeowKeeper role.  Descendants of a legacy launcher
    are included so a stale mixed-runtime process tree cannot remain.  Official
    go2rtc is deliberately excluded and can be reused by the new Host.
    """

    keep = set(keep_pids or set())
    records = iter_meowkeeper_processes(root)
    legacy_roots = {
        record.pid
        for record in records
        if record.pid not in keep
        and record.role in {"launcher", "host", "tray"}
        and not is_project_python(record, root)
    }
    related = _descendants(records, legacy_roots)
    candidates = [
        record
        for record in records
        if record.pid in related
        and record.pid not in keep
        and record.role in {"launcher", "host", "tray"}
    ]
    # Stop children before their parent and never issue a generic name-based
    # taskkill.  If identity changes while stopping, skip it.
    candidates.sort(key=lambda item: item.parent_pid, reverse=True)
    result: list[dict[str, Any]] = []
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return result
    for record in candidates:
        if record.pid == os.getpid():
            continue
        current = inspect_process(record.pid, root)
        if current is None or current.role != record.role:
            continue
        if is_project_python(current, root) and current.pid not in _descendants(records, legacy_roots):
            continue
        try:
            process = psutil.Process(current.pid)
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
            result.append({"pid": current.pid, "role": current.role, "executable": current.executable or ""})
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.TimeoutExpired, OSError):
            result.append({"pid": current.pid, "role": current.role, "error": "cannot_stop"})
    return result


def safe_stop_identified_processes(
    root: Path,
    *,
    include_go2rtc: bool = False,
    keep_pids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Stop only repository processes identified by role and command line."""

    keep = set(keep_pids or set())
    records = iter_meowkeeper_processes(root)
    roles = {"launcher", "host", "tray"}
    if include_go2rtc:
        roles.add("go2rtc")
    candidates = [record for record in records if record.pid not in keep and record.role in roles]
    # Descendants first: consumers and Host release their ports before the
    # launcher/helper is asked to exit.  No generic process-name termination.
    def depth(record: ProcessInfo) -> int:
        seen: set[int] = set()
        parent = record.parent_pid
        result = 0
        by_pid = {item.pid: item for item in records}
        while parent in by_pid and parent not in seen:
            seen.add(parent)
            result += 1
            parent = by_pid[parent].parent_pid
        return result

    candidates.sort(key=depth, reverse=True)
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return []
    result: list[dict[str, Any]] = []
    for record in candidates:
        if record.pid == os.getpid():
            continue
        current = inspect_process(record.pid, root)
        if current is None or current.role != record.role:
            continue
        try:
            process = psutil.Process(current.pid)
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
            result.append({"pid": current.pid, "role": current.role, "executable": current.executable or "", "status": "stopped"})
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.TimeoutExpired, OSError) as exc:
            result.append({"pid": current.pid, "role": current.role, "error": type(exc).__name__})
    return result


class CrossProcessLock:
    """Windows named mutex plus a diagnostic PID sidecar."""

    def __init__(self, name: str, *, root: Path | None = None) -> None:
        if not name or any(char in name for char in "\\/\0"):
            raise ValueError("單一實例名稱格式不正確。")
        self.name = name
        self.root = (root or Path.cwd()).resolve()
        self._handle: int | None = None
        self._file = None
        self.previous_metadata_check: dict[str, Any] | None = None
        base = self.root / "data" / "locks"
        self.metadata_path = base / f"{name}.json"

    @property
    def acquired(self) -> bool:
        return self._handle is not None or self._file is not None

    def _write_metadata(self) -> None:
        payload = {
            "lock_name": self.name,
            "role": self.name.removeprefix("MeowKeeperPlus").lower() or "unknown",
            "pid": os.getpid(),
            "executable": str(Path(os.path.abspath(os.sys.executable))),
            "command_line": [str(item) for item in os.sys.argv[:2]],
            "created_at": time.time(),
        }
        try:
            self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.metadata_path.with_suffix(self.metadata_path.suffix + f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(temporary, self.metadata_path)
        except OSError:
            # The named mutex remains authoritative when AppData/repository
            # metadata is protected by a different Windows account.
            return

    def _check_previous_metadata(self) -> None:
        try:
            previous = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            previous_pid = int(previous.get("pid", -1))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if previous_pid == os.getpid() or previous_pid < 1:
            return
        record = inspect_process(previous_pid, self.root)
        stored_executable = _normalise(previous.get("executable"))
        live_executable = _normalise(record.executable) if record else ""
        stored_role = str(previous.get("role") or "").lower()
        self.previous_metadata_check = {
            "pid": previous_pid,
            "process_alive": record is not None,
            "executable_verified": bool(record and stored_executable and stored_executable == live_executable),
            "command_line_verified": bool(
                record
                and record.role is not None
                and (not stored_role or stored_role == record.role.lower())
            ),
        }

    def acquire(self) -> bool:
        if self.acquired:
            return True
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            handle = kernel32.CreateMutexW(None, True, f"Local\\{self.name}")
            if not handle:
                return False
            if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)
                return False
            self._handle = int(handle)
            self._check_previous_metadata()
            self._write_metadata()
            return True
        try:
            import fcntl

            self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.metadata_path.open("a+", encoding="utf-8")
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._check_previous_metadata()
            self._write_metadata()
            return True
        except (FileExistsError, BlockingIOError, OSError):
            if self._file is not None:
                self._file.close()
                self._file = None
            return False

    def release(self) -> None:
        if self._handle is not None and os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.ReleaseMutex(self._handle)
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._file is not None:
            try:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            self._file.close()
            self._file = None
        try:
            payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            if int(payload.get("pid", -1)) == os.getpid():
                self.metadata_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    def __enter__(self) -> CrossProcessLock:  # noqa: PYI034
        if not self.acquire():
            raise RuntimeError(f"{self.name} 已經在執行。")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
