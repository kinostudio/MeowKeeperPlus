"""唯讀抽樣本地事件影片；不建立攝影機連線、不保存私人裁切或身分原值。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--compare-reference-crops", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 20:
        parser.error("抽樣數量必須介於 1 到 20。")
    sys.path.insert(0, str(args.application_root / "src"))
    import cv2
    import numpy as np

    from meowkeeper.core.identity import DinoV2Embedder, IdentityClassifier, PetRegistry
    from meowkeeper.core.identity.quality_filter import ReferenceQualityFilter
    from ultralytics import YOLO

    registry = PetRegistry(args.data_root / "pets")
    classifier = IdentityClassifier(registry, DinoV2Embedder(device="cpu"))
    detector = YOLO(str(args.application_root / "models" / "yolo11n.pt"))
    comparison = None
    reference_counts = []
    if args.compare_reference_crops:
        from meowkeeper.core.identity.color_features import extract_hsv_histogram

        class MemoryGallery:
            def __init__(self):
                self.profiles = []
                self.features = {}

            def list_pets(self, *, enabled_only=True):
                return self.profiles

            def get_embeddings(self, pet_id):
                return self.features[pet_id]

        gallery = MemoryGallery()
        for profile in registry.list_pets(enabled_only=True):
            vectors, colors = [], []
            for relative in profile.reference_images:
                image = cv2.imdecode(np.frombuffer(
                    (registry.root_dir / profile.pet_id / relative).read_bytes(), dtype=np.uint8),
                    cv2.IMREAD_COLOR)
                if image is None:
                    continue
                boxes = detector.predict(image, classes=[15], conf=0.45, device="cpu", verbose=False)[0].boxes
                # A multi-cat reference cannot be attributed without human selection.
                if len(boxes) != 1:
                    continue
                x1, y1, x2, y2 = (int(value) for value in boxes.xyxy[0].tolist())
                crop = image[max(0, y1):min(image.shape[0], y2), max(0, x1):min(image.shape[1], x2)]
                ok, encoded = cv2.imencode(".jpg", crop)
                if not ok or not ReferenceQualityFilter().assess(encoded.tobytes()).accepted:
                    continue
                vectors.append(classifier.embedder.embed(encoded.tobytes()))
                colors.append(extract_hsv_histogram(encoded.tobytes()))
            reference_counts.append(len(vectors))
            if vectors:
                gallery.profiles.append(profile)
                gallery.features[profile.pet_id] = (np.asarray(vectors), np.asarray(colors))
        comparison = IdentityClassifier(gallery, classifier.embedder)
    ffmpeg = args.application_root / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
    clips = sorted((args.data_root / "events").glob("*.mp4"),
                   key=lambda path: path.stat().st_mtime, reverse=True)[:args.limit]
    samples = []
    for index, path in enumerate(clips):
        row = {"sample_index": index, "cats": []}
        try:
            result = subprocess.run(
                [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-ss", "5",
                 "-i", str(path), "-frames:v", "1", "-f", "image2pipe",
                 "-vcodec", "mjpeg", "pipe:1"],
                capture_output=True, timeout=15, check=False, shell=False,
            )
            row["decode_ok"] = result.returncode == 0 and bool(result.stdout)
            image = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                samples.append(row)
                continue
            detected = detector.predict(image, classes=[15], conf=0.45, device="cpu", verbose=False)[0]
            for box in detected.boxes:
                x1, y1, x2, y2 = (int(value) for value in box.xyxy[0].tolist())
                crop = image[max(0, y1):min(image.shape[0], y2),
                             max(0, x1):min(image.shape[1], x2)]
                ok, encoded = cv2.imencode(".jpg", crop)
                if not ok:
                    continue
                jpeg = encoded.tobytes()
                quality = ReferenceQualityFilter().assess(jpeg)
                prediction = classifier.classify(jpeg)
                row["cats"].append({
                    "width": quality.width, "height": quality.height,
                    "quality_accepted": quality.accepted, "quality_reason": quality.reason,
                    "blur_score": round(quality.blur_score, 2),
                    "identity_known": not prediction.is_unknown,
                    "similarity": round(prediction.similarity, 4),
                    "margin": round(prediction.confidence_margin, 4),
                    "identity_reason": prediction.reason,
                })
                if comparison is not None:
                    alternative = comparison.classify(jpeg)
                    row["cats"][-1]["reference_crop_comparison"] = {
                        "identity_known": not alternative.is_unknown,
                        "similarity": round(alternative.similarity, 4),
                        "margin": round(alternative.confidence_margin, 4),
                        "identity_reason": alternative.reason,
                    }
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            row["error_type"] = type(error).__name__
        samples.append(row)
    print(json.dumps({"mode": "read_only_offline", "backend": "CPU",
                      "reference_crop_counts": reference_counts,
                      "samples": samples}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
