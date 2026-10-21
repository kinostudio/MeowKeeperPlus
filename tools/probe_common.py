"""Phase 0 探針共用的結果格式、安全遮罩與子程序工具。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit


class ProbeStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    NOT_CHECKED = "not_checked"


@dataclass
class ProbeItem:
    name: str
    status: ProbeStatus
    message_zh_tw: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProbeReport:
    probe: str
    items: list[ProbeItem]
    schema_version: int = 1
    generated_at_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for item in payload["items"]:
            item["status"] = str(item["status"].value)
        return payload


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    unavailable: bool = False


def run_command(command: Sequence[str], timeout_seconds: float = 10.0) -> CommandResult:
    """執行固定參數陣列，不經 shell，並將缺少程式與逾時轉成結果。"""

    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return CommandResult(None, "", "找不到外部程式。", unavailable=True)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(None, stdout, stderr, timed_out=True)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def redact_secret(text: str, secret: str | None) -> str:
    if not secret:
        return text
    return text.replace(secret, "***")


def redact_rtsp_uri(uri: str) -> str:
    """保留非敏感連線資訊，遮罩 RTSP userinfo。"""

    try:
        parts = urlsplit(uri)
    except ValueError:
        return "rtsp://***"
    if parts.scheme.lower() not in {"rtsp", "rtsps"}:
        return "<非 RTSP URI>"

    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        parsed_port = parts.port
    except ValueError:
        return "rtsp://***"
    port = f":{parsed_port}" if parsed_port else ""
    netloc = f"***:***@{hostname}{port}" if parts.username is not None else f"{hostname}{port}"
    safe = SplitResult(parts.scheme, netloc, parts.path, parts.query, "")
    return urlunsplit(safe)


def sanitize_text(text: str, secrets: Sequence[str] = ()) -> str:
    cleaned = text
    for secret in secrets:
        cleaned = redact_secret(cleaned, secret)
    cleaned = re.sub(
        r"rtsps?://[^/\s:@]+:[^@\s/]+@",
        "rtsp://***:***@",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def emit_json(payload: dict[str, Any], output: str | None) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(serialized)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized + "\n", encoding="utf-8")


def emit_report(report: ProbeReport, output: str | None) -> None:
    emit_json(report.as_dict(), output)
