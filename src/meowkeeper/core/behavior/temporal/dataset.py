"""R3D18 行為資料集的最小 JSONL 契約。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BehaviorSample:
    clip_path: str
    label: str
    pet_id: str | None = None
    split: str = "train"


def load_manifest(path: str | Path) -> list[BehaviorSample]:
    manifest = Path(path)
    if not manifest.is_file():
        raise FileNotFoundError(f"找不到行為資料集 manifest：{manifest}")
    samples: list[BehaviorSample] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        data = json.loads(line)
        samples.append(
            BehaviorSample(
                clip_path=str(data["clip_path"]),
                label=str(data["label"]),
                pet_id=str(data["pet_id"]) if data.get("pet_id") else None,
                split=str(data.get("split", "train")),
            )
        )
    return samples
