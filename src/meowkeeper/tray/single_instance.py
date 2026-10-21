"""Tray single-instance compatibility wrapper."""

from __future__ import annotations

from pathlib import Path

from meowkeeper.process_guard import CrossProcessLock


class SingleInstanceLock(CrossProcessLock):
    """Keep the historical Tray API while using the shared process guard."""

    def __init__(self, name: str = "MeowKeeperPlusTray", *, root: str | Path | None = None) -> None:
        super().__init__(name, root=Path(root).resolve() if root else None)
