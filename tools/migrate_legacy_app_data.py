"""Safely move persistent files from an installed App\\data into Data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

IGNORED_TOP_LEVEL = {"cache", "locks", "install-history"}
# Runtime-generated caches that may exist in both App\data and Data.  Only the
# exact leaves accepted by the signed delta baseline contract qualify; any
# other file inside them makes the whole entry persistent (fail-closed).
RUNTIME_CACHE_TOP_LEVEL = {"matplotlib", "ultralytics", "torch"}
MAX_RUNTIME_CACHE_DIRECTORIES = 16
JOURNAL_SCHEMA = 1


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & marker)


def _plain_directory(path: Path, label: str) -> Path:
    if _is_reparse_point(path):
        raise ValueError(f"{label} 是連結／重新解析點；為避免移動資料夾外內容，已停止。")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{label} 不是一般資料夾。")
    return resolved


def _runtime_leaf(file: Path, name: str) -> bool:
    """Exact R120 runtime-output contract; corrupt or oversized leaves are not caches."""
    from meowkeeper.app.delta_update import _runtime_generated

    try:
        return bool(_runtime_generated(file, "data/" + name))
    except (OSError, ValueError, UnicodeDecodeError):
        return False


def runtime_cache_only(entry: Path) -> bool:
    """True only for a known cache folder whose every item is a verified runtime leaf.

    These folders are rebuilt by the runtime itself.  They stay in the old App
    backup (never merged, overwritten or deleted) so a same-named Data folder is
    not a data conflict.  Unknown files, links, extra folders or invalid JSON
    keep the entry persistent, so the normal fail-closed collision rule applies.
    """
    top = entry.name.casefold()
    if top not in RUNTIME_CACHE_TOP_LEVEL or entry.name != top or _is_reparse_point(entry) or not entry.is_dir():
        return False
    directories = 0
    pending = [entry]
    while pending:
        current = pending.pop()
        for child in current.iterdir():
            if _is_reparse_point(child):
                return False
            relative = child.relative_to(entry.parent).as_posix()
            if child.is_dir():
                directories += 1
                allowed_directory = top == "torch" or relative == "ultralytics/Ultralytics"
                if not allowed_directory or directories > MAX_RUNTIME_CACHE_DIRECTORIES:
                    return False
                pending.append(child)
            elif not child.is_file() or top == "torch" or not _runtime_leaf(child, relative):
                return False
    return True


def _top_level_entries(source: Path) -> tuple[list[Path], list[Path]]:
    """Return persistent entries to move and runtime-only caches left in place."""
    entries: list[Path] = []
    retained: list[Path] = []
    for entry in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
        if entry.name.casefold() in IGNORED_TOP_LEVEL:
            continue
        if _is_reparse_point(entry):
            raise ValueError("舊資料包含連結項目；為避免移動資料夾外內容，已停止。")
        if runtime_cache_only(entry):
            retained.append(entry)
            continue
        entries.append(entry)
    return entries, retained


def _kind(path: Path) -> str:
    if _is_reparse_point(path):
        return "link"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def _describe(path: Path, *, include_hash: bool = False) -> dict[str, Any]:
    """Metadata only: names, sizes and optional content hash; contents never printed."""
    info: dict[str, Any] = {"kind": _kind(path), "files": 0, "bytes": 0}
    tree = hashlib.sha256()
    if info["kind"] == "file":
        files = [path]
    elif info["kind"] == "directory":
        files = sorted((item for item in path.rglob("*") if item.is_file() and not _is_reparse_point(item)),
                       key=lambda item: item.relative_to(path).as_posix())
    else:
        files = []
    for file in files:
        size = file.stat().st_size
        info["files"] += 1
        info["bytes"] += size
        if include_hash:
            leaf = hashlib.sha256()
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    leaf.update(chunk)
            relative = "." if file == path else file.relative_to(path).as_posix()
            tree.update(f"{relative}\0{size}\0{leaf.hexdigest()}\n".encode())
    if include_hash:
        info["sha256"] = tree.hexdigest()
    return info


def _collision_message(collisions: list[dict[str, Any]]) -> str:
    names = "、".join(f"{item['name']}（舊：{item['source_kind']}／新：{item['destination_kind']}）" for item in collisions)
    return (
        f"新舊資料目錄有 {len(collisions)} 個同名項目：{names}；沒有覆寫任何資料，"
        "舊 App 已完整保留，請先備份並合併。"
    )


def _assert_no_links(path: Path) -> None:
    pending = [path]
    while pending:
        current = pending.pop()
        with os.scandir(current) as children:
            for child in children:
                child_path = Path(child.path)
                if _is_reparse_point(child_path):
                    raise ValueError("待移動資料包含連結項目；為避免存取資料夾外內容，已停止。")
                if child.is_dir(follow_symlinks=False):
                    pending.append(child_path)


def _legacy_config_path(value: str | None, source: Path) -> Path | None:
    if value is None:
        return None
    candidate = Path(value)
    if candidate.name.casefold() != "config.json" or _is_reparse_point(candidate) or not candidate.is_file():
        raise ValueError("舊版設定檔不是安全的一般 config.json 檔案。")
    resolved = candidate.resolve(strict=True)
    if resolved != (source.parent / "config.json").resolve():
        raise ValueError("舊版設定檔不在已驗證的 App 根目錄。")
    return resolved


class MigrationConflict(ValueError):
    def __init__(self, message: str, collisions: list[dict[str, Any]], retained: list[str]) -> None:
        super().__init__(message)
        self.collisions = collisions
        self.retained = retained


def _write_journal(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def move_legacy_data(
    source_arg: str,
    destination_arg: str,
    journal_arg: str,
    legacy_config_arg: str | None = None,
) -> dict[str, Any]:
    source = _plain_directory(Path(source_arg), "舊資料來源")
    destination_path = Path(destination_arg)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        destination = _plain_directory(destination_path, "正式資料目錄")
    else:
        destination_path.mkdir()
        destination = destination_path.resolve()
    journal_path = Path(journal_arg).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("資料來源與目的地目錄重疊；已停止。")
    _assert_no_links(source)
    _assert_no_links(destination)
    entries, retained = _top_level_entries(source)
    retained_names = [entry.name for entry in retained]
    legacy_config = _legacy_config_path(legacy_config_arg, source)
    if not entries and legacy_config is None:
        return {"status": "skipped", "moved_entries": 0, "reason": "no_persistent_files",
                "retained_runtime_entries": retained_names}

    existing = {entry.name.casefold(): entry for entry in destination.iterdir()}
    collisions = [
        {"name": entry.name, "source_kind": _kind(entry), "destination_kind": _kind(existing[entry.name.casefold()])}
        for entry in entries if entry.name.casefold() in existing
    ]
    if legacy_config is not None and "config.json" in existing:
        collisions.append({"name": "config.json", "source_kind": "file",
                           "destination_kind": _kind(existing["config.json"])})
    if collisions:
        raise MigrationConflict(_collision_message(collisions), collisions, retained_names)

    plan = [entry.name for entry in entries]
    journal = {
        "schema_version": JOURNAL_SCHEMA,
        "source": str(source),
        "destination": str(destination),
        "planned_entries": plan,
        "retained_runtime_entries": retained_names,
        "legacy_config": str(legacy_config) if legacy_config is not None else None,
        "status": "prepared",
    }
    _write_journal(journal_path, journal)
    moved: list[str] = []
    try:
        for entry in entries:
            entry.rename(destination / entry.name)
            moved.append(entry.name)
            journal["status"] = "moving"
            journal["moved_entries"] = moved
            _write_journal(journal_path, journal)
        if legacy_config is not None:
            legacy_config.rename(destination / "config.json")
            journal["legacy_config_moved"] = True
            journal["status"] = "moving"
            _write_journal(journal_path, journal)
    except BaseException:
        rollback_data(source, destination, journal_path)
        raise
    journal["status"] = "completed"
    journal["moved_entries"] = moved
    _write_journal(journal_path, journal)
    return {"status": "passed", "moved_entries": len(moved), "reason": "legacy_app_data_migrated",
            "retained_runtime_entries": retained_names}


def inspect_legacy_data(source_arg: str, destination_arg: str, *, include_hash: bool = False,
                        legacy_config_arg: str | None = None) -> dict[str, Any]:
    """Read-only preflight: classify every top-level item and report collisions without moving anything."""
    source = _plain_directory(Path(source_arg), "舊資料來源")
    destination_path = Path(destination_arg)
    destination = _plain_directory(destination_path, "正式資料目錄") if destination_path.exists() else None
    existing = {entry.name.casefold(): entry for entry in destination.iterdir()} if destination else {}
    items = []
    for entry in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
        folded = entry.name.casefold()
        if folded in IGNORED_TOP_LEVEL:
            category = "ignored"
        elif _is_reparse_point(entry):
            category = "link_blocked"
        elif runtime_cache_only(entry):
            category = "runtime_cache_retained"
        else:
            category = "persistent"
        record: dict[str, Any] = {"name": entry.name, "category": category,
                                  "source": _describe(entry, include_hash=include_hash) if category != "link_blocked" else {"kind": "link"},
                                  "collision": folded in existing}
        if folded in existing:
            other = existing[folded]
            record["destination"] = {"kind": "link"} if _is_reparse_point(other) else _describe(other, include_hash=include_hash)
        items.append(record)
    if legacy_config_arg is not None and Path(legacy_config_arg).exists():
        config = Path(legacy_config_arg)
        record = {"name": "config.json", "category": "persistent", "source": {"kind": _kind(config)},
                  "collision": "config.json" in existing}
        if record["collision"]:
            record["destination"] = {"kind": _kind(existing["config.json"])}
        items.append(record)
    blocking = [item["name"] for item in items
                if item["category"] == "link_blocked" or (item["category"] == "persistent" and item["collision"])]
    return {"status": "blocked" if blocking else "ready", "blocking_entries": blocking, "items": items}


def residual_persistent_entries(source_arg: str) -> dict[str, Any]:
    """Names left in an old App\\data that are not ignored or verified runtime caches.

    The update worker deletes an old App backup only when this list is empty,
    so retained caches never force a backup to be kept forever and real data
    never disappears with it.
    """
    source = Path(source_arg)
    if not source.exists():
        return {"status": "passed", "persistent_entries": []}
    source = _plain_directory(source, "舊資料來源")
    persistent = []
    for entry in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
        if entry.name.casefold() in IGNORED_TOP_LEVEL:
            continue
        if _is_reparse_point(entry) or not runtime_cache_only(entry):
            persistent.append(entry.name)
    return {"status": "passed", "persistent_entries": persistent}


def rollback_data(source_arg: str | None, destination_arg: str | None, journal_path: Path) -> dict[str, Any]:
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != JOURNAL_SCHEMA:
        raise ValueError("資料遷移交易紀錄版本不符；沒有執行回復。")
    source = Path(source_arg or payload["source"]).resolve()
    destination = Path(destination_arg or payload["destination"]).resolve()
    if source != Path(payload["source"]).resolve() or destination != Path(payload["destination"]).resolve():
        raise ValueError("資料遷移回復路徑與原交易不符。")
    source.mkdir(parents=True, exist_ok=True)
    restored = 0
    for name in reversed(payload.get("planned_entries", [])):
        if not isinstance(name, str) or Path(name).name != name or name.casefold() in IGNORED_TOP_LEVEL:
            raise ValueError("資料遷移交易紀錄含有不安全項目名稱。")
        old = source / name
        new = destination / name
        if old.exists() and not new.exists():
            continue
        if old.exists() and new.exists():
            raise ValueError(f"回復時新舊資料都存在同名項目：{name}。")
        if not new.exists():
            raise ValueError(f"回復時找不到資料項目：{name}。")
        new.rename(old)
        restored += 1
    config_value = payload.get("legacy_config")
    if config_value is not None:
        config_source = Path(config_value).resolve()
        if config_source != source.parent / "config.json":
            raise ValueError("回復設定檔路徑不在原 App 根目錄。")
        old_config = config_source
        new_config = destination / "config.json"
        if old_config.exists() and not new_config.exists():
            pass
        elif old_config.exists() and new_config.exists():
            raise ValueError("回復時新舊設定檔同時存在；沒有覆寫任何資料。")
        elif new_config.exists():
            new_config.rename(old_config)
            restored += 1
        else:
            raise ValueError("回復時找不到舊版 Dashboard 設定檔。")
    payload["status"] = "rolled_back"
    payload["restored_entries"] = restored
    _write_journal(journal_path, payload)
    return {"status": "passed", "restored_entries": restored, "reason": "migration_rolled_back"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安全保留安裝版舊資料目錄。")
    parser.add_argument("--mode", choices=("move", "rollback", "inspect", "residual"), required=True)
    parser.add_argument("--source")
    parser.add_argument("--destination")
    parser.add_argument("--legacy-config", help="舊 App 根目錄中的 config.json；僅在目的地沒有同名檔案時移動。")
    parser.add_argument("--journal")
    parser.add_argument("--hash", action="store_true", help="inspect 模式另計算內容雜湊（只輸出雜湊，不輸出內容）。")
    args = parser.parse_args(argv)
    try:
        if args.mode in {"move", "rollback"} and not args.journal:
            raise ValueError("資料遷移交易紀錄路徑未提供。")
        if args.mode == "inspect":
            if not args.source or not args.destination:
                raise ValueError("資料檢查來源或目的地未提供。")
            result = inspect_legacy_data(args.source, args.destination, include_hash=args.hash,
                                         legacy_config_arg=args.legacy_config)
        elif args.mode == "residual":
            if not args.source:
                raise ValueError("殘留資料檢查來源未提供。")
            result = residual_persistent_entries(args.source)
        elif args.mode == "move":
            if not args.source or not args.destination:
                raise ValueError("資料遷移來源或目的地未提供。")
            result = move_legacy_data(args.source, args.destination, args.journal, args.legacy_config)
        else:
            result = rollback_data(args.source, args.destination, Path(args.journal))
        # inspect/residual are parsed by PowerShell through a code-page pipe;
        # ASCII escapes keep any folder name decodable there.
        print(json.dumps(result, ensure_ascii=args.mode in {"inspect", "residual"}))
        return 0
    except MigrationConflict as exc:
        print(json.dumps({"status": "failed", "error_type": "MigrationConflict", "message_zh_tw": str(exc),
                          "collisions": exc.collisions, "retained_runtime_entries": exc.retained}, ensure_ascii=True))
        return 2
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "message_zh_tw": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
