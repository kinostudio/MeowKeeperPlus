"""Resolve persistent data paths consistently for portable and installed runs."""

from __future__ import annotations

import os
from pathlib import Path

_NON_PERSISTENT_ROOTS = {"cache", "locks", "install-history", "logs", "updates", "tmp"}


def installed_app_root(root: str | Path) -> bool:
    """Return true only for the known ``MeowKeeperPlus\\App`` layout."""

    app = Path(root).resolve()
    return (
        app.name.casefold() == "app"
        and app.parent.name.casefold() == "meowkeeperplus"
        and (app / "build_manifest.json").is_file()
    )


def _contains_files(path: Path) -> bool:
    """Conservatively detect existing data without following directory links."""

    if not path.is_dir():
        return False
    pending = [(path, True)]
    try:
        while pending:
            current, is_root = pending.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    if is_root and entry.name.casefold() in _NON_PERSISTENT_ROOTS:
                        continue
                    if entry.is_symlink():
                        # A link may point to user data; never treat it as empty.
                        return True
                    if entry.is_file(follow_symlinks=False):
                        return True
                    if entry.is_dir(follow_symlinks=False):
                        pending.append((Path(entry.path), False))
    except OSError:
        # An unreadable directory must not be mistaken for a blank destination.
        return True
    return False


def default_data_root(app_root: str | Path) -> Path:
    """Choose one persistent root, retaining legacy installed data if needed.

    New installs use ``<install>\\Data``. If that directory has no data but
    the prior relative-path launch left user data under ``App\\data``, keep
    using that existing root until an installer migration can move it safely.
    Portable and repository runs continue to use ``<root>\\data``.
    """

    root = Path(app_root).resolve()
    if not installed_app_root(root):
        return root / "data"
    install_root = root.parent
    canonical = install_root / "Data"
    legacy = root / "data"
    if _contains_files(canonical):
        return canonical
    if _contains_files(legacy):
        return legacy
    return canonical


def default_dashboard_config_path(app_root: str | Path, data_root: str | Path | None = None) -> Path:
    """Prefer the selected data root while preserving an existing dev config."""

    root = Path(app_root).resolve()
    data = Path(data_root).resolve() if data_root is not None else default_data_root(root)
    preferred = data / "config.json"
    if preferred.is_file():
        return preferred
    legacy = root / "config.json"
    if legacy.is_file():
        return legacy
    return preferred


def active_data_root_from_status(app_root: str | Path, status: object) -> Path | None:
    """Return the data root proven by the running Host's status snapshot.

    Installed launch tasks may still carry a legacy ``App\\data`` argument
    until a verified upgrade migrates it. Tray actions must follow the live
    Host instead of independently guessing from which folders happen to exist.
    Only known package data roots are accepted; arbitrary paths from a status
    payload are rejected.
    """

    root = Path(app_root).resolve()
    if not isinstance(status, dict):
        return None
    metrics = status.get("metrics")
    disk = metrics.get("disk") if isinstance(metrics, dict) else None
    raw_events = disk.get("path") if isinstance(disk, dict) else None
    if not isinstance(raw_events, str) or not raw_events.strip():
        return None

    try:
        events_path = Path(raw_events).resolve()
        if not events_path.is_absolute() or events_path.name.casefold() != "events":
            return None
        candidate = events_path.parent
        if installed_app_root(root):
            allowed = (root.parent / "Data", root / "data")
        else:
            allowed = (root / "data",)
        if any(candidate == item.resolve() for item in allowed):
            return candidate
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def default_install_root(app_root: str | Path) -> Path:
    root = Path(app_root).resolve()
    return root.parent if installed_app_root(root) else root
