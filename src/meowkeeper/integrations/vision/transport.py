"""限時 HTTP 子程序；秘密只走 stdin，不走參數、檔案或日誌。"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil

from meowkeeper.core.event_semantics import ProviderReply, VisionFailure

ProviderError = VisionFailure


def _stop_owned_request(process: subprocess.Popen, worker: Path) -> None:
    """Windows venv launcher 可有一個 Python 子程序；按 handle/血緣與腳本核對。"""
    try:
        root = psutil.Process(process.pid)
        children = root.children(recursive=True)
        for child in reversed(children):
            try:
                if any(Path(argument).resolve() == worker.resolve() for argument in child.cmdline()[1:]):
                    child.kill()  # psutil 同時檢查 PID 是否已重用。
                    child.wait(timeout=2)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired, OSError):
                continue
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    if process.poll() is None:
        process.kill()


def request_json(endpoint: str, headers: dict[str, str], body: dict[str, Any],
                 *, timeout_seconds: float, method: str = "POST") -> dict[str, Any]:
    if not 0 < timeout_seconds <= 30:
        raise ValueError("視覺服務逾時設定不正確。")
    if method not in {"POST", "GET"}:
        raise ValueError("視覺服務只允許圖片分析或唯讀能力檢查。")
    payload = json.dumps({"endpoint": endpoint, "headers": headers, "body": body,
                          "timeout": timeout_seconds, "method":method}, ensure_ascii=False).encode("utf-8")
    if len(payload) > 8 * 1024 * 1024:
        raise ProviderError("bad_response")
    # 不繼承程序環境中的 API Key；不搜尋 PATH、不開啟 console。
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "USERPROFILE",
                                      "LOCALAPPDATA", "APPDATA", "COMSPEC", "PATHEXT", "HOMEDRIVE",
                                      "HOMEPATH", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
                                      "SYSTEMDRIVE"}}
    worker = Path(__file__).with_name("http_worker.py")
    process = subprocess.Popen(
        [sys.executable, "-I", str(worker)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        shell=False, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        output, _ = process.communicate(input=payload, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _stop_owned_request(process, worker)
        partial, _ = process.communicate(timeout=2)
        sent = b'{"event":"request_dispatched"}\n' in partial.splitlines(keepends=True)
        raise ProviderError("timeout", request_dispatched=True if sent else None) from None
    finally:
        if process.poll() is None:
            _stop_owned_request(process, worker)
            process.wait(timeout=2)
    marker_seen = b'{"event":"request_dispatched"}\n' in output.splitlines(keepends=True)
    if process.returncode != 0:
        raise ProviderError("unavailable", request_dispatched=True if marker_seen else None)
    try:
        lines = output.splitlines()
        envelope = json.loads(lines[-1])
        if not isinstance(envelope, dict):
            raise TypeError
        status = envelope.get("http_status")
        category = envelope.get("error")
        if category:
            allowed = {"auth_error", "rate_limited", "unavailable", "bad_response", "timeout"}
            raise ProviderError(category if category in allowed else "bad_response",
                                status if type(status) is int else None,
                                envelope.get("retry_after_seconds"),
                                request_dispatched=envelope.get("request_dispatched"),
                                api_elapsed_ms=envelope.get("api_elapsed_ms"), http_timings=envelope.get("http_timings"))
        response = envelope["response"]
        if not isinstance(response, dict):
            raise TypeError
        return ProviderReply(response, request_dispatched=envelope.get("request_dispatched"), http_status=status,
                             api_elapsed_ms=envelope.get("api_elapsed_ms"), http_timings=envelope.get("http_timings"))
    except (ValueError, KeyError, TypeError, IndexError):
        raise ProviderError("bad_response", request_dispatched=True if marker_seen else None) from None
