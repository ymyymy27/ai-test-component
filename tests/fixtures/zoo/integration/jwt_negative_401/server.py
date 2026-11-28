"""本地进程集成夹具：JWT 登录、负向 401/403 拦截与 SQLite 隔离复位。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB_PATH = Path(os.environ.get("AITEST_FIXTURE_DB", "jwt_fixture.sqlite3"))
VALID_TOKEN = "eyJhbGciOiJIUzI1NiJ9.aitest-valid-jwt"
USER_TOKEN = "eyJhbGciOiJIUzI1NiJ9.aitest-user-jwt"


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS secrets ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()


def reset_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM secrets")
    conn.commit()
    conn.close()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: object) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True})
            return
        if self.path == "/api/secrets":
            auth = self.headers.get("Authorization", "")
            if auth not in {f"Bearer {VALID_TOKEN}", f"Bearer {USER_TOKEN}"}:
                self._send(401, {"error": "unauthorized"})
                return
            conn = sqlite3.connect(DB_PATH)
            rows = [
                {"id": r[0], "title": r[1]} for r in conn.execute("SELECT id, title FROM secrets")
            ]
            conn.close()
            self._send(200, {"items": rows})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        body = self._read_json()
        if self.path == "/api/login":
            if body.get("username") == "admin" and body.get("password") == "admin123":
                self._send(200, {"token": VALID_TOKEN, "role": "admin"})
                return
            if body.get("username") == "viewer" and body.get("password") == "viewer123":
                self._send(200, {"token": USER_TOKEN, "role": "viewer"})
                return
            self._send(401, {"error": "invalid_credentials"})
            return
        if self.path == "/api/secrets":
            auth = self.headers.get("Authorization", "")
            if auth == f"Bearer {USER_TOKEN}":
                self._send(403, {"error": "forbidden"})
                return
            if auth != f"Bearer {VALID_TOKEN}":
                self._send(401, {"error": "unauthorized"})
                return
            title = str(body.get("title") or "").strip()
            if not title:
                self._send(400, {"error": "title_required"})
                return
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute("INSERT INTO secrets (title) VALUES (?)", (title,))
            conn.commit()
            new_id = cur.lastrowid
            conn.close()
            self._send(201, {"id": new_id, "title": title})
            return
        if self.path == "/_reset":
            reset_db()
            self._send(200, {"reset": True})
            return
        self._send(404, {"error": "not_found"})

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"READY {server.server_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
