"""Local-only Tailscale device display aliases."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

_DEVICE_REF = re.compile(r"^ts_[0-9a-f]{16}$")


class TailscaleAliasStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._aliases: dict[str, str] = {}
        if self.path is not None:
            self._load()

    @staticmethod
    def _validate(device_ref: str, alias: str | None) -> tuple[str, str | None]:
        if not isinstance(device_ref, str) or _DEVICE_REF.fullmatch(device_ref) is None:
            raise ValueError("裝置識別格式不正確。")
        if alias is None:
            return device_ref, None
        if not isinstance(alias, str):
            raise TypeError("裝置別名必須是文字。")
        value = " ".join(alias.split())
        if len(value) > 40 or any(ord(character) < 32 for character in value):
            raise ValueError("別名最多 40 個字元，且不可包含控制字元。")
        return device_ref, value or None

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        values = raw.get("aliases") if isinstance(raw, dict) else None
        if not isinstance(values, dict):
            return
        for device_ref, alias in list(values.items())[:256]:
            try:
                key, value = self._validate(device_ref, alias)
            except ValueError:
                continue
            if value:
                self._aliases[key] = value

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._aliases)

    def set_alias(self, device_ref: str, alias: str | None) -> dict[str, str]:
        key, value = self._validate(device_ref, alias)
        with self._lock:
            updated = dict(self._aliases)
            if value is None:
                updated.pop(key, None)
            else:
                if key not in updated and len(updated) >= 256:
                    raise ValueError("本機裝置別名已達保存上限。")
                updated[key] = value
            if self.path is not None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = self.path.with_suffix(self.path.suffix + ".tmp")
                    temporary.write_text(
                        json.dumps({"schema_version": 1, "aliases": updated}, ensure_ascii=False, sort_keys=True),
                        encoding="utf-8",
                    )
                    temporary.replace(self.path)
                except OSError as exc:
                    raise OSError("裝置別名無法保存到本機資料目錄。") from exc
            self._aliases = updated
            return dict(updated)
