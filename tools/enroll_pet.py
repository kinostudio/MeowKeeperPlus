"""以參考影像建立可配置寵物身分 profile。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.core.identity.color_features import extract_hsv_histogram
from meowkeeper.core.identity.dinov2_embedder import DinoV2Embedder, DINOv2Unavailable
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.core.identity.quality_filter import ReferenceQualityFilter


def _read_image(path: Path) -> bytes:
    data = path.read_bytes()
    if not data:
        raise ValueError(f"影像為空白：{path}")
    return data


def _video_frames(path: Path, *, every: int, limit: int) -> list[bytes]:
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("影片取樣需要 opencv-python 與 numpy。") from exc
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"無法開啟影片：{path}")
    result: list[bytes] = []
    index = 0
    try:
        while len(result) < limit:
            ok, frame = capture.read()
            if not ok:
                break
            if index % every == 0:
                ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                if ok:
                    result.append(bytes(np.asarray(encoded).tobytes()))
            index += 1
    finally:
        capture.release()
    return result


def enroll(args: argparse.Namespace) -> dict[str, object]:
    payloads = [_read_image(path) for path in args.images]
    for video in args.video:
        payloads.extend(_video_frames(video, every=args.video_every, limit=args.video_limit))
    if not payloads:
        raise ValueError("至少需要一張影像或一段影片。")
    quality = ReferenceQualityFilter()
    embedder = None
    if not args.skip_embeddings:
        embedder = DinoV2Embedder(model_path=args.model)
    registry = PetRegistry(args.root)
    profile = registry.register_pet(
        args.name,
        coat_description=args.coat,
        pattern_description=args.pattern,
        pet_id=args.pet_id,
    )
    accepted = 0
    rejected: list[str] = []
    for index, jpeg in enumerate(payloads, start=1):
        result = quality.assess(jpeg)
        if not result.accepted or result.duplicate:
            rejected.append(f"{index}: {result.reason}")
            continue
        registry.add_reference_image(profile.pet_id, jpeg, filename=f"reference_{index:04d}.jpg")
        accepted += 1
        if embedder is not None:
            embedding = embedder.embed(jpeg)
            color = extract_hsv_histogram(jpeg)
            registry.add_embedding(
                profile.pet_id,
                embedding,
                color_vector=color,
                model_version=embedder.model_version,
            )
    return {
        "pet_id": profile.pet_id,
        "display_name": profile.display_name,
        "accepted_reference_images": accepted,
        "rejected": rejected,
        "embedding_model": embedder.model_version if embedder is not None else "skipped",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="建立可配置的 MeowKeeper+ 寵物身分 profile。")
    parser.add_argument("--name", required=True, help="顯示名稱；不會寫死在程式碼。")
    parser.add_argument("--pet-id", help="選擇性穩定 ID；未提供則自動產生。")
    parser.add_argument("--coat", default="", help="毛色描述。")
    parser.add_argument("--pattern", default="", help="花紋描述。")
    parser.add_argument("--images", nargs="*", type=Path, default=[], help="參考圖片路徑。")
    parser.add_argument("--video", nargs="*", type=Path, default=[], help="可選影片路徑。")
    parser.add_argument("--video-every", type=int, default=15, help="每幾格取一張影片參考圖。")
    parser.add_argument("--video-limit", type=int, default=20, help="每段影片最多取幾張。")
    parser.add_argument("--root", type=Path, default=ROOT / "data" / "pets")
    parser.add_argument("--model", type=Path, help="已驗證的官方 DINOv2 權重路徑。")
    parser.add_argument("--skip-embeddings", action="store_true", help="只登錄影像，不計算 embedding。")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.video_every < 1 or args.video_limit < 1:
        print("影片取樣參數必須大於零。", file=sys.stderr)
        return 2
    try:
        result = enroll(args)
    except DINOv2Unavailable as exc:
        print(f"DINOv2 尚未可用：{exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"建立寵物 profile 失敗：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
