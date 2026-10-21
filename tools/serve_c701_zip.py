"""Serve only the secret-free C701 relay ZIP on localhost for Tailnet proxying."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ZIP = Path(__file__).resolve().parents[1] / "dist" / "C701-remote-relay-v1.9.14-2026-10-04-diagnostic-3.zip"
URL_PATH = "/" + ZIP.name


class ZipOnlyHandler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self._respond(include_body=False)

    def do_GET(self) -> None:
        self._respond(include_body=True)

    def _respond(self, *, include_body: bool) -> None:
        if self.path != URL_PATH or not ZIP.is_file():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(ZIP.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if include_body:
            with ZIP.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    self.wfile.write(chunk)
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 28082), ZipOnlyHandler).serve_forever()
