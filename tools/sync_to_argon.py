"""以既有 SSH 授權差量同步 Argon 開發工作區，絕不操作正式 Host。"""
from __future__ import annotations

import argparse
import base64
import difflib
import fnmatch
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
REMOTE = ""
DESTINATION = ""
DESKTOP = ""
MAX_FILE_BYTES = 2 * 1024 * 1024
BLOCKED_DIRS = {".git", "runtime", "vendor", "models", "data", "dist", "build", ".venv", "venv",
                "__pycache__", "secrets", "node_modules", "reports", "logs", ".ssh", ".aws", ".codex", "ultralytics"}
BLOCKED_GLOBS = ("*.pyc", "*.pyo", "*.zip", "*.pt", "*.pth", "*.onnx", "*.db*", "*.sqlite*", ".env*",
                 "*.dpapi", "*.pem", "*.key", "*.pfx", "*.p12", "*.exe", "*.dll", "*.pyd", "*.bak*",
                 "*.secrets.json", "id_rsa*", "id_ed25519*", "*authorized_keys*", "*ed25519*", "*.part??",
                 "*.log", "kernel.errors.txt")
GENERATED = {".argon-git-meta.json", "ARGON_UNCOMMITTED.diff"}
BOUNDARY_FIELDS = ("src/", "tools/", "scripts/", "configs/")


def allowed_path(name: str) -> bool:
    parts = name.split("/")
    if (not name or "\\" in name or any(p in {"", ".", ".."} for p in parts)
            or any(re.search(r'[<>:"|?*\x00-\x1f]', p) or p.endswith((" ", ".")) for p in parts)
            or any(p.lower() in BLOCKED_DIRS for p in parts)
            or any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", p) for p in parts)):
        return False
    if name.lower() == "configs/update-public.pem":
        return True
    if name in GENERATED:
        return True
    return (not any(fnmatch.fnmatch(p.lower(), glob) for p in parts for glob in BLOCKED_GLOBS)
            and not parts[-1].lower().startswith(".argon-"))


def git(*args: str, root: Path = ROOT) -> bytes:
    result = subprocess.run(["git", "-c", f"safe.directory={root}", *args], cwd=root,
                            capture_output=True, timeout=60, check=False, shell=False)
    if result.returncode:
        raise RuntimeError("Git 工作區讀取失敗；未執行同步。")
    return result.stdout


def safe_local(root: Path, name: str) -> Path:
    if not allowed_path(name):
        raise ValueError("同步檔案路徑不在允許範圍。")
    path = root
    for part in PurePosixPath(name).parts:
        path = path / part
        if path.exists() and (path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("同步不接受連結或 Windows reparse point。")
    return path


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def inventory(root: Path = ROOT) -> tuple[dict[str, bytes], list[str]]:
    names = sorted(set(git("ls-files", "-z", "--cached", "--others", "--exclude-standard", root=root)
                       .decode("utf-8").rstrip("\0").split("\0")))
    files, diff_paths = {}, []
    seen = set()
    for name in names:
        if not allowed_path(name) or name in GENERATED:
            continue
        path = safe_local(root, name)
        if path.is_file() and path.stat().st_size > MAX_FILE_BYTES:
            continue
        if name.casefold() in seen:
            raise ValueError("工作區有 Windows 大小寫衝突檔名。")
        seen.add(name.casefold())
        diff_paths.append(name)
        if path.is_file():
            content = path.read_bytes()
            if re.search(rb"(?m)^-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\r?$", content):
                raise ValueError("允許範圍內發現私鑰格式，已阻擋同步；未輸出檔案內容。")
            files[name] = content
    return files, diff_paths


def working_diff(files: dict[str, bytes], paths: list[str], root: Path = ROOT) -> bytes:
    pieces = []
    # HEAD includes both staged and unstaged changes; excluded files never enter
    # the pathspec. Batch below Windows command-line limits.
    for start in range(0, len(paths), 60):
        pieces.append(git("diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", *paths[start:start + 60], root=root))
    untracked = set(git("ls-files", "-z", "--others", "--exclude-standard", root=root)
                    .decode("utf-8").rstrip("\0").split("\0"))
    for name in sorted(untracked.intersection(files)):
        try:
            text = files[name].decode("utf-8-sig")
        except UnicodeDecodeError:
            pieces.append(f"Binary new file: {name}\n".encode())
            continue
        pieces.append((f"diff --git a/{name} b/{name}\n" + "".join(difflib.unified_diff(
            [], text.splitlines(keepends=True), fromfile="/dev/null", tofile=f"b/{name}"))).encode("utf-8"))
    diff = b"".join(pieces)
    if re.search(rb"(?m)^[+-]?-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\r?$", diff):
        raise ValueError("歷史差異含私鑰格式，已阻擋傳送。")
    return diff


def remote_call(mode: str, payload: dict, identity: Path) -> dict:
    payload = {**payload, "workspace_root": DESTINATION, "desktop_root": DESKTOP}
    helper = (ROOT / "tools/argon_sync_remote.ps1").read_text(encoding="utf-8-sig")
    # Keep the remote command below cmd.exe's command-line limit. Code and
    # changed-file payload travel compressed on stdin, never as CLI arguments.
    bootstrap = """
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
try {
$bytes=[Convert]::FromBase64String([Console]::In.ReadToEnd())
$memory=New-Object IO.MemoryStream(,$bytes)
$gzip=New-Object IO.Compression.GZipStream($memory,[IO.Compression.CompressionMode]::Decompress)
$reader=New-Object IO.StreamReader($gzip,[Text.Encoding]::UTF8)
try{$envelope=$reader.ReadToEnd()|ConvertFrom-Json}finally{$reader.Dispose();$memory.Dispose()}
$mode=$envelope.mode;$request=$envelope.payload
& ([ScriptBlock]::Create($envelope.program))
}catch{[Console]::Error.WriteLine('argon_sync_remote_failed:bootstrap_failed');exit 1}
"""
    envelope = {"program": helper, "mode": mode, "payload": payload}
    wire = base64.b64encode(gzip.compress(json.dumps(envelope, ensure_ascii=True).encode("ascii")))
    if len(wire) > 8 * 1024:
        # Windows OpenSSH Console.In is unsuitable for very long base64 lines.
        # Use its SFTP channel for large payloads, confined to the workspace.
        upload = ".argon-upload-" + uuid.uuid4().hex + ".txt"
        remote_path = DESTINATION + "\\" + upload
        with tempfile.TemporaryDirectory(prefix="meowkeeper-argon-wire-") as directory:
            local_wire = Path(directory) / "payload.txt"
            local_wire.write_bytes(wire)
            copied = subprocess.run(["scp", "-q", "-i", str(identity), "-o", "BatchMode=yes", "-o",
                                    "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", str(local_wire),
                                    REMOTE + ":" + remote_path.replace("\\", "/")],
                                   capture_output=True, timeout=180, check=False, shell=False)
            if copied.returncode:
                raise RuntimeError("SSH SFTP 工作區傳輸未完成；未操作正式 Host。")
        bootstrap = bootstrap.replace("[Console]::In.ReadToEnd()", f"[IO.File]::ReadAllText('{remote_path}')")
        bootstrap = ("$upload='" + remote_path + "'\n"
                     "try {\n" + bootstrap + "\n} finally { if([IO.File]::Exists($upload)){[IO.File]::Delete($upload)} }\n")
        wire_size = len(wire)
        wire = b""
    else:
        wire_size = len(wire)
    encoded = base64.b64encode(bootstrap.encode("utf-16-le")).decode("ascii")
    # A real stdin file avoids Windows communicate() blocking while writing a
    # large pipe before its timeout can be observed. No credential goes here.
    with tempfile.TemporaryFile() as input_file:
        input_file.write(wire)
        input_file.seek(0)
        result = subprocess.run(["ssh", "-i", str(identity), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                                 "-o", "ConnectTimeout=12", REMOTE, "powershell.exe", "-NoProfile", "-NonInteractive",
                                 "-EncodedCommand", encoded], stdin=input_file, capture_output=True, timeout=180,
                                check=False, shell=False)
    if result.returncode:
        # Never print raw SSH/PowerShell stderr: it can include private paths or
        # the serialized request after a parser error.
        code = re.search(rb"argon_sync_remote_failed:([a-z_]+)", result.stderr)
        reason = code[1].decode("ascii") if code else "ssh_failed"
        raise RuntimeError(f"SSH 同步未完成（結束碼 {result.returncode}；{reason}）；未操作正式 Host。")
    try:
        reply = json.loads(result.stdout.decode("utf-8-sig"))
        if mode in {"apply", "stage"}:
            reply["transport_bytes_sent"] = wire_size
        return reply
    except (ValueError, UnicodeError):
        raise RuntimeError("遠端回覆格式無法辨識；未宣稱同步完成。") from None


def baseline(package: Path, installed: dict) -> dict:
    with zipfile.ZipFile(package) as archive:
        manifest_name = next(n for n in archive.namelist() if n == "build_manifest.json" or n.endswith("/build_manifest.json"))
        raw = archive.read(manifest_name)
        manifest = json.loads(raw)
        if digest(raw).lower() != installed["manifest_sha256"].lower():
            raise ValueError("本機基準包與遠端正式包 manifest 不同，停止同步以免混用基準。")
        prefix = manifest_name[:-len("build_manifest.json")]
        changed, formatting, missing, count = [], [], [], 0
        for item in manifest["files"]:
            name = item["path"]
            if not (name.startswith(BOUNDARY_FIELDS) or name == "pyproject.toml") or not allowed_path(name):
                continue
            count += 1
            path = safe_local(ROOT, name)
            if not path.is_file():
                missing.append(name)
                continue
            content = path.read_bytes()
            if digest(content).lower() != item["sha256"].lower():
                old = archive.read(prefix + name)
                try:
                    same = old.decode("utf-8-sig").replace("\r\n", "\n") == content.decode("utf-8-sig").replace("\r\n", "\n")
                except UnicodeError:
                    same = False
                (formatting if same else changed).append(name)
        if missing:
            raise ValueError("開發工作區缺少基準包檔案；已停止同步。")
        return {"release": manifest["output"], "built_at_utc": manifest["built_at_utc"],
                "manifest_sha256": digest(raw), "checked_files": count, "missing_files": missing,
                "content_changes": changed, "format_only_changes": formatting,
                "commit_evidence": "發布包沒有 Git commit 欄位；此結果為檔案基準比對，非發布 tag 祖先證明。"}


def plan(files: dict[str, bytes], remote: dict) -> tuple[list[str], list[str]]:
    changed = [name for name, content in files.items() if remote["actual"].get(name) != digest(content)]
    removed = [name for name in remote["managed"] if name not in files]
    if any(not allowed_path(name) for name in removed):
        raise ValueError("遠端清單含不允許刪除的路徑；已停止同步。")
    return sorted(changed), sorted(removed)


def main() -> int:
    global REMOTE, DESTINATION, DESKTOP
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", type=Path, default=Path(os.environ.get("LOCALAPPDATA", "")) /
                        "Codex/MeowKeeperPlus/ssh/argon-ed25519")
    parser.add_argument("--remote", required=True, help="已授權的 SSH 使用者與主機，格式 user@host。")
    parser.add_argument("--destination", required=True, help="遠端桌面下的檔案交接目錄；不可為 Git clone。")
    parser.add_argument("--desktop", required=True, help="遠端使用者桌面的絕對路徑。")
    parser.add_argument("--baseline-package", type=Path, help="比對正式 r111 等已發布 ZIP 的第一方基準；不傳送 ZIP。")
    parser.add_argument("--dry-run", action="store_true", help="唯讀盤點遠端並顯示差量，不寫遠端。")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+", args.remote):
        parser.error("SSH 目的地格式不正確。")
    for value in (args.destination, args.desktop):
        if not re.fullmatch(r"[A-Za-z]:\\[^'\r\n]+", value):
            parser.error("遠端目錄須為不含引號的 Windows 絕對路徑。")
    REMOTE, DESTINATION, DESKTOP = args.remote, args.destination.rstrip("\\"), args.desktop.rstrip("\\")
    try:
        files, paths = inventory()
        source_snapshot = {name: digest(content) for name, content in files.items()}
        stamp = datetime.now(timezone.utc).isoformat()
        meta = {"branch": git("branch", "--show-current").decode().strip(), "head": git("rev-parse", "HEAD").decode().strip(),
                "synced_at_utc": stamp, "status_short": git("status", "--short").decode("utf-8"),
                "repository": "https://github.com/kinostudio/MeowKeeperPlus", "destination": DESTINATION}
        for name, content in list(files.items()):
            if fnmatch.fnmatch(PurePosixPath(name).name, "HANDOFF_ARGON_*.md"):
                short = PurePosixPath(name).name
                if short in files and files[short] != content:
                    raise ValueError("交接文件同名但內容不同；已停止同步。")
                files[short] = content
        handoffs = sorted(n for n in files if "/" not in n and fnmatch.fnmatch(n, "HANDOFF_ARGON_*.md"))
        remote = remote_call("inspect", {"paths": sorted(files)}, args.identity)
        if args.baseline_package:
            meta["baseline"] = baseline(args.baseline_package, remote["installed"])
        files["ARGON_UNCOMMITTED.diff"] = working_diff(files, paths)
        files[".argon-git-meta.json"] = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
        # Fetch artifact hashes too; content-only uploads stay incremental.
        remote = remote_call("inspect", {"paths": sorted(files)}, args.identity)
        changed, removed = plan(files, remote)
        summary = {"status": "dry_run" if args.dry_run else "pending", "file_count": len(files),
                   "changed_files": len(changed), "removed_files": len(removed),
                   "changed_bytes": sum(len(files[n]) for n in changed), "handoffs": handoffs,
                   "baseline": meta.get("baseline")}
        if not args.dry_run:
            latest, _ = inventory()
            if {name: digest(content) for name, content in latest.items()} != source_snapshot:
                raise ValueError("盤點期間工作區發生變更；已停止同步。")
            entries = {n: {"sha256": digest(c), "size_bytes": len(c)} for n, c in files.items()}
            batches, batch, size = [], [], 0
            for name in changed:
                if batch and (size + len(files[name]) > 256 * 1024 or len(batch) >= 20):
                    batches.append(batch)
                    batch, size = [], 0
                batch.append(name)
                size += len(files[name])
            if batch:
                batches.append(batch)
            transferred = 0
            for index, batch in enumerate(batches, 1):
                print(f"Argon：傳送批次 {index}/{len(batches)}（{len(batch)} 檔）", file=sys.stderr, flush=True)
                staged = remote_call("stage", {"files": {n: entries[n] for n in batch},
                    "changed": {n: base64.b64encode(files[n]).decode() for n in batch}, "remove": [], "handoffs": [],
                    "expected": {n: remote["actual"].get(n) for n in batch}}, args.identity)
                transferred += staged["transport_bytes_sent"]
            latest, _ = inventory()
            if {name: digest(content) for name, content in latest.items()} != source_snapshot:
                raise ValueError("傳送期間工作區改變；未更新完成 manifest，請重跑。")
            result = remote_call("apply", {"files": entries, "changed": {},
                "remove": removed, "expected": {n: remote["actual"].get(n) for n in changed + removed},
                "handoffs": handoffs, "synced_at_utc": stamp}, args.identity)
            result["transport_bytes_sent"] += transferred
            result["uploaded_files"] = len(changed)
            result["upload_batches"] = len(batches)
            summary.update(result)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.TimeoutExpired, zipfile.BadZipFile):
        print("Argon 同步未完成；請確認基準、SSH 身分與檔案安全條件。未操作正式 Host。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
