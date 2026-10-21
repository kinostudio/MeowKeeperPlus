"""Serve one secret-free C701 recovery script once on a chosen Tailnet IP."""

from __future__ import annotations

import argparse
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "remote-c701" / "Test-C701Relay.ps1"
URL_PATH = "/Test-C701Relay.ps1"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != URL_PATH or not SCRIPT.is_file():
            self.send_error(404)
            return
        payload = SCRIPT.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-SHA256", hashlib.sha256(payload).hexdigest())
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="本機 Tailscale IPv4；不得使用 0.0.0.0。")
    parser.add_argument("--port", type=int, default=28081)
    args = parser.parse_args()
    if not args.host.startswith("100."):
        parser.error("只允許明確指定本機 Tailscale IPv4。")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    expiry = threading.Timer(3600, server.shutdown)
    expiry.daemon = True
    expiry.start()
    print(f"one_shot_ready={args.host}:{args.port} sha256={hashlib.sha256(SCRIPT.read_bytes()).hexdigest()}", flush=True)
    server.serve_forever(poll_interval=0.5)
    server.server_close()
    expiry.cancel()


if __name__ == "__main__":
    main()
