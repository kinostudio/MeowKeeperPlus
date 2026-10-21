"""故障注入只證明降級契約，不代表實際 GPU 已通過。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from meowkeeper.core.inference.runtime_device import run_with_cpu_fallback


class RuntimeDeviceTests(unittest.TestCase):
    def owner(self, device="cuda:0"):
        return SimpleNamespace(
            device=device, inference_backend="PyTorch CUDA", _model=Mock(),
            track_history=Mock(),
        )

    def test_cuda_failure_retries_same_frame_and_stays_cpu(self):
        owner = self.owner()
        call = Mock(side_effect=[RuntimeError("CUDA out of memory"), ["result"], []])
        frame = object()
        self.assertEqual(run_with_cpu_fallback(owner, call, {"source": frame}), ["result"])
        self.assertIs(call.call_args.kwargs["source"], frame)
        self.assertEqual(call.call_args.kwargs["device"], "cpu")
        self.assertEqual(owner.device, "cpu")
        self.assertEqual(owner.inference_backend, "CPU")
        self.assertGreaterEqual(owner.last_cpu_fallback_ms, 0)
        self.assertIsNone(owner._model.predictor)
        owner._model.to.assert_called_once_with("cpu")
        owner.track_history.reset.assert_called_once()
        run_with_cpu_fallback(owner, call, {"device": owner.device})
        owner._model.to.assert_called_once()

    def test_cpu_error_is_not_retried(self):
        owner = self.owner("cpu")
        call = Mock(side_effect=RuntimeError("out of memory"))
        with self.assertRaises(RuntimeError):
            run_with_cpu_fallback(owner, call, {})
        self.assertEqual(call.call_count, 1)
        self.assertIsNone(owner.last_cpu_fallback_ms)

    def test_unrelated_error_is_not_retried(self):
        owner = self.owner()
        call = Mock(side_effect=RuntimeError("invalid image shape"))
        with self.assertRaises(RuntimeError):
            run_with_cpu_fallback(owner, call, {})
        self.assertEqual(owner.device, "cuda:0")

    def test_cpu_retry_failure_remains_visible(self):
        owner = self.owner()
        call = Mock(side_effect=[RuntimeError("cuDNN failure"), RuntimeError("CPU failure")])
        with self.assertRaisesRegex(RuntimeError, "CPU failure"):
            run_with_cpu_fallback(owner, call, {})
        self.assertEqual(call.call_count, 2)
