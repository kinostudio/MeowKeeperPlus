"""Explicit one-camera native standby cycle; restores on in finally."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.integrations.xiaomi.miot_power import MiotPowerClient


def main() -> None:
    parser = argparse.ArgumentParser(description="MIoT 原生開關驗證；預設唯讀。")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-port", type=int, default=1984)
    parser.add_argument("--region", default="sg")
    parser.add_argument("--cycle", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report: dict = {"model": args.model, "off_verified": False, "on_verified": False}
    try:
        api = Go2RtcClient(f"http://127.0.0.1:{args.api_port}", timeout_seconds=20)
        accounts = api.xiaomi_accounts()
        if len(accounts) != 1:
            raise ValueError("account_count")
        devices = api.request_json("/api/xiaomi", params={"id": str(accounts[0]), "region": args.region})
        sources = [item["url"] for item in devices.get("sources", [])
                   if parse_qs(urlsplit(item.get("url", "")).query).get("model") == [args.model]]
        if len(sources) != 1:
            raise ValueError("model_match_count")
        client = MiotPowerClient(Path(__file__).resolve().parents[1], args.config, api)
        report["before"] = client.request(sources[0])
        if args.cycle and report["before"].get("success") is True and report["before"].get("on") is True:
            try:
                report["off"] = client.request(sources[0], on=False)
                report["off_verified"] = report["off"].get("success") is True and report["off"].get("on") is False
            finally:
                report["restore"] = client.request(sources[0], on=True)
                report["on_verified"] = report["restore"].get("success") is True and report["restore"].get("on") is True
        report["status"] = "passed" if report["off_verified"] and report["on_verified"] else "not_verified"
    except Exception as exc:  # noqa: BLE001 - never expose cloud values
        report["status"], report["error_type"] = "failed", type(exc).__name__
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
