"""第 6 步集成夹具测试：3 个本地进程真实服务回归（jwt_negative_401 / multi_service / redis_cache）。

纯本地子进程执行，绝不访问外网，常驻默认 pytest。
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from aitest.runners.http_runner import run_http_sequence

ZOO_INT_ROOT = Path("tests/fixtures/zoo/integration")


@contextmanager
def _spawn_fixture(script: Path, *, env_extra: dict[str, str] | None = None) -> Iterator[int]:
    env = {**os.environ, **(env_extra or {})}
    proc = subprocess.Popen(
        [sys.executable, "-u", str(script), "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    try:
        assert proc.stdout is not None
        ready_line = proc.stdout.readline().strip()
        assert ready_line.startswith("READY "), f"服务未就绪：{ready_line!r}"
        port = int(ready_line.split(" ", 1)[1])
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_zoo_integration_jwt_negative_401_and_sqlite_reset(tmp_path: Path) -> None:
    """验证 JWT 负向 401/403、合法登录创建读回以及 SQLite 复位隔离。"""
    db_file = tmp_path / "jwt.sqlite3"
    with _spawn_fixture(
        ZOO_INT_ROOT / "jwt_negative_401" / "server.py",
        env_extra={"AITEST_FIXTURE_DB": str(db_file)},
    ) as port:
        base = f"http://127.0.0.1:{port}"
        res = run_http_sequence(
            [
                {
                    "method": "GET",
                    "url": f"{base}/api/secrets",
                    "stage": "assert",
                    "expect": [{"path": "status", "operator": "equals", "value": 401}],
                },
                {
                    "method": "POST",
                    "url": f"{base}/api/login",
                    "stage": "setup",
                    "json": {"username": "viewer", "password": "viewer123"},
                    "extract": {"viewer_token": "token"},
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                },
                {
                    "method": "POST",
                    "url": f"{base}/api/secrets",
                    "stage": "assert",
                    "headers": {"Authorization": "Bearer ${vars.viewer_token}"},
                    "json": {"title": "forbidden-item"},
                    "expect": [{"path": "status", "operator": "equals", "value": 403}],
                },
                {
                    "method": "POST",
                    "url": f"{base}/api/login",
                    "stage": "setup",
                    "json": {"username": "admin", "password": "admin123"},
                    "extract": {"admin_token": "token"},
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                },
                {
                    "method": "POST",
                    "url": f"{base}/api/secrets",
                    "stage": "assert",
                    "headers": {"Authorization": "Bearer ${vars.admin_token}"},
                    "json": {"title": "secret-1"},
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 201},
                        {"path": "title", "operator": "equals", "value": "secret-1"},
                    ],
                },
                {
                    "method": "POST",
                    "url": f"{base}/_reset",
                    "stage": "assert",
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                },
                {
                    "method": "GET",
                    "url": f"{base}/api/secrets",
                    "stage": "assert",
                    "headers": {"Authorization": "Bearer ${vars.admin_token}"},
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 200},
                        {"path": "items", "operator": "equals", "value": []},
                    ],
                },
            ]
        )
        assert res.status == "passed", res.error


def test_zoo_integration_multi_service_role_health_and_isolation() -> None:
    """验证前后端分离多服务独立进程启动、JSON API 与 HTML SPA 健康探针匹配。"""
    with (
        _spawn_fixture(ZOO_INT_ROOT / "multi_service" / "backend.py") as be_port,
        _spawn_fixture(ZOO_INT_ROOT / "multi_service" / "frontend.py") as fe_port,
    ):
        res = run_http_sequence(
            [
                {
                    "method": "GET",
                    "url": f"http://127.0.0.1:{be_port}/health",
                    "stage": "assert",
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 200},
                        {"path": "service", "operator": "equals", "value": "backend"},
                    ],
                },
                {
                    "method": "GET",
                    "url": f"http://127.0.0.1:{be_port}/api/items",
                    "stage": "assert",
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 200},
                        {"path": "items.0.name", "operator": "equals", "value": "demo"},
                    ],
                },
                {
                    "method": "GET",
                    "url": f"http://127.0.0.1:{fe_port}/",
                    "stage": "assert",
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 200},
                    ],
                },
            ]
        )
        assert res.status == "passed", res.error
        assert b"MultiService SPA" in res.stdout


def test_zoo_integration_redis_cache_invalidation_and_flush_reset(tmp_path: Path) -> None:
    """验证创建读回源、缓存命中、更新失效、删除防脏读与 FLUSHDB/SQLite 复位隔离。"""
    db_file = tmp_path / "redis_cache.sqlite3"
    with _spawn_fixture(
        ZOO_INT_ROOT / "redis_cache" / "app.py",
        env_extra={
            "AITEST_FIXTURE_DB": str(db_file),
            "AITEST_CACHE_PREFIX": "run_iso:",
        },
    ) as port:
        base = f"http://127.0.0.1:{port}"
        res = run_http_sequence(
            [
                {
                    "method": "POST",
                    "url": f"{base}/api/products",
                    "stage": "assert",
                    "json": {"name": "widget-v1"},
                    "extract": {"pid": "id"},
                    "expect": [{"path": "status", "operator": "equals", "value": 201}],
                },
                # 首次读走 db 并写缓存
                {
                    "method": "GET",
                    "url": f"{base}/api/products/${{vars.pid}}",
                    "stage": "assert",
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 200},
                        {"path": "source", "operator": "equals", "value": "db"},
                        {"path": "name", "operator": "equals", "value": "widget-v1"},
                    ],
                },
                # 二次读命中 cache
                {
                    "method": "GET",
                    "url": f"{base}/api/products/${{vars.pid}}",
                    "stage": "assert",
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 200},
                        {"path": "source", "operator": "equals", "value": "cache"},
                    ],
                },
                # PATCH 更新后缓存必须失效，重新读回新值
                {
                    "method": "PATCH",
                    "url": f"{base}/api/products/${{vars.pid}}",
                    "stage": "assert",
                    "json": {"name": "widget-v2"},
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                },
                {
                    "method": "GET",
                    "url": f"{base}/api/products/${{vars.pid}}",
                    "stage": "assert",
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 200},
                        {"path": "source", "operator": "equals", "value": "db"},
                        {"path": "name", "operator": "equals", "value": "widget-v2"},
                    ],
                },
                # DELETE 删除后防脏读，返回 404
                {
                    "method": "DELETE",
                    "url": f"{base}/api/products/${{vars.pid}}",
                    "stage": "assert",
                    "expect": [{"path": "status", "operator": "equals", "value": 204}],
                },
                {
                    "method": "GET",
                    "url": f"{base}/api/products/${{vars.pid}}",
                    "stage": "assert",
                    "expect": [{"path": "status", "operator": "equals", "value": 404}],
                },
                # FLUSH 复位后缓存清空
                {
                    "method": "POST",
                    "url": f"{base}/_flush",
                    "stage": "assert",
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 200},
                        {"path": "cache_keys", "operator": "equals", "value": 0},
                    ],
                },
            ]
        )
        assert res.status == "passed", res.error
