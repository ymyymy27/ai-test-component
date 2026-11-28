"""本地进程集成夹具：多服务（前端 SPA 静态壳服务）。"""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INDEX_HTML = (
    b"<!doctype html><html><head><title>MultiService SPA</title></head>"
    b"<body><div id='app'>ready</div></body></html>"
)


class FrontendHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(INDEX_HTML)))
        self.end_headers()
        self.wfile.write(INDEX_HTML)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    server = ThreadingHTTPServer(("127.0.0.1", port), FrontendHandler)
    print(f"READY {server.server_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
