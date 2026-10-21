"""建置階段匯出同一官方權重，終端使用者無需操作。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from meowkeeper.runtime import default_model_file, validate_model_file


def main() -> None:
    os.environ.setdefault("YOLO_CONFIG_DIR", str(Path(__file__).resolve().parents[1] / "data/ultralytics"))
    from ultralytics import YOLO
    source = default_model_file()
    validate_model_file(source)
    target = source.parent / "accelerated"
    target.mkdir(parents=True, exist_ok=True)
    exported = Path(YOLO(str(source)).export(format="onnx", dynamic=True, batch=1,
                                          imgsz=640, opset=17, simplify=False, device="cpu"))
    path = target / "yolo11n.onnx"
    shutil.move(str(exported), path)
    manifest = {"model": "yolo11n", "format": "onnx", "batch": 1, "dynamic_spatial": True,
                "ultralytics_version": __import__("ultralytics").__version__,
                "onnx_version": __import__("onnx").__version__, "opset": 17,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size, "created_at": datetime.now(timezone.utc).isoformat()}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
