"""第 3 步单测：只读 capabilities 快照、稳定指纹、D3 严格只读与 T1-T4 / D1-D4 识别。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from aitest.core.project_context import build_capabilities_snapshot
from aitest.models import Project
from aitest.runtime.snapshot import cmd_project_snapshot
from aitest.storage.workspace import Workspace


def test_capabilities_snapshot_is_read_only_and_fingerprint_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D3：默认发现与快照绝不拉起任何子进程，且重复计算指纹完全稳定。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["fastapi", "uvicorn", "redis"]\n',
        encoding="utf-8",
    )
    (repo / "app.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/health")\n'
        'def ok(): return {"ok": True}\n',
        encoding="utf-8",
    )
    (repo / "docker-compose.yml").write_text(
        "services:\n  cache:\n    image: redis:7\n",
        encoding="utf-8",
    )

    def _forbid_popen(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("只读 capabilities 快照禁止拉起任何子进程")

    monkeypatch.setattr(subprocess, "Popen", _forbid_popen)

    cap1 = build_capabilities_snapshot(repo, {})
    cap2 = build_capabilities_snapshot(repo, {})
    assert cap1["source"] == "static"
    assert cap1["degraded"] is False
    assert cap1["level"] == "T1"
    assert "fastapi" in cap1["backend"]
    assert any(d["type"] == "redis" and d["level"] == "D1" for d in cap1["dependencies"])
    assert cap1["fingerprint"].startswith("sha256:")
    assert cap1["fingerprint"] == cap2["fingerprint"]


def test_runtime_introspect_failure_degrades_back_to_static(tmp_path: Path) -> None:
    """显式升级运行时内省失败时平滑回退静态事实并标记 degraded=True。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "openapi.json").write_text(
        '{"openapi":"3.0.3","paths":{"/ping":{"get":{"responses":{"200":{"description":"ok"}}}}}}',
        encoding="utf-8",
    )
    cap = build_capabilities_snapshot(
        repo,
        {"services": {"backend": {"health": "http://127.0.0.1:1/health"}}},
        runtime_introspect=True,
    )
    assert cap["source"] == "static"
    assert cap["degraded"] is True
    assert "runtime_introspect_failed" in cap["unknowns"]
    assert cap["level"] == "T2"


def test_t4_protocol_marked_unsupported_not_hidden(tmp_path: Path) -> None:
    """T4 明确不支持的协议（如 GraphQL / gRPC）显式记录到 unsupported。"""
    repo = tmp_path / "grpc_repo"
    repo.mkdir()
    (repo / "service.proto").write_text('syntax = "proto3";\nservice Demo {}\n', encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[project]\ndependencies = ["grpcio", "strawberry"]\n', encoding="utf-8"
    )
    cap = build_capabilities_snapshot(repo, {})
    assert cap["level"] == "T4"
    assert "grpc" in cap["unsupported"]
    assert "graphql" in cap["unsupported"]


def test_project_snapshot_returns_capabilities_and_health_summary(tmp_path: Path) -> None:
    """project.snapshot 命令返回只读 capabilities 与精简 health_summary。"""
    ws = Workspace(tmp_path / "ws")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["flask"]\n', encoding="utf-8"
    )
    (repo / "app.py").write_text(
        'from flask import Flask\napp = Flask(__name__)\n@app.route("/api/ping")\n'
        'def ping(): return "ok"\n',
        encoding="utf-8",
    )
    proj = Project(project_id="proj_snap", name="demo", root=str(repo))
    ws.save_project(proj)

    snap = cmd_project_snapshot(ws, {"project_id": "proj_snap"}, False)
    assert snap["capabilities"]["level"] == "T1"
    assert "flask" in snap["capabilities"]["backend"]
    assert snap["health_summary"]["route_count"] >= 1
    assert snap["health_summary"]["fingerprint"] == snap["capabilities"]["fingerprint"]
