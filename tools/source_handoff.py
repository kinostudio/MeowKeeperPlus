"""從固定 Git 提交建立完整來源／測試交接包，不讀取私人 runtime 資料。"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", *args],
                            cwd=root, capture_output=True, check=False, timeout=60, shell=False)
    if result.returncode:
        raise ValueError("Git 基準、完成提交或來源內容無法驗證。")
    return result.stdout


def validate_paths(paths: list[str]) -> list[str]:
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
        raise ValueError("交接清單須為來源路徑陣列。")
    if len(set(paths)) != len(paths):
        raise ValueError("交接清單須包含不重複的來源及測試檔案。")
    for name in paths:
        path = PurePosixPath(name)
        if (not isinstance(name, str) or "\\" in name or path.is_absolute()
                or len(path.parts) < 2 or path.parts[0] not in {"src", "tests"}
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or re.search(r"[\x00-\x20:<>\"|?*]", name)
                or path.suffix not in {".py", ".js", ".cjs", ".css", ".html", ".json"}):
            raise ValueError("交接清單只能包含允許的相對來源／測試路徑。")
    return sorted(paths)


def snapshot_diff(name: str, data: bytes) -> bytes:
    text = data.decode("utf-8")
    blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    lines = list(difflib.unified_diff([], text.splitlines(True),
                 fromfile="/dev/null", tofile="b/" + name))
    body = []
    for line in lines:
        body.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return (f"diff --git a/{name} b/{name}\nnew file mode 100644\n"
            f"index {'0' * 40}..{blob}\n" + "".join(body)).encode("utf-8")


def build_bundle(root: Path, base: str, head: str, paths: list[str], output: Path) -> dict:
    paths = validate_paths(paths)
    if any(not re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base, head)):
        raise ValueError("基準及完成版本必須提供完整40位 commit SHA。")
    for sha in (base, head):
        if git(root, "rev-parse", "--verify", sha + "^{commit}").decode().strip() != sha:
            raise ValueError("Git commit 不一致。")
    # Squash merges preserve a reviewed tree without retaining its original
    # ancestry. Both fixed commits remain valid endpoints for an applyable diff.
    output = output.absolute()
    if output.exists():
        raise ValueError("交接輸出目錄已存在；不覆寫先前交付。")
    if not output.resolve().is_relative_to((root / "data").resolve()):
        raise ValueError("交接輸出須位於本專案 Data，避免加入公開 Git。")
    output.parent.mkdir(parents=True, exist_ok=True)
    rows, snapshots = [], []
    changes = git(root, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, head,
                  "--", *paths)
    with tempfile.TemporaryDirectory(prefix=".source-handoff-", dir=output.parent) as temporary:
        stage = Path(temporary)
        for name in paths:
            data = git(root, "show", f"{head}:{name}")
            # Decode before any output is published; binary inputs are rejected.
            data.decode("utf-8")
            before = subprocess.run(
                ["git", "-c", f"safe.directory={root.as_posix()}", "show", f"{base}:{name}"],
                cwd=root, capture_output=True, timeout=60, check=False, shell=False)
            old = before.stdout if before.returncode == 0 else None
            dest = stage / "files" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            rows.append({"path": name, "size_bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest(),
                         "base_sha256": hashlib.sha256(old).hexdigest() if old is not None else None,
                         "changed": old != data})
            snapshots.append(snapshot_diff(name, data))
        snapshot = b"".join(snapshots)
        manifest = {"schema_version": 1, "base_commit": base, "completed_commit": head,
                    "file_count": len(rows), "files": rows,
                    "changes_patch_sha256": hashlib.sha256(changes).hexdigest(),
                    "snapshot_patch_sha256": hashlib.sha256(snapshot).hexdigest()}
        (stage / "changes.patch").write_bytes(changes)
        (stage / "snapshot.patch").write_bytes(snapshot)
        (stage / "CHANGED_FILES.txt").write_text("\n".join(paths) + "\n", encoding="utf-8")
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (stage / "README.md").write_text(
            "# 固定 Git 來源交接\n\nmanifest 列出全部來源與測試檔、SHA-256 及兩個完整 commit。\n"
            "changes.patch 只適用於 base_commit 的乾淨 clone，先執行 git apply --check。\n"
            "snapshot.patch 是全部檔案的新建 patch，只能套到空目錄；不可覆蓋正式 Host。\n"
            "files/ 包含完成提交的完整內容；未提交工作區及私人實機紀錄不在包中。\n",
            encoding="utf-8")
        stage.rename(output)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--paths", type=Path, default=ROOT / "configs/source-handoff-paths.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        paths = json.loads(args.paths.read_text(encoding="utf-8"))
        result = build_bundle(ROOT, args.base, args.head, paths, args.output)
        print(json.dumps({k: result[k] for k in ("base_commit", "completed_commit", "file_count")}))
        return 0
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        print("來源交接未完成；請檢查提交、清單與未使用的 Data 輸出目錄。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
