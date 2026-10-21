"""Explicit, pinned PTZ build selection; the official binary stays untouched."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from .manifest import sha256_file

PTZ_SHA256 = "3F64D4C5144350DC816ABB4DE41BD686E248A9528EB2825A00CD8FC65EFEA0F3"
PTZ_VERSION = "experimental-miss-ptz-feedback-e746789"


class VerifiedPtzBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.executable = root / "vendor" / "go2rtc" / "ptz-feedback" / "go2rtc.exe"

    def ensure_installed(self, *, force: bool = False) -> SimpleNamespace:
        # Never download a fork or silently fall back to a different backend.
        if force:
            raise ValueError("PTZ 實驗版只能由已驗證發布包更新，不能線上覆寫。")
        manifest = json.loads(self.executable.with_name("manifest.json").read_text(encoding="utf-8"))
        if manifest.get("binary_sha256") != PTZ_SHA256 or sha256_file(self.executable).upper() != PTZ_SHA256:
            raise ValueError("PTZ 實驗執行檔校驗失敗；未啟動，官方執行檔保持不變。")
        return SimpleNamespace(release_tag=PTZ_VERSION)


def ptz_backend_selected(root: Path) -> bool:
    selection = root / "vendor" / "go2rtc" / "backend.json"
    if not selection.is_file():
        return False
    value = json.loads(selection.read_text(encoding="utf-8"))
    if value.get("backend") not in {"official", "ptz-feedback"}:
        raise ValueError("go2rtc 後端設定無效。")
    return value["backend"] == "ptz-feedback"
