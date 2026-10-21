"""使用本地公開測試影像驗證實際推論裝置，不建立攝影機或 Telegram 連線。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meowkeeper.core.detection.frame_analysis import AnalysisConfigurationError, YoloFrameAnalyzer
from meowkeeper.core.media.sources import FramePacket


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    parser.add_argument("--test-fallback", action="store_true",
                        help="真實 GPU 推論後注入一次 CUDA 錯誤，驗證同一影格轉 CPU。")
    args = parser.parse_args()
    try:
        import torch

        analyzer = YoloFrameAnalyzer(
            str(ROOT / "models" / "yolo11n.pt"),
            device=None if args.device == "auto" else "cpu",
        )
        samples = []
        for sequence, name in enumerate(("cat.jpg", "bus.jpg"), start=1):
            packet = FramePacket(
                sequence=sequence,
                captured_at=datetime.now(timezone.utc),
                source_url="local-public-test-fixture",
                jpeg_bytes=(ROOT / "tests" / "assets" / name).read_bytes(),
            )
            started = time.perf_counter()
            result = analyzer.analyze(packet)
            samples.append({
                "fixture": name,
                "triggered": result.triggered,
                "cat_count": result.details.get("cat_count", 0),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            })
        parameter_device = str(next(analyzer._model.model.parameters()).device)
        initial_backend = analyzer.inference_backend
        initial_device = analyzer.device
        passed = samples[0]["triggered"] and not samples[1]["triggered"]
        fallback = None
        if args.test_fallback:
            if not parameter_device.startswith("cuda"):
                raise RuntimeError("需要先成功完成真實 GPU 推論。")
            from unittest.mock import patch

            predict = analyzer._model.predict
            calls = []

            def fail_once(**kwargs):
                calls.append(kwargs["device"])
                if len(calls) == 1:
                    raise RuntimeError("CUDA out of memory (controlled fault injection)")
                return predict(**kwargs)

            with patch.object(analyzer._model, "predict", side_effect=fail_once):
                recovered = analyzer.analyze(FramePacket(
                    sequence=3,
                    captured_at=datetime.now(timezone.utc),
                    source_url="local-public-test-fixture",
                    jpeg_bytes=(ROOT / "tests" / "assets" / "cat.jpg").read_bytes(),
                ))
            fallback = {
                "failure_is_injected": True,
                "retry_devices": calls,
                "cat_found": recovered.triggered,
                "final_backend": analyzer.inference_backend,
                "model_parameter_device": str(next(analyzer._model.model.parameters()).device),
            }
            passed = passed and recovered.triggered and calls == ["cuda:0", "cpu"]
        print(json.dumps({
            "status": "passed" if passed else "failed",
            "torch_version": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "requested_device": args.device,
            "backend": initial_backend,
            "device": initial_device,
            "model_parameter_device": parameter_device,
            "cuda_architectures": torch.cuda.get_arch_list(),
            "samples": samples,
            "fallback": fallback,
            "external_notifications": 0,
        }, ensure_ascii=False, indent=2))
        return 0 if passed else 1
    except (AnalysisConfigurationError, ImportError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({
            "status": "failed",
            "error_type": type(exc).__name__,
            "message_zh_tw": "本地推論驗證失敗；未建立外部來源或傳送通知。",
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
