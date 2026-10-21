"""Serve only the secret-free one-shot C701 diagnostic script on localhost."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "remote-c701"
    / "Set-C701UdpFirewall.ps1"
)
URL_PATH = "/" + SCRIPT.name


class ScriptOnlyHandler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self._respond(include_body=False)

    def do_GET(self) -> None:
        self._respond(include_body=True)

    def _respond(self, *, include_body: bool) -> None:
        if self.path != URL_PATH or not SCRIPT.is_file():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(SCRIPT.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if include_body:
            with SCRIPT.open("rb") as source:
                while chunk := source.read(64 * 1024):
                    self.wfile.write(chunk)
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 28082), ScriptOnlyHandler).serve_forever()
