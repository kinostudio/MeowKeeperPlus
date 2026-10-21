"""Safely move persistent files from an installed App\\data into Data."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any

IGNORED_TOP_LEVEL = {"cache", "locks", "install-history"}
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


def _top_level_entries(source: Path) -> list[Path]:
    entries: list[Path] = []
    for entry in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
        if entry.name.casefold() in IGNORED_TOP_LEVEL:
            continue
        if _is_reparse_point(entry):
            raise ValueError("舊資料包含連結項目；為避免移動資料夾外內容，已停止。")
        entries.append(entry)
    return entries


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
    entries = _top_level_entries(source)
    legacy_config = _legacy_config_path(legacy_config_arg, source)
    if not entries and legacy_config is None:
        return {"status": "skipped", "moved_entries": 0, "reason": "no_persistent_files"}

    existing = {entry.name.casefold(): entry for entry in destination.iterdir()}
    collisions = [entry.name for entry in entries if entry.name.casefold() in existing]
    if legacy_config is not None and "config.json" in existing:
        collisions.append("config.json")
    if collisions:
        raise ValueError(
            f"新舊資料目錄有 {len(collisions)} 個同名項目；沒有覆寫任何資料，請先備份並合併。"
        )

    plan = [entry.name for entry in entries]
    journal = {
        "schema_version": JOURNAL_SCHEMA,
        "source": str(source),
        "destination": str(destination),
        "planned_entries": plan,
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
    return {"status": "passed", "moved_entries": len(moved), "reason": "legacy_app_data_migrated"}


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
    parser.add_argument("--mode", choices=("move", "rollback"), required=True)
    parser.add_argument("--source")
    parser.add_argument("--destination")
    parser.add_argument("--legacy-config", help="舊 App 根目錄中的 config.json；僅在目的地沒有同名檔案時移動。")
    parser.add_argument("--journal", required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode == "move":
            if not args.source or not args.destination:
                raise ValueError("資料遷移來源或目的地未提供。")
            result = move_legacy_data(args.source, args.destination, args.journal, args.legacy_config)
        else:
            result = rollback_data(args.source, args.destination, Path(args.journal))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "message_zh_tw": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
