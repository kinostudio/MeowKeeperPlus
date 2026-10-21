"""取得官方 Windows 可攜式 Python 與 Node runtime，不修改系統安裝。"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PYTHON_URL = "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip"
PYTHON_SHA256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
NODE_URL = "https://nodejs.org/download/release/v22.23.2/node-v22.23.2-win-x64.zip"
NODE_SHA256 = "1177b4137ba5adaa56354ae40f1080c7450e8ae09cecb47da459d1c52ac99f97"


def _download_verified(url: str, expected_sha256: str, destination: Path) -> None:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("建置環境缺少 requests；請先準備建置依賴，終端使用者不需安裝。") from exc
    # Some managed Windows networks serve official archives only through
    # bounded byte-range requests; a full-body GET can wait forever at byte 0.
    with requests.head(url, timeout=(10, 20)) as response:
        response.raise_for_status()
        size = int(response.headers.get("Content-Length", "0"))
    if size <= 0:
        raise ValueError("官方下載端未提供有效檔案大小。")
    digest = hashlib.sha256()
    chunk_size = 1024 * 1024
    with destination.open("wb") as target:
        for start in range(0, size, chunk_size):
            end = min(start + chunk_size, size) - 1
            with requests.get(
                url, headers={"Range": f"bytes={start}-{end}"}, timeout=(10, 30)
            ) as response:
                response.raise_for_status()
                expected_range = f"bytes {start}-{end}/{size}"
                if response.status_code != 206 or response.headers.get("Content-Range") != expected_range:
                    raise ValueError("官方下載端未依要求回傳正確的檔案區段。")
                chunk = response.content
            if len(chunk) != end - start + 1:
                raise ValueError("官方 runtime 下載區段長度不符。")
            target.write(chunk)
            digest.update(chunk)
    if digest.hexdigest().lower() != expected_sha256:
        destination.unlink(missing_ok=True)
        raise ValueError("官方 runtime SHA-256 不符；已刪除下載檔，不會安裝。")


def _extract_verified(archive: Path, destination: Path, *, node: bool = False) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            normalized = PurePosixPath(member.filename.replace("\\", "/"))
            parts = normalized.parts
            if normalized.is_absolute() or not parts or any(
                part in ("", ".", "..") or ":" in part for part in parts
            ):
                raise ValueError("官方 ZIP 含不安全路徑，已停止解壓。")
            if node:
                if not parts[0].startswith("node-v22.23.2-win-x64"):
                    raise ValueError("Node ZIP 目錄格式與預期不符。")
                parts = parts[1:]
                if not parts:
                    continue
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _enable_embedded_packages(runtime: Path) -> None:
    pth = runtime / "python313._pth"
    if not pth.is_file():
        raise ValueError("官方 Python runtime 缺少 python313._pth。")
    original = pth.read_text(encoding="utf-8")
    if "python313.zip" not in original:
        raise ValueError("官方 Python runtime 的模組路徑格式與預期不符。")
    (runtime / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    pth.write_text("python313.zip\n.\nLib\\site-packages\nimport site\n", encoding="utf-8")


def prepare(*, root: Path = ROOT) -> dict[str, str]:
    python_target = root / "runtime" / "python"
    node_target = root / "vendor" / "javascript"
    for target in (python_target, node_target):
        if target.exists():
            raise ValueError(f"目標目錄已存在，為避免覆寫已停止：{target}")
    python_target.parent.mkdir(parents=True, exist_ok=True)
    node_target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="meowkeeper-official-runtimes-") as scratch:
        scratch_path = Path(scratch)
        python_archive = scratch_path / "python.zip"
        node_archive = scratch_path / "node.zip"
        _download_verified(PYTHON_URL, PYTHON_SHA256, python_archive)
        _download_verified(NODE_URL, NODE_SHA256, node_archive)
        python_staging = scratch_path / "python"
        node_staging = scratch_path / "javascript"
        _extract_verified(python_archive, python_staging)
        _extract_verified(node_archive, node_staging, node=True)
        _enable_embedded_packages(python_staging)
        if not (python_staging / "python.exe").is_file() or not (python_staging / "pythonw.exe").is_file():
            raise ValueError("官方 Python ZIP 缺少 python.exe 或 pythonw.exe。")
        if not (node_staging / "node.exe").is_file():
            raise ValueError("官方 Node ZIP 缺少 node.exe。")
        python_staging.replace(python_target)
        node_staging.replace(node_target)
    return {
        "python": str(python_target),
        "python_sha256": PYTHON_SHA256,
        "node": str(node_target),
        "node_sha256": NODE_SHA256,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下載並校驗官方可攜式 Python／Node runtime。")
    parser.parse_args(argv)
    try:
        result = prepare()
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        print(f"官方 runtime 準備失敗：{exc}", file=sys.stderr)
        return 2
    print(f"Python runtime: {result['python']} (SHA-256: {result['python_sha256']})")
    print(f"Node runtime: {result['node']} (SHA-256: {result['node_sha256']})")
    print("尚未安裝 Python 套件；此步驟本身不構成可發布包。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
