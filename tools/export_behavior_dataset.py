"""把行為資料夾轉成 R3D18 scaffold 使用的 JSONL manifest。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="輸出 V0.2 行為時序資料集 JSONL manifest。")
    parser.add_argument("--root", type=Path, required=True, help="資料集根目錄。")
    parser.add_argument("--output", type=Path, required=True, help="JSONL 輸出路徑。")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.root.is_dir():
        raise SystemExit(f"找不到資料集根目錄：{args.root}")
    rows: list[dict[str, str]] = []
    for label_dir in sorted(path for path in args.root.iterdir() if path.is_dir()):
        for clip in sorted(label_dir.glob("*")):
            if clip.is_file():
                rows.append({"clip_path": str(clip), "label": label_dir.name, "split": "train"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    print(f"已輸出 {len(rows)} 筆行為資料 manifest：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
