"""Sandboxed Manim runner (stdlib only).

Runs on an internal Docker network with no egress. Phase 1 exposes /health only;
/render (static checks + timed subprocess render into /out) is added in phase 4.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            import manim

            self._json(200, {"status": "ok", "manim": manim.__version__})
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, fmt: str, *args) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()  # noqa: S104
