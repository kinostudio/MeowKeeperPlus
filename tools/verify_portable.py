"""驗證 MeowKeeper+ 可攜包的檔案完整性與私人資料邊界。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any

PROHIBITED_TOP_LEVEL = {
    ".env",
    "config.json",
    "reports",
    "tests",
    "Start-MeowKeeperPlus.cmd",
    "Start-MeowKeeperPlus-Tray.cmd",
}
ALLOWED_BUNDLED_DATA_PREFIX = "data/torch/hub/facebookresearch_dinov2_7764ea0f912e53c92e82eb78a2a1631e92725fc/"
ALLOWED_BUNDLED_DATA_FILES = {"data/torch/hub/trusted_list"}
RUNTIME_LOCK_NAMES = {"MeowKeeperPlusHost", "MeowKeeperPlusTray", "MeowKeeperPlusGo2Rtc"}


def _runtime_lock(path: Path, relative: str) -> bool:
    if relative not in {f"data/locks/{name}.json" for name in RUNTIME_LOCK_NAMES}:
        return False
    if path.is_symlink() or path.stat().st_size > 16384:
        raise ValueError("執行期鎖檔格式不安全。")
    payload = json.loads(path.read_text(encoding="utf-8"))
    name = path.stem
    if (not isinstance(payload, dict) or payload.get("lock_name") != name
            or payload.get("role") != name.removeprefix("MeowKeeperPlus").lower()
            or type(payload.get("pid")) is not int or payload["pid"] < 1
            or not isinstance(payload.get("executable"), str)
            or not isinstance(payload.get("command_line"), list)
            or not isinstance(payload.get("created_at"), (int, float))):
        raise ValueError("執行期鎖檔格式不正確。")
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def verify_portable(root: Path, *, allow_runtime_locks: bool = False) -> dict[str, Any]:
    package = root.resolve()
    manifest_path = package / "build_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("找不到 build_manifest.json，拒絕安裝不完整的發布包。")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("build_manifest.json 無法讀取。") from exc
    records = payload.get("files")
    if not isinstance(records, list) or not records:
        raise ValueError("build manifest 沒有可驗證的檔案清單。")

    expected: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise TypeError("build manifest 含有無效記錄。")
        relative = str(record.get("path", ""))
        portable_path = PurePosixPath(relative)
        if not relative or portable_path.is_absolute() or ".." in portable_path.parts:
            raise ValueError("build manifest 含有不安全路徑。")
        normalized = portable_path.as_posix()
        if normalized in expected:
            raise ValueError(f"build manifest 含有重複路徑：{normalized}")
        expected.add(normalized)
        target = package.joinpath(*portable_path.parts)
        if not target.is_file():
            raise ValueError(f"發布包缺少檔案：{normalized}")
        try:
            expected_size = int(record["size_bytes"])
            expected_hash = str(record["sha256"]).upper()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"build manifest 檔案記錄不完整：{normalized}") from exc
        if target.stat().st_size != expected_size or _sha256(target) != expected_hash:
            raise ValueError(f"發布包檔案已缺損或被篡改：{normalized}")

    actual = set()
    for path in package.rglob("*"):
        if not path.is_file() or path == manifest_path:
            continue
        relative = path.relative_to(package)
        if allow_runtime_locks and _runtime_lock(path, relative.as_posix()):
            continue
        if path.suffix.lower() == ".pyc" or "__pycache__" in relative.parts:
            # CPython may create bytecode before the verifier's own main()
            # starts.  These derived files are never trusted as application
            # inputs; every shipped source and binary still must be listed.
            continue
        actual.add(relative.as_posix())
    unlisted = sorted(actual - expected)
    if unlisted:
        raise ValueError(f"發布包含有 manifest 外檔案：{unlisted[0]}")
    missing = sorted(expected - actual)
    if missing:
        raise ValueError(f"發布包缺少 manifest 檔案：{missing[0]}")

    bundled_data = package / "data"
    if bundled_data.is_dir():
        for path in bundled_data.rglob("*"):
            if path.is_file() and path.suffix.lower() != ".pyc" and "__pycache__" not in path.parts:
                relative = path.relative_to(package).as_posix()
                if allow_runtime_locks and _runtime_lock(path, relative):
                    continue
                if relative not in ALLOWED_BUNDLED_DATA_FILES and not relative.startswith(ALLOWED_BUNDLED_DATA_PREFIX):
                    raise ValueError("發布包含未核准的 data 檔案；只允許固定 commit 的官方 DINOv2 source cache。")

    prohibited = sorted(name for name in PROHIBITED_TOP_LEVEL if (package / name).exists())
    if prohibited:
        raise ValueError("發布包含有不得安裝的私人或開發資料：" + ", ".join(prohibited))
    for required in (
        "runtime/python/python.exe",
        "runtime/python/pythonw.exe",
        "vendor/ffmpeg/bin/ffmpeg.exe",
        "vendor/ffmpeg/bin/ffprobe.exe",
        "models/yolo11n.pt",
        "models/model_manifest.json",
        "tools/run_host.py",
        "tools/run_tray.py",
        "configs/botsort_reid.yaml",
    ):
        if not (package / Path(required)).is_file():
            raise ValueError(f"發布包缺少必要檔案：{required}")
    model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
    if _sha256(package / "models" / "yolo11n.pt") != str(model.get("sha256", "")).upper():
        raise ValueError("預設 YOLO 模型校驗碼與 build manifest 不一致。")
    return {
        "status": "passed",
        "package_type": payload.get("package_type"),
        "file_count": len(expected),
        "model_sha256": str(model.get("sha256", "")).upper(),
        "private_data_present": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安裝前完整驗證 MeowKeeper+ 可攜包。")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--allow-runtime-locks", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = verify_portable(args.root, allow_runtime_locks=args.allow_runtime_locks)
    except (OSError, TypeError, ValueError) as exc:
        print(f"發布包驗證失敗：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
