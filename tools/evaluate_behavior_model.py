"""R3D18 評估入口 scaffold；沒有核准 checkpoint 時安全停止。"""

from __future__ import annotations

import argparse
from pathlib import Path

from meowkeeper.core.behavior.temporal.dataset import load_manifest
from meowkeeper.core.behavior.temporal.r3d18_backend import R3D18Backend, TemporalModelUnavailable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="評估 V0.2 R3D18 行為模型 checkpoint。")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    samples = load_manifest(args.manifest)
    if not samples:
        raise SystemExit("行為 manifest 沒有資料；無法評估。")
    try:
        R3D18Backend(args.checkpoint)
    except TemporalModelUnavailable as exc:
        raise SystemExit(f"尚未能評估 R3D18：{exc}") from exc
    raise SystemExit("R3D18 評估資料介面已準備；label mapping 尚未在 V0.2 啟用。")


if __name__ == "__main__":
    raise SystemExit(main())
