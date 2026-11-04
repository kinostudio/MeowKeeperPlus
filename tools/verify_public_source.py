"""Reject recognizable credentials in first-party source; never print matched values."""
from __future__ import annotations

import re
from pathlib import Path

PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{30,}"),
    re.compile(r"[0-9]{8,12}:AA[A-Za-z0-9_-]{30,}"),
    re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----"),
)
EXCLUDED = {".git", "runtime", "vendor", "models", "data", "dist", "build", "reports", ".venv", "__pycache__", ".codex", ".agents"}
SUFFIXES = {".py", ".ps1", ".cmd", ".json", ".md", ".js", ".html", ".toml", ".yaml", ".yml", ".pem"}


def verify_public_source(root: Path) -> int:
    pending, checked, findings = [root], 0, []
    while pending:
        for path in pending.pop().iterdir():
            if path.name in EXCLUDED:
                continue
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("公開原始碼含重新解析點，停止發布。")
            if path.is_dir():
                pending.append(path)
            elif path.suffix.lower() in SUFFIXES:
                checked += 1
                content = path.read_text(encoding="utf-8-sig")
                if any(pattern.search(content) for pattern in PATTERNS):
                    findings.append(path.relative_to(root).as_posix())
    if findings:
        raise ValueError("公開原始碼疑似包含憑證，停止發布：" + ", ".join(sorted(findings)))
    return checked


if __name__ == "__main__":
    print({"status": "passed", "files_checked": verify_public_source(Path(__file__).resolve().parents[1])})
