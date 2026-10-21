"""取得並驗證官方 DINOv2 ViT-S/14 身分特徵模型。"""

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
    DEFAULT_IDENTITY_MODEL_NAME,
    DEFAULT_IDENTITY_MODEL_SHA256,
    DEFAULT_IDENTITY_MODEL_URL,
    identity_model_file,
    identity_model_manifest_path,
    sha256_file,
    write_identity_model_manifest,
)


def fetch_identity_model(
    destination: Path,
    *,
    force: bool = False,
    timeout_seconds: float = 120.0,
    manifest: Path | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and not force:
        if sha256_file(destination) != DEFAULT_IDENTITY_MODEL_SHA256:
            raise RuntimeError("既有 DINOv2 模型 SHA-256 不符合固定官方值；請使用 --force。")
        existing_downloaded_at: str | None = None
        target = manifest or identity_model_manifest_path()
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if (
                existing.get("model_name") == DEFAULT_IDENTITY_MODEL_NAME
                and existing.get("sha256") == DEFAULT_IDENTITY_MODEL_SHA256
                and isinstance(existing.get("downloaded_at_utc"), str)
            ):
                existing_downloaded_at = existing["downloaded_at_utc"]
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        write_identity_model_manifest(
            destination,
            manifest_path=manifest,
            downloaded_at_utc=existing_downloaded_at,
        )
        return destination

    request = Request(DEFAULT_IDENTITY_MODEL_URL, headers={"User-Agent": "MeowKeeperPlus-model-fetch/1.0"})
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{DEFAULT_IDENTITY_MODEL_NAME}.",
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
                        raise RuntimeError("DINOv2 模型下載超過安全大小上限。")
                    temporary.write(chunk)
        if temporary_path is None:
            raise RuntimeError("DINOv2 模型暫存檔建立失敗。")
        if sha256_file(temporary_path) != DEFAULT_IDENTITY_MODEL_SHA256:
            raise RuntimeError("DINOv2 模型 SHA-256 驗證失敗，已拒絕寫入。")
        os.replace(temporary_path, destination)
        write_identity_model_manifest(destination, manifest_path=manifest)
        return destination
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="取得並驗證官方 DINOv2 ViT-S/14 身分模型。")
    parser.add_argument("--output", default=str(identity_model_file()))
    parser.add_argument("--manifest", default=str(identity_model_manifest_path()))
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        path = fetch_identity_model(
            Path(args.output),
            force=args.force,
            manifest=Path(args.manifest),
        )
    except (HTTPError, URLError, OSError, RuntimeError) as exc:
        print(f"❌ DINOv2 身分模型取得失敗：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "model": DEFAULT_IDENTITY_MODEL_NAME,
                "path": str(path),
                "source": DEFAULT_IDENTITY_MODEL_URL,
                "sha256": sha256_file(path),
                "manifest": str(Path(args.manifest)),
                "message_zh_tw": "DINOv2 身分模型已驗證。",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
