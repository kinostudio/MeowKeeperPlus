"""取得並驗證 MeowKeeper+ 預設 YOLO11n 權重。

來源固定為官方 GitHub ``ultralytics/assets`` release；下載到暫存檔並先驗證
SHA-256，再原子替換目標檔。此工具不接受任意 URL，避免把不明模型塞進正式
預設設定。模型已存在且 hash 正確時不會重複下載。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_MODEL_BYTES = 200 * 1024 * 1024

if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from meowkeeper.runtime import (
    DEFAULT_MODEL_NAME as MODEL_NAME,
)
from meowkeeper.runtime import (
    DEFAULT_MODEL_SHA256 as MODEL_SHA256,
)
from meowkeeper.runtime import (
    DEFAULT_MODEL_URL as MODEL_URL,
)
from meowkeeper.runtime import (
    default_model_file,
    model_manifest_path,
    sha256_file,
    write_model_manifest,
)


def fetch_model(
    destination: Path,
    *,
    force: bool = False,
    timeout_seconds: float = 60.0,
    manifest: Path | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and not force:
        actual = sha256_file(destination)
        if actual == MODEL_SHA256:
            existing_downloaded_at: str | None = None
            manifest_target = manifest or model_manifest_path()
            try:
                existing_manifest = json.loads(manifest_target.read_text(encoding="utf-8"))
                if (
                    existing_manifest.get("model_name") == MODEL_NAME
                    and existing_manifest.get("sha256") == MODEL_SHA256
                    and isinstance(existing_manifest.get("downloaded_at_utc"), str)
                ):
                    existing_downloaded_at = existing_manifest["downloaded_at_utc"]
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
            write_model_manifest(
                destination,
                manifest_path=manifest,
                downloaded_at_utc=existing_downloaded_at,
            )
            return destination
        raise RuntimeError(
            f"既有模型 hash 不符合預設值：{destination}；請使用 --force 重新取得。"
        )

    request = Request(MODEL_URL, headers={"User-Agent": "MeowKeeperPlus-model-fetch/1.0"})
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{MODEL_NAME}.",
            suffix=".download",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            with urlopen(request, timeout=timeout_seconds) as response:
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_MODEL_BYTES:
                        raise RuntimeError("模型下載超過安全大小上限。")
                    temporary.write(chunk)
        if temporary_path is None:
            raise RuntimeError("模型暫存檔建立失敗。")
        actual = sha256_file(temporary_path)
        if actual != MODEL_SHA256:
            raise RuntimeError("模型 SHA-256 驗證失敗，已拒絕寫入預設權重。")
        os.replace(temporary_path, destination)
        write_model_manifest(destination, manifest_path=manifest)
        return destination
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="取得並驗證官方 YOLO11n 預設權重。")
    parser.add_argument(
        "--output",
        default=str(default_model_file()),
        help="目標模型路徑；預設為 models\\yolo11n.pt。",
    )
    parser.add_argument(
        "--manifest",
        default=str(model_manifest_path()),
        help="manifest 路徑；預設為 models\\model_manifest.json。",
    )
    parser.add_argument("--force", action="store_true", help="忽略既有檔案並重新下載後驗證。")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        path = fetch_model(
            Path(args.output),
            force=args.force,
            manifest=Path(args.manifest),
        )
    except (HTTPError, URLError, OSError, RuntimeError) as exc:
        print(f"❌ 預設 YOLO 權重取得失敗：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "model": MODEL_NAME,
                "path": str(path),
                "source": MODEL_URL,
                "sha256": sha256_file(path),
                "manifest": str(Path(args.manifest)),
                "message_zh_tw": "預設 YOLO11n 權重已驗證。",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
