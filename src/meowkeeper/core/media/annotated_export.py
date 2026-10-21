"""Explicit offline export; no camera connections or automatic Telegram sends."""
from __future__ import annotations

import argparse
import json
import math
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def annotation_filters(samples):
    filters = []
    for second, boxes in samples:
        if not math.isfinite(second) or not 0 <= second <= 60:
            raise ValueError("標記時間不合法。")
        for box in boxes[:20]:
            if len(box) != 4 or not all(math.isfinite(v) and 0 <= v <= 1 for v in box):
                continue
            x1, y1, x2, y2 = box
            if x2 <= x1 or y2 <= y1:
                continue
            filters.append(f"drawbox=x=iw*{x1:.6f}:y=ih*{y1:.6f}:w=iw*{x2-x1:.6f}:h=ih*{y2-y1:.6f}:color=orange:t=3:enable='gte(t,{second:.3f})*lt(t,{second+.5:.3f})'")
    return ",".join(filters) or "null"


def export(source: Path, output: Path, *, compare=False):
    # Separate short-lived CPU worker: never steal/reconfigure the live model.
    import cv2
    import torch

    from meowkeeper.core.detection.frame_analysis import YoloFrameAnalyzer
    from meowkeeper.core.media.clip_assembler import ClipAssembler
    from meowkeeper.core.media.media_validation import validate_media
    from meowkeeper.core.media.sources import FramePacket
    from meowkeeper.runtime import default_model_file

    started = time.monotonic()
    assembler = ClipAssembler(timeout_seconds=120)
    validation = validate_media(source, ffprobe_path=assembler.ffprobe_path)
    if not validation.valid or not validation.duration_seconds or validation.duration_seconds > 60:
        raise ValueError("標記匯出僅接受 60 秒內的有效事件影片，原檔不會修改。")
    torch.set_num_threads(1)
    cv2.setNumThreads(1)
    analyzer = YoloFrameAnalyzer(str(default_model_file()), device="cpu", input_size=640)
    capture = cv2.VideoCapture(str(source))
    samples = []
    try:
        for second in range(math.ceil(validation.duration_seconds)):
            if time.monotonic() - started > 90:
                raise TimeoutError("事件標記分析逾時。")
            capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            frame = cv2.resize(frame, (640, max(2, round(h * 640 / w)))) if w > 640 else frame
            ok, encoded = cv2.imencode(".jpg", frame)
            if not ok:
                raise ValueError("標記影格編碼失敗。")
            result = analyzer.analyze(FramePacket(second, datetime.now(timezone.utc), "offline-event", encoded.tobytes()))
            samples.append((float(second), [d["bbox"] for d in result.details.get("detections", []) if d.get("bbox")]))
    finally:
        capture.release()
    if not samples:
        raise ValueError("未取得可分析的事件影格。")
    analyzed_ms = (time.monotonic() - started) * 1000
    with tempfile.TemporaryDirectory(prefix="meowkeeper-annotation-") as directory:
        script = Path(directory) / "filters.txt"
        script.write_text(annotation_filters(samples), encoding="ascii")
        encoding_started = time.monotonic()
        result = assembler.transcode_for_telegram(source, output, annotation_filter_file=script)
        encoding_ms = (time.monotonic() - encoding_started) * 1000
        baseline_ms = None
        if compare:
            baseline_started = time.monotonic()
            baseline = assembler.transcode_for_telegram(source, Path(directory) / "baseline.mp4")
            if baseline.path is None:
                raise RuntimeError("無框比較轉碼失敗。")
            baseline_ms = round((time.monotonic() - baseline_started) * 1000)
    if result.path is None:
        raise RuntimeError("標記影片轉碼失敗；原始影片仍保留。")
    return {"status": "passed", "sample_count": len(samples), "analysis_ms": round(analyzed_ms),
            "encoding_ms": round(encoding_ms), "total_ms": round((time.monotonic()-started)*1000),
            "baseline_encoding_ms": baseline_ms, "notification_ms": None,
            "annotation": "cat_boxes_sampled_1fps_hold_500ms", "telegram_sent": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()
    try:
        if not args.source.is_file() or args.source.suffix.lower() != ".mp4":
            raise ValueError("僅接受本機事件 MP4。")
        print(json.dumps(export(args.source, args.output, compare=args.compare)))
        return 0
    except Exception as error:  # noqa: BLE001 - no file path/provider output in logs
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
