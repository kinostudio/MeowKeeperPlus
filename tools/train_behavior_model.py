"""R3D18 訓練入口 scaffold；V0.2 不會在沒有資料時開訓。"""

from __future__ import annotations

import argparse
from pathlib import Path

from meowkeeper.core.behavior.temporal.dataset import load_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="準備 V0.2 R3D18 行為模型訓練；目前只做資料檢查。")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=1)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    samples = load_manifest(args.manifest)
    if not samples:
        raise SystemExit("行為 manifest 沒有資料；不會啟動訓練。")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    raise SystemExit(
        "R3D18 訓練 scaffold 已驗證資料，但尚未在 V0.2 自動訓練；請先建立標註與 checkpoint。"
    )


if __name__ == "__main__":
    raise SystemExit(main())
