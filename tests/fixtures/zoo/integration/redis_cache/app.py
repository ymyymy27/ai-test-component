"""本地进程集成夹具：Redis 风格缓存（前缀隔离/失效/FLUSHDB）+ SQLite 持久化。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DB_PATH = Path(os.environ.get("AITEST_FIXTURE_DB", "redis_fixture.sqlite3"))
CACHE_PREFIX = os.environ.get("AITEST_CACHE_PREFIX", "aitest:")
CACHE_STORE: dict[str, tuple[str, float]] = {}


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS products ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()


def cache_get(key: str) -> dict[str, Any] | None:
    full = f"{CACHE_PREFIX}{key}"
    entry = CACHE_STORE.get(full)
    if entry is None:
        return None
    raw, expires_at = entry
    if expires_at > 0 and time.monotonic() > expires_at:
        CACHE_STORE.pop(full, None)
        return None
    return json.loads(raw)


def cache_set(key: str, value: dict[str, Any], ttl_seconds: float = 30.0) -> None:
    full = f"{CACHE_PREFIX}{key}"
    CACHE_STORE[full] = (json.dumps(value, ensure_ascii=False), time.monotonic() + ttl_seconds)


def cache_delete(key: str) -> None:
    CACHE_STORE.pop(f"{CACHE_PREFIX}{key}", None)


def flush_all() -> None:
    CACHE_STORE.clear()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM products")
    conn.commit()
    conn.close()


class CacheHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: object) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any]:
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
            self._send(200, {"ok": True, "cache_keys": len(CACHE_STORE)})
            return
        if self.path.startswith("/api/products/"):
            pid = self.path.rsplit("/", 1)[-1]
            cached = cache_get(f"product:{pid}")
            if cached is not None:
                self._send(200, {**cached, "source": "cache"})
                return
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute("SELECT id, name FROM products WHERE id = ?", (pid,)).fetchone()
            conn.close()
            if row is None:
                self._send(404, {"error": "not_found"})
                return
            obj = {"id": row[0], "name": row[1]}
            cache_set(f"product:{pid}", obj)
            self._send(200, {**obj, "source": "db"})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path == "/_flush":
            flush_all()
            self._send(200, {"flushed": True, "cache_keys": len(CACHE_STORE)})
            return
        if self.path == "/api/products":
            body = self._read_json()
            name = str(body.get("name") or "").strip()
            if not name:
                self._send(400, {"error": "name_required"})
                return
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute("INSERT INTO products (name) VALUES (?)", (name,))
            conn.commit()
            pid = cur.lastrowid
            conn.close()
            self._send(201, {"id": pid, "name": name})
            return
        self._send(404, {"error": "not_found"})

    def do_PATCH(self) -> None:
        if self.path.startswith("/api/products/"):
            pid = self.path.rsplit("/", 1)[-1]
            body = self._read_json()
            name = str(body.get("name") or "").strip()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute("UPDATE products SET name = ? WHERE id = ?", (name, pid))
            conn.commit()
            updated = cur.rowcount
            conn.close()
            if updated == 0:
                self._send(404, {"error": "not_found"})
                return
            cache_delete(f"product:{pid}")
            self._send(200, {"id": int(pid), "name": name})
            return
        self._send(404, {"error": "not_found"})

    def do_DELETE(self) -> None:
        if self.path.startswith("/api/products/"):
            pid = self.path.rsplit("/", 1)[-1]
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute("DELETE FROM products WHERE id = ?", (pid,))
            conn.commit()
            deleted = cur.rowcount
            conn.close()
            cache_delete(f"product:{pid}")
            if deleted == 0:
                self._send(404, {"error": "not_found"})
                return
            self._send(204, {})
            return
        self._send(404, {"error": "not_found"})

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", port), CacheHandler)
    print(f"READY {server.server_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
