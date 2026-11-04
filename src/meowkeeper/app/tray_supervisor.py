"""Bounded Tray readiness checks; never launch a GUI in SYSTEM session zero."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path


class TraySupervisor:
    def __init__(self, root: Path, lock_root: Path, logger, *, port: int = 8000):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Tray 連線埠無效。")
        self.base_url = f"http://127.0.0.1:{port}"
        self.root = root.resolve()
        self.lock_root = lock_root.resolve()
        self.logger = logger
        self._lock = threading.Lock()
        self._launch_lock = threading.Lock()
        self._closed = threading.Event()
        self.state = {"status": "not_checked"}

    def ensure(self):
        if self._closed.is_set() or os.name != "nt" or not self._lock.acquire(blocking=False):
            return dict(self.state)
        self.state = {"status": "checking"}
        threading.Thread(target=self._check, name="meowkeeper-tray-check", daemon=True).start()
        return dict(self.state)

    def close(self):
        self._closed.set()
        with self._launch_lock:
            self.state = {"status": "closed"}

    def _present(self):
        import psutil

        expected = str(self.root / "tools" / "run_tray.py").casefold()
        runtimes = {str(self.root / "runtime" / "python" / name).casefold()
                    for name in ("python.exe", "pythonw.exe")}
        runtimes.add(str(Path(sys.executable).resolve()).casefold())
        for process in psutil.process_iter():
            try:
                arguments = process.cmdline()
                if "--base-url" in arguments:
                    index = arguments.index("--base-url") + 1
                    if index >= len(arguments) or arguments[index].rstrip("/") != self.base_url:
                        continue
                if "--lock-root" in arguments:
                    index = arguments.index("--lock-root") + 1
                    if index >= len(arguments) or Path(arguments[index]).resolve() != self.lock_root:
                        continue
                if (process.exe().casefold() in runtimes
                        and any(arg.casefold() == expected for arg in arguments)
                        and self._interactive(process.pid)):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    @staticmethod
    def _interactive(pid):
        import ctypes

        session = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session)) and session.value)

    def _launch(self):
        installed = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "MeowKeeperPlus" / "App"
        if self.root == installed.resolve():
            scheduler = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "schtasks.exe")
            task = "MeowKeeperPlus Tray"
            result = subprocess.run([scheduler, "/Query", "/TN", task, "/XML"],
                                    capture_output=True, timeout=8, check=True, shell=False)
            xml = ET.fromstring(result.stdout)
            ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
            actions = xml.findall("t:Actions/t:Exec", ns)
            if (len(actions) != 1
                    or xml.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns) != "InteractiveToken"):
                raise ValueError("Tray 工作不是互動使用者工作。")
            action = actions[0]
            expected_args = f'"{self.root / "tools" / "run_tray.py"}" --background --lock-root "{self.lock_root}"'
            if ((action.findtext("t:Command", namespaces=ns) or "").casefold()
                    != str(self.root / "runtime" / "python" / "pythonw.exe").casefold()
                    or (action.findtext("t:Arguments", namespaces=ns) or "").casefold() != expected_args.casefold()):
                raise ValueError("Tray 工作不屬於目前安裝。")
            subprocess.run([scheduler, "/Run", "/TN", task], capture_output=True,
                           timeout=8, check=True, shell=False)
        else:
            if not self._interactive(os.getpid()):
                raise ValueError("沒有可使用的互動桌面，未在背景工作階段啟動 Tray。")
            runtime = Path(sys.executable).with_name("pythonw.exe")
            if not runtime.is_file():
                raise ValueError("缺少 Tray 圖形執行環境。")
            subprocess.Popen([str(runtime), str(self.root / "tools" / "run_tray.py"),
                              "--background", "--lock-root", str(self.lock_root), "--base-url", self.base_url],
                             cwd=self.root, shell=False, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def _check(self):
        try:
            if self._closed.is_set():
                return
            if not self._present():
                with self._launch_lock:
                    if self._closed.is_set():
                        return
                    self._launch()
                for _ in range(20):
                    if self._closed.is_set():
                        return
                    if self._present():
                        break
                    time.sleep(0.25)
                else:
                    raise RuntimeError("Tray 尚未確認啟動；請確認 Windows 使用者已登入桌面。")
            self.state = {"status": "running"}
            self.logger("ℹ️ 已確認 Tray 程序正在執行；未建立第二個核心或攝影機連線。")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, ET.ParseError):
            self.state = {"status": "unconfirmed"}
            self.logger("⚠️ Tray 尚未確認啟動；核心繼續運作，請由開始功能表開啟喵喵守護者+。")
        finally:
            self._lock.release()
