from __future__ import annotations

import importlib.util
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.integrations.xiaomi.relay_control import RelayControlClient, RelayControlError
from meowkeeper.integrations.xiaomi.relay_pairing import device_fingerprint

spec = importlib.util.spec_from_file_location("relay_control_agent", ROOT / "scripts" / "remote-c701" / "relay_control_agent.py")
assert spec is not None and spec.loader is not None
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class RelayControlTest(unittest.TestCase):
    def test_client_rejects_non_tailnet_and_unsupported_actions(self) -> None:
        with self.assertRaises(RelayControlError):
            RelayControlClient("192.168.1.1", "a" * 32)
        client = RelayControlClient("100.64.0.43", "a" * 32)
        with self.assertRaises(RelayControlError):
            client.request("profile", profile="subtype=99")
        with self.assertRaises(RelayControlError):
            client.request("ptz_move", direction="diagonal")

    def test_agent_checks_producer_fingerprint_before_control(self) -> None:
        nonce = "a" * 32
        did = "test-did"
        credential = {"nonce": nonce, "fingerprint": device_fingerprint(nonce, did, agent.MODEL),
                      "model": agent.MODEL, "stream": agent.STREAM}
        source = f"xiaomi://private@192.0.2.1?did={did}&model={agent.MODEL}"
        with patch.object(agent, "_api", return_value={agent.STREAM: {"producers": [{"url": source}]}}):
            self.assertEqual(len(agent._stream(credential)["producers"]), 1)
            other = dict(credential, fingerprint="0" * 64)
            with self.assertRaises(RuntimeError):
                agent._stream(other)

    def test_agent_rejects_unauthorized_and_does_not_claim_official_ptz(self) -> None:
        credential = {"nonce": "a" * 32, "fingerprint": "b" * 64, "model": agent.MODEL, "stream": agent.STREAM}
        server = agent.ControlServer("127.0.0.1", credential, Path("unused"), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/v1/control"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            request = urllib.request.Request(url, data=b'{"action":"status"}', headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                opener.open(request, timeout=2)
            self.assertEqual(rejected.exception.code, 403)
            rejected.exception.close()
            request.add_header("Authorization", "Bearer " + credential["nonce"])
            with (
                patch.object(agent, "_stream", return_value={"producers": [{}]}),
                patch.object(agent, "_ptz_state", return_value=False),
                opener.open(request, timeout=2) as response,
            ):
                status = json.loads(response.read())
            self.assertTrue(status["quality_available"])
            self.assertFalse(status["ptz_available"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
