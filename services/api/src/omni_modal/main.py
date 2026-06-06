from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from omni_modal.orchestration import Phase1Orchestrator


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return

        payload = json.dumps(Phase1Orchestrator().health()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = HTTPServer((host, port), HealthHandler)
    server.serve_forever()


if __name__ == "__main__":
    run()
