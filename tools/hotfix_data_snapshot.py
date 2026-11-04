"""Protected update snapshots; callers must stop the Host before capture/restore."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

EXCLUDED = {"updates", "cache"}


def inventory(root: Path) -> dict[str, str]:
    result = {}
    pending = [root]
    while pending:
        path = pending.pop()
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("資料快照不接受重新解析點。")
        if path.is_dir():
            pending.extend(path.iterdir())
        else:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[path.relative_to(root).as_posix()] = digest.hexdigest()
    return result


def capture(data: Path, backup: Path) -> None:
    if backup.exists():
        raise ValueError("資料快照已存在，拒絕覆寫。")
    paths = []
    for path in data.iterdir():
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("資料根目錄不接受重新解析點。")
        if path.name not in EXCLUDED:
            paths.append(path)
            if path.is_dir():
                inventory(path)
    needed = sum(p.stat().st_size for item in paths for p in
                 (item.rglob("*") if item.is_dir() else [item]) if p.is_file())
    if shutil.disk_usage(backup.parent).free < needed * 2 + 512 * 1024**2:
        raise ValueError("空間不足以保存資料快照及失敗現場，停止更新。")
    backup.mkdir()
    for path in paths:
        destination = backup / path.name
        if path.is_dir():
            shutil.copytree(path, destination)
        elif path.suffix == ".db":
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(destination)) as target:
                source.backup(target)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("資料庫快照校驗失敗。")
        elif not path.name.endswith((".db-wal", ".db-shm")):
            shutil.copy2(path, destination)
    manifest = inventory(backup)
    (backup.parent / "data-snapshot.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")


def restore(data: Path, backup: Path) -> None:
    manifest = json.loads((backup.parent / "data-snapshot.json").read_text(encoding="utf-8"))
    if inventory(backup) != manifest:
        raise ValueError("資料快照雜湊不符，拒絕還原。")
    failed = backup.parent / "failed-data"
    if failed.exists():
        raise ValueError("失敗資料現場已存在，拒絕覆寫。")
    for path in data.iterdir():
        if path.name not in EXCLUDED:
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("目前資料含重新解析點，停止還原。")
            if path.is_dir():
                inventory(path)
    failed.mkdir()
    for path in list(data.iterdir()):
        if path.name not in EXCLUDED:
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("目前資料含重新解析點，停止還原。")
            path.rename(failed / path.name)
    for path in backup.iterdir():
        if path.is_dir():
            shutil.copytree(path, data / path.name)
        else:
            shutil.copy2(path, data / path.name)
    actual = {}
    for path in data.iterdir():
        if path.name in EXCLUDED:
            continue
        if path.is_dir():
            actual.update({path.name + "/" + key: value for key, value in inventory(path).items()})
        else:
            actual[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != manifest:
        raise ValueError("還原資料校驗失敗，已保留快照與失敗現場。")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("capture", "restore"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    try:
        base = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "MeowKeeperPlus"
        if os.name != "nt" or args.data.resolve() != (base / "Data").resolve():
            raise ValueError("資料根目錄不符合安裝邊界。")
        if (args.backup.name != "data-backup" or args.backup.parent.parent.resolve() != (base / "Updater").resolve()
                or not args.backup.parent.name.startswith("work-")):
            raise ValueError("資料快照不在受保護工作目錄。")
        for path in (base, args.data, args.backup.parent, args.backup.parent.parent):
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("快照路徑不接受重新解析點。")
        (capture if args.mode == "capture" else restore)(args.data, args.backup)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps({"status": "passed", "mode": args.mode}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
