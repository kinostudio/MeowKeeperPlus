"""Loopback acceptance app: real settings persistence and optional offline clip AI.

No camera connection, cloud request, Telegram or production data is used.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from camera_redesign_fixture import build_fixture

from meowkeeper.app.vision_settings import VisionSettingsStore
from meowkeeper.core.inference.live_overlay import update_live_overlay


def replay(app, root, clip, stopped):
    import cv2
    import torch
    from ultralytics import YOLO

    torch.set_num_threads(2)
    model_path = Path(__file__).resolve().parents[1] / "models" / "yolo11n.pt"
    if not model_path.is_file():
        raise RuntimeError("離線驗收找不到本機模型；不會自動下載。")
    model = YOLO(str(model_path))
    capture = cv2.VideoCapture(str(clip))
    fps = capture.get(cv2.CAP_PROP_FPS)
    duration = capture.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    core = app.state.core
    core._callbacks["preview_snapshot"] = lambda: core.latest_jpeg_path
    core._callbacks["snapshot"] = lambda: core.latest_jpeg_path
    state = {"status": "starting", "metrics": {}}
    core._callbacks["status"] = lambda: state
    overlay = None
    started = time.monotonic()
    index = 0
    try:
        while not stopped.is_set():
            offset = (5 + time.monotonic() - started) % duration
            capture.set(cv2.CAP_PROP_POS_MSEC, offset * 1000)
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("離線影片影格讀取失敗。")
            captured = datetime.now(timezone.utc)
            result = model.predict(frame, imgsz=960, conf=0.55, classes=[15], device="cpu", verbose=False)[0]
            detections = [{"bbox": box, "confidence": confidence}
                          for box, confidence in zip(result.boxes.xyxyn.tolist(), result.boxes.conf.tolist(), strict=True)]
            overlay = update_live_overlay(overlay, detections, source_epoch="offline-event-replay",
                                          captured_at=captured)
            height, width = frame.shape[:2]
            preview = cv2.resize(frame, (960, round(height * 960 / width)))
            target = root / f"replay-{index:05d}.jpg"
            if not cv2.imwrite(str(target), preview):
                raise RuntimeError("離線驗收影格保存失敗。")
            core.set_latest_jpeg(target)
            stamp = datetime.now(timezone.utc).isoformat()
            state = {"status": "running", "metrics": {
                "source": {"status": "running", "source_id": "離線事件影片重播（驗收）",
                           "connections": 1, "resolution": "960×540", "width": 960, "height": 540,
                           "fps": 1, "last_frame_at": stamp, "preview_saved_at": stamp,
                           "preview_epoch": "offline-event-replay", "stream_name": "offline_event_replay"},
                "ai": {"backend": "CPU 離線實際 YOLO", "device": "cpu", "fps": 1,
                       "inference_input_size": 960, "overlay": overlay},
                "telegram": {"enabled": False, "status": "disabled", "recipient_count": 0},
            }}
            index += 1
            stopped.wait(0.5)
    finally:
        capture.release()


def main():
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18986)
    parser.add_argument("--clip", type=Path)
    args = parser.parse_args()
    if args.clip and not args.clip.is_file():
        parser.error("找不到指定的離線事件影片。")
    with tempfile.TemporaryDirectory(prefix="meowkeeper-vision-overlay-acceptance-") as directory:
        root = Path(directory)
        app = build_fixture(root)
        app.state.vision_settings = VisionSettingsStore(root, store=app.state.core.store)
        stopped = threading.Event()
        worker = None
        if args.clip:
            app.state.templates.env.globals["preview_banner"] = "離線事件影片驗收：使用原始素材與實際 CPU YOLO；非即時攝影機，身分及動作未確認。"
            worker = threading.Thread(target=replay, args=(app, root, args.clip, stopped), daemon=True)
            worker.start()
        try:
            uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        finally:
            stopped.set()
            if worker:
                worker.join(timeout=10)


if __name__ == "__main__":
    main()
