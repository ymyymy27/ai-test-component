"""阶段 2 单元测试：只读本地 Git、4 大插槽、全栈代码图、共享配置与错题本。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aitest.commands import (
    cmd_project_add,
    cmd_project_config,
    cmd_project_map,
)
from aitest.core.code_graph import (
    apply_git_impact,
    build_code_graph,
    load_or_build_code_graph,
)
from aitest.core.git_engine import inspect_local_git, sanitize_remote_url
from aitest.core.judge import judge_case_failure
from aitest.core.plugins import (
    DEFAULT_REGISTRY,
    LocalFileSnapshotIsolator,
    PluginRegistry,
)
from aitest.core.probes import run_deterministic_probes
from aitest.core.script_linter import JsTsScriptLinter, lint_script
from aitest.core.shared_config import (
    extract_conventions,
    load_shared_repo_config,
    resolve_environment_profile,
)
from aitest.errors import AiTestError
from aitest.models import Case
from aitest.runtime._common import _project_context
from aitest.runtime.run_commands import cmd_run_quick
from aitest.runtime.snapshot import cmd_project_snapshot
from aitest.storage.workspace import Workspace


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return completed.stdout.strip()


def _create_fullstack_repo(repo: Path) -> None:
    """构建带前后端路由、数据模型与缺失静态资源的微型全栈仓库。"""
    (repo / "frontend").mkdir(parents=True, exist_ok=True)
    (repo / "backend").mkdir(parents=True, exist_ok=True)
    (repo / "static").mkdir(parents=True, exist_ok=True)
    (repo / "static" / "ok.css").write_text("body { margin: 0; }\n", encoding="utf-8")

    (repo / "frontend" / "index.html").write_text(
        (
            "<!doctype html>\n"
            "<html>\n"
            "<head>\n"
            '  <link rel="stylesheet" href="/static/ok.css">\n'
            '  <script src="/static/missing.js"></script>\n'
            "</head>\n"
            '<body><script src="./app.js"></script></body>\n'
            "</html>\n"
        ),
        encoding="utf-8",
    )
    (repo / "frontend" / "app.js").write_text(
        (
            "export async function loadOrders() {\n"
            '  await fetch("/api/orders");\n'
            '  return fetch("/api/ghost");\n'
            "}\n"
        ),
        encoding="utf-8",
    )
    (repo / "backend" / "models.py").write_text(
        (
            "class OrderRecord:\n"
            '    __tablename__ = "orders"\n'
            "    id = 1\n"
        ),
        encoding="utf-8",
    )
    (repo / "backend" / "routes.py").write_text(
        (
            "from backend import models\n\n"
            "class FakeRouter:\n"
            "    def get(self, _p):\n"
            "        return lambda fn: fn\n\n"
            "router = FakeRouter()\n\n"
            '@router.get("/api/orders")\n'
            "def list_orders():\n"
            '    return {"table": models.OrderRecord.__tablename__}\n'
        ),
        encoding="utf-8",
    )


def test_git_engine_inspects_real_repo_and_degrades_gracefully(tmp_path: Path) -> None:
    non_git = tmp_path / "plain_dir"
    non_git.mkdir()
    degraded = inspect_local_git(non_git)
    assert degraded["is_git"] is False
    assert degraded["available"] is False
    assert degraded["dirty"] is False

    assert (
        sanitize_remote_url("https://alice:top-secret-pw@git.example.com:8443/team/app.git")
        == "https://git.example.com:8443/team/app.git"
    )

    repo = tmp_path / "git_repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "remote", "add", "origin", "https://alice:super-secret@example.com/repo.git")
    (repo / "app.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(
        repo,
        "-c",
        "user.name=tester",
        "-c",
        "user.email=tester@example.com",
        "commit",
        "-m",
        "init app",
    )
    c1 = _git(repo, "rev-parse", "HEAD")

    (repo / "app.py").write_text(
        "def a():\n    return 1\n\ndef b():\n    return 2\n", encoding="utf-8"
    )
    _git(repo, "add", "app.py")
    _git(
        repo,
        "-c",
        "user.name=tester",
        "-c",
        "user.email=tester@example.com",
        "commit",
        "-m",
        "add b",
    )

    (repo / "app.py").write_text(
        "def a():\n    return 1\n\ndef b():\n    return 3\n", encoding="utf-8"
    )
    (repo / "scratch.py").write_text("x = 1\n", encoding="utf-8")

    info = inspect_local_git(repo, since_commit=c1)
    assert info["is_git"] is True
    assert info["available"] is True
    assert info["branch"]
    assert info["head"] and info["head"] != c1
    assert info["dirty"] is True
    assert "super-secret" not in info["remote_url"]
    assert "app.py" in info["changed_files"]
    assert "scratch.py" in info["untracked_files"]
    assert any(row["path"] == "app.py" for row in info["diff_stat"])
    assert any(h["path"] == "app.py" for h in info["changed_hunks"])
    assert any(h["path"] == "app.py" for h in info["hotspots"])


def test_plugins_slots_js_linter_and_file_snapshot_isolator(tmp_path: Path) -> None:
    assert isinstance(DEFAULT_REGISTRY, PluginRegistry)
    provider_ids = {p.provider_id for p in DEFAULT_REGISTRY.context_providers()}
    assert {"python_backend", "frontend_web", "openapi_spec"} <= provider_ids
    assert any(isinstance(iso, LocalFileSnapshotIsolator) for iso in DEFAULT_REGISTRY.isolators())

    linter = JsTsScriptLinter()
    assert any(i.rule == "empty_script" for i in linter.lint("   "))
    assert any(i.rule == "no_assertion" for i in linter.lint("console.log('ok');"))
    assert any(
        i.rule == "trivial_assertion"
        for i in lint_script("spec.ts", "expect(true).toBe(true);")
    )
    assert any(
        i.rule == "swallowed_exception"
        for i in lint_script("spec.js", "try { foo(); } catch (e) {}\nexpect(res.ok).toBe(true);")
    )
    clean_js = (
        "import { test, expect } from '@playwright/test';\n"
        "test('login', async () => {\n"
        "  const status = 200;\n"
        "  expect(status).toBe(200);\n"
        "});\n"
    )
    assert DEFAULT_REGISTRY.lint("login.spec.ts", clean_js) == []

    # LocalFileSnapshotIsolator：备份既有文件 + 清理运行后新建文件 + 防止路径穿越
    workdir = tmp_path / "app"
    run_dir = tmp_path / "run_001"
    workdir.mkdir()
    run_dir.mkdir()
    target = workdir / "local.data.json"
    target.write_text('{"balance": 100}', encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")

    isolator = LocalFileSnapshotIsolator()
    state = isolator.prepare(
        workdir,
        "run_001",
        run_dir,
        {"data": {"snapshot_files": ["local.data.json", "created_later.txt", "../outside.txt"]}},
    )
    target.write_text('{"balance": 999}', encoding="utf-8")
    (workdir / "created_later.txt").write_text("dirty", encoding="utf-8")

    isolator.cleanup(state)
    assert target.read_text(encoding="utf-8") == '{"balance": 100}'
    assert not (workdir / "created_later.txt").exists()
    assert outside.read_text(encoding="utf-8") == "keep"


def test_code_graph_layers_conflicts_git_impact_and_cache(tmp_path: Path) -> None:
    repo = tmp_path / "fullstack"
    repo.mkdir()
    _create_fullstack_repo(repo)
    ws = Workspace(tmp_path / "ws")

    graph = build_code_graph(repo, {})
    layers = {n["layer"] for n in graph["nodes"]}
    assert {"frontend", "api", "backend", "storage"} <= layers

    edge_kinds = {e["kind"] for e in graph["edges"]}
    assert "calls_api" in edge_kinds
    assert "handled_by" in edge_kinds
    assert "reads_writes" in edge_kinds

    # backend/routes.py 精确路由节点应记录非零行号
    api_node = next(
        n
        for n in graph["nodes"]
        if n["layer"] == "api" and "/api/orders" in n["label"]
    )
    assert api_node["file"] == "backend/routes.py"
    assert api_node["line"] >= 1

    # /api/ghost 与 /static/missing.js 进入跨层 conflicts
    conflict_details = [str(c.get("detail") or "") for c in graph["conflicts"]]
    assert any("/api/ghost" in d for d in conflict_details)
    assert any("missing.js" in d for d in conflict_details)
    assert len(graph["module_chunks"]) >= 2

    # Git 变更 backend/models.py 应触发直接与跨层 2 跳传播
    impacted = apply_git_impact(graph, {"changed_files": ["backend/models.py"]})
    assert impacted["impact"]["direct_node_ids"]
    storage_node = next(n for n in impacted["nodes"] if n["file"] == "backend/models.py")
    assert storage_node["impact"] == "direct"
    assert any(
        n["layer"] == "api" and n["impact"] == "transitive" for n in impacted["nodes"]
    )
    assert impacted["impact"]["affected_routes"]

    # load_or_build_code_graph 命中磁盘缓存并在源码变化后自动失效重建
    cached_first = load_or_build_code_graph(ws, "proj_demo", repo, {})
    cache_file = ws.root / ".cache" / "code_graph" / "proj_demo" / "graph.json"
    assert cache_file.is_file()
    cached_second = load_or_build_code_graph(ws, "proj_demo", repo, {})
    assert cached_second["source_hash"] == cached_first["source_hash"]
    assert cached_second["generated_at"] == cached_first["generated_at"]

    (repo / "backend" / "extra.py").write_text("def helper():\n    return 42\n", encoding="utf-8")
    rebuilt = load_or_build_code_graph(ws, "proj_demo", repo, {})
    assert rebuilt["source_hash"] != cached_first["source_hash"]


def test_shared_config_environments_and_conventions(tmp_path: Path) -> None:
    repo = tmp_path / "repo_cfg"
    repo.mkdir()
    shared_payload: dict[str, Any] = {
        "api_key": "sk-should-be-removed",
        "token": "ghp_secretvalue123456",
        "model": {"name": "gpt-4.1", "password": "secret-password"},
        "environments": {
            "active": "staging",
            "profiles": {
                "staging": {
                    "base_url": "https://staging.example.com/",
                    "env": {"APP_ENV": "staging", "secret_key": "never-leak"},
                    "allowed_hosts": ["staging.example.com"],
                }
            },
        },
        "conventions": [
            {
                "id": "conv_cdn",
                "pattern": "missing.js",
                "reason": "构建产物运行时生成，开发态不存在属预期行为",
                "scope": "probe",
            }
        ],
    }
    (repo / ".aitest.json").write_text(
        json.dumps(shared_payload, ensure_ascii=False), encoding="utf-8"
    )

    loaded_shared = load_shared_repo_config(repo)
    assert "api_key" not in loaded_shared
    assert "token" not in loaded_shared
    assert "password" not in loaded_shared.get("model", {})

    ws = Workspace(tmp_path / "ws")
    added = cmd_project_add(
        ws, {"name": "SharedDemo", "root": str(repo)}, confirm=False
    )
    project_id = added["project_id"]

    cfg_res = cmd_project_config(ws, {"project_id": project_id, "action": "get"}, False)
    assert cfg_res["shared_config_loaded"] is True
    assert cfg_res["config"]["environments"]["active"] == "staging"

    profile = resolve_environment_profile(cfg_res["config"], "staging")
    assert profile["found"] is True
    assert profile["base_url"] == "https://staging.example.com"
    assert "secret_key" not in profile["env"]

    missing_profile = resolve_environment_profile(cfg_res["config"], "unknown_env")
    assert missing_profile["found"] is False

    # 请求不存在的环境档案应由 run.quick 拒绝并给出 INVALID_REQUEST
    with pytest.raises(AiTestError) as exc_info:
        cmd_run_quick(
            ws,
            {"project_id": project_id, "environment": "nonexistent_env"},
            confirm=False,
        )
    assert exc_info.value.info.code == "INVALID_REQUEST"

    # add_convention 与 remove_convention 正常持久化
    added_conv = cmd_project_config(
        ws,
        {
            "project_id": project_id,
            "action": "add_convention",
            "pattern": "429 Too Many Requests",
            "reason": "压测限流白名单",
            "scope": "case",
        },
        False,
    )
    convs = added_conv["conventions"]
    assert any(c["pattern"] == "429 Too Many Requests" for c in convs)

    # 裁判与探针分别消费错题本进行精准降噪
    case = Case(
        case_id="case_rate",
        layer="L2",
        title="限流场景",
        acceptance_item_ids=["ac_1"],
        expectation_source="正常返回或限流",
        expected="接口返回 200",
        verify_method="api",
        script_name="test_rate.py",
        script_content="assert True",
    )
    verdict = judge_case_failure(
        ws,
        project_id,
        case=case,
        outputs=[b"HTTP 429 Too Many Requests from gateway"],
        message="status != 200",
        service_log_issues=[],
        probe_results=[],
        secrets=[],
    )
    assert verdict["source"] == "convention"
    assert verdict["verdict"] == "inconclusive"

    probe_repo = tmp_path / "probe_repo"
    probe_repo.mkdir()
    _create_fullstack_repo(probe_repo)
    probe_out = run_deterministic_probes(
        probe_repo,
        {
            "conventions": [
                {
                    "id": "allow_ghost",
                    "pattern": "/api/ghost",
                    "reason": "网关代理路径",
                    "scope": "probe",
                }
            ]
        },
    )
    contract_probe = next(p for p in probe_out if p["probe_id"] == "contract_reconciliation")
    assert contract_probe["status"] == "passed"
    assert len(contract_probe.get("suppressed_findings", [])) == 1

    # 删除新增错题本条目
    target_id = next(c["id"] for c in convs if c["pattern"] == "429 Too Many Requests")
    removed = cmd_project_config(
        ws,
        {"project_id": project_id, "action": "remove_convention", "id": target_id},
        False,
    )
    assert all(c["id"] != target_id for c in removed["conventions"])
    reloaded_cfg = cmd_project_config(
        ws, {"project_id": project_id, "action": "get"}, False
    )
    assert all(
        c["id"] != target_id for c in extract_conventions(reloaded_cfg["config"])
    )


def test_map_snapshot_and_project_context_integration(tmp_path: Path) -> None:
    repo = tmp_path / "integrated"
    repo.mkdir()
    _create_fullstack_repo(repo)
    (repo / ".aitest.json").write_text(
        json.dumps(
            {
                "conventions": [
                    {
                        "id": "conv_ok",
                        "pattern": "预期警告",
                        "reason": "已知可忽略",
                        "scope": "all",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _git(repo, "init")
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=tester",
        "-c",
        "user.email=tester@example.com",
        "commit",
        "-m",
        "initial fullstack",
    )

    ws = Workspace(tmp_path / "ws")
    added = cmd_project_add(
        ws, {"name": "Integrated", "root": str(repo)}, confirm=False
    )
    project_id = added["project_id"]

    # 修改文件制造脏工作区改动
    (repo / "backend" / "models.py").write_text(
        'class OrderRecord:\n    __tablename__ = "orders"\n    id = 2\n',
        encoding="utf-8",
    )

    map_res = cmd_project_map(ws, {"project_id": project_id, "action": "show"}, False)
    assert map_res["local_git"]["is_git"] is True
    assert map_res["local_git"]["dirty"] is True
    assert map_res["graph"]["stats"]["api"] >= 1

    snap = cmd_project_snapshot(ws, {"project_id": project_id}, False)
    assert snap["local_git"]["is_git"] is True
    assert snap["code_graph"]["stats"]["storage"] >= 1
    assert "backend/models.py" in snap["code_graph"]["impact"]["changed_files"]

    from aitest.commands import _project

    proj_obj = _project(ws, {"project_id": project_id})
    ctx = _project_context(proj_obj, ws)
    assert len(ctx["conventions"]) == 1
    assert ctx["code_graph_summary"]["stats"]["frontend"] >= 1
