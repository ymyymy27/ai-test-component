"""针对大项目实机测试暴露的 6 项问题修复的回归单元测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from aitest.ai_features import collect_dependencies, judge_mock
from aitest.command_handlers.map_task_commands import (
    _compact_map_context,
    cmd_project_map_refresh,
)
from aitest.core.project_context import (
    build_capabilities_snapshot,
    cached_project_context,
)
from aitest.errors import AiTestError
from aitest.models import AcceptanceItem, Project, Task
from aitest.runtime.plan_commands import cmd_plan_generate
from aitest.runtime.snapshot import cmd_project_snapshot
from aitest.storage.files import write_json, write_model
from aitest.storage.workspace import Workspace


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_cached_project_context_and_reuse_api_routes(tmp_path: Path, monkeypatch) -> None:
    """cached_project_context 命中缓存后零扫描返回，且 build_capabilities_snapshot 不重算路由。"""
    repo = tmp_path / "repo"
    _write(repo, "pyproject.toml", "[project]\nname='demo'\ndependencies=['fastapi']\n")
    _write(
        repo,
        "app.py",
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/ping')\n"
        "def ping(): return {'ok': True}\n",
    )
    ws = Workspace(tmp_path / "ws")
    ws.init()

    calls = 0
    import aitest.core.project_context as pc_mod

    orig_build = pc_mod.build_project_context

    def _counting_build(root: Path, cfg: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return orig_build(root, cfg)

    monkeypatch.setattr(pc_mod, "build_project_context", _counting_build)

    ctx1 = cached_project_context(ws.root, "proj_1", repo, {})
    ctx2 = cached_project_context(ws.root, "proj_1", repo, {})
    assert calls == 1
    assert ctx1["api_routes"] == ctx2["api_routes"]

    def _forbid_rescan(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("不应该在已有 api_routes 时再次全库扫路由")

    monkeypatch.setattr(pc_mod, "_api_routes_with_meta", _forbid_rescan)
    cap = build_capabilities_snapshot(repo, {}, project_context=ctx2)
    assert "fastapi" in cap["backend"]


def test_project_snapshot_compacts_huge_code_graph(tmp_path: Path) -> None:
    """超过 300 节点的图谱在 project.snapshot 中默认瘦身，传 include_full_graph=True 返回全量。"""
    repo = tmp_path / "repo"
    _write(repo, "app.py", "def ok(): return True\n")
    ws = Workspace(tmp_path / "ws")
    ws.init()
    proj = Project(project_id="proj_big", name="big", root=str(repo))
    ws.save_project(proj)

    from aitest.core.code_graph import _GRAPH_SCHEMA_VERSION, _fast_source_hash

    nodes = [
        {"id": f"n_{i}", "path": f"mod_{i}.py", "kind": "api_route" if i < 5 else "module"}
        for i in range(450)
    ]
    edges = [{"source": f"n_{i}", "target": f"n_{i+1}", "kind": "import"} for i in range(449)]
    huge_graph = {
        "schema_version": _GRAPH_SCHEMA_VERSION,
        "source_hash": _fast_source_hash(repo),
        "nodes": nodes,
        "edges": edges,
        "stats": {"nodes": 450, "edges": 449},
        "conflicts": [],
        "module_chunks": [],
        "impact": {},
    }
    cache_graph = ws.root / ".cache" / "code_graph" / "proj_big" / "graph.json"
    write_json(cache_graph, huge_graph)
    write_json(
        ws.project_dir("proj_big") / "map.json",
        {
            "schema_version": 1,
            "project_id": "proj_big",
            "local_git": {"branch": "main", "head": "", "dirty": False},
            "graph": huge_graph,
        },
    )

    compact_snap = cmd_project_snapshot(ws, {"project_id": "proj_big"}, False)
    assert compact_snap["code_graph"]["truncated"] is True
    assert compact_snap["code_graph"]["total_nodes"] == 450
    assert len(compact_snap["code_graph"]["nodes"]) == 300

    full_snap = cmd_project_snapshot(
        ws, {"project_id": "proj_big", "include_full_graph": True}, False
    )
    assert len(full_snap["code_graph"]["nodes"]) == 450
    assert "truncated" not in full_snap["code_graph"]


def test_compact_map_context_and_map_refresh_retries_minimal(
    tmp_path: Path, monkeypatch
) -> None:
    """_compact_map_context 严控尺寸，且 project.map.refresh 在首次 JSON 报错时降级重试。"""
    big_ctx = {
        "framework": ["django"],
        "api_routes": [
            {"method": "GET", "path": f"/api/v3/pages/{i}", "auth": "public", "file": f"f{i}.py"}
            for i in range(200)
        ],
        "frontend_api_calls": [
            {"method": "GET", "path": f"/fe/{i}", "source": f"s{i}.ts"} for i in range(80)
        ],
        "test_modules": [f"tests/test_{i}.py" for i in range(100)],
        "test_functions": {f"f{i}.py": [f"test_{j}" for j in range(50)] for i in range(20)},
    }
    compact = _compact_map_context(big_ctx, max_chars=8000)
    assert compact["total_api_routes"] == 200
    assert len(compact["api_routes"]) <= 60
    assert "test_functions" not in compact
    assert len(json.dumps(compact, ensure_ascii=False)) <= 9000

    repo = tmp_path / "repo"
    _write(repo, "app.py", "def main(): pass\n")
    ws = Workspace(tmp_path / "ws")
    ws.init()
    proj = Project(project_id="proj_m", name="maptest", root=str(repo))
    ws.save_project(proj)

    class _RetryMapClient:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def project_map(self, context: str) -> dict[str, Any]:
            self.calls.append(len(context))
            if len(self.calls) == 1:
                raise AiTestError("MODEL_OUTPUT_INVALID", "模型输出不是合法 JSON", "重试")
            return {
                "business_functions": [
                    {
                        "name": "核心功能",
                        "modules": ["app"],
                        "entry_points": ["app.py"],
                        "test_commands": [],
                        "testable_items": ["入口可执行"],
                        "evidence": ["app.py"],
                        "confidence": "medium",
                    }
                ],
                "unknowns": [],
            }

    client = _RetryMapClient()
    monkeypatch.setattr("aitest.commands.build_client", lambda *_: client)
    monkeypatch.setattr("aitest.command_handlers.map_task_commands.build_client", lambda *_: client)
    out = cmd_project_map_refresh(ws, {"project_id": "proj_m"}, False)
    assert len(client.calls) == 2
    assert client.calls[1] < client.calls[0]
    assert len(out["map"]["business_functions"]) == 1


def test_collect_dependencies_filters_noise_domains_and_frontend_tools(tmp_path: Path) -> None:
    """collect_dependencies 过滤测试假域名和前端样式测试库，保留真实外部依赖。"""
    _write(
        tmp_path,
        "tests/test_urls.py",
        (
            "import redis\n"
            "import boto3\n"
            "URLS = [\n"
            "  'https://admin.example.com/a',\n"
            "  'https://a-very-long.example/b',\n"
            "  'http://a.co/x',\n"
            "  'https://foo.test/y',\n"
            "  'https://payments.stripe.com/v1/charges',\n"
            "]\n"
        ),
    )
    _write(
        tmp_path,
        "frontend/comp.test.ts",
        "import '@axe-core/react';\nimport 'autosize';\nimport 'axios';\n",
    )
    deps = collect_dependencies(tmp_path)
    assert "redis" in deps
    assert "boto3" in deps
    assert "axios" in deps
    assert "payments.stripe.com" in deps
    noises = (
        "admin.example.com",
        "a-very-long.example",
        "a.co",
        "foo.test",
        "@axe-core",
        "autosize",
    )
    for noise in noises:
        assert noise not in deps


def test_judge_mock_per_dependency_avoids_false_unknown_across_deps(tmp_path: Path) -> None:
    """多依赖合计超 max_files 时按单依赖小预算分批判定，不因一个热依赖连坐其它依赖标 unknown。"""
    for i in range(18):
        _write(tmp_path, f"pkg/hot_{i}.py", "import hot_dep\nhot_dep.call()\n")
    _write(tmp_path, "pkg/cache.py", "import redis\nr = redis.Redis()\n")
    _write(tmp_path, "pkg/storage.py", "import boto3\ns3 = boto3.client('s3')\n")

    class _MockJudgeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def complete_json(self, system: str, user: str) -> dict[str, Any]:
            del system
            payload = json.loads(user)
            self.calls.append(payload)
            return {
                "dependencies": [
                    {
                        "name": "redis",
                        "kind": "real",
                        "reason": "直接调用 redis.Redis",
                        "evidence_refs": ["pkg/cache.py"],
                    },
                    {
                        "name": "boto3",
                        "kind": "real",
                        "reason": "直接创建 boto3 客户端",
                        "evidence_refs": ["pkg/storage.py"],
                    },
                ]
            }

    client = _MockJudgeClient()
    result = judge_mock(
        tmp_path,
        ["hot_dep", "redis", "boto3"],
        [],
        client=client,  # type: ignore[arg-type]
        max_files=16,
    )
    by_name = {item["name"]: item for item in result}
    assert by_name["hot_dep"]["kind"] == "unknown"
    assert by_name["hot_dep"]["limit_reached"] is True
    assert by_name["redis"]["kind"] == "real"
    assert by_name["redis"]["read_files"] == ["pkg/cache.py"]
    assert by_name["boto3"]["kind"] == "real"
    assert by_name["boto3"]["read_files"] == ["pkg/storage.py"]


def test_plan_scope_custom_invariants_merge_and_no_cross_domain_override(
    tmp_path: Path, monkeypatch
) -> None:
    """验证未传 preset 时指定 item_ids 生效、有模型合入 invariant 且禁止跨资源串项替换。"""
    repo = tmp_path / "repo"
    _write(repo, "pyproject.toml", "[project]\nname='rbac'\ndependencies=['fastapi','pyjwt']\n")
    _write(
        repo,
        "app.py",
        "from fastapi import FastAPI, Depends\n"
        "def get_current_user(): return {'id': 1}\n"
        "app = FastAPI()\n"
        "@app.post('/items')\n"
        "def create_item(name: str, u=Depends(get_current_user)):\n"
        "    return {'id': 1, 'name': name}\n"
        "@app.get('/items')\n"
        "def list_items(u=Depends(get_current_user)): return []\n"
        "@app.get('/items/{item_id}')\n"
        "def get_item(item_id: int, u=Depends(get_current_user)): return {'id': item_id}\n"
        "@app.put('/items/{item_id}')\n"
        "def upd_item(item_id: int, name: str, u=Depends(get_current_user)):\n"
        "    return {'id': item_id}\n"
        "@app.delete('/items/{item_id}')\n"
        "def del_item(item_id: int, u=Depends(get_current_user)): return {'ok': True}\n"
        "@app.post('/auth/login')\n"
        "def login(username: str, password: str): return {'access_token': 't'}\n",
    )
    ws = Workspace(tmp_path / "ws")
    ws.init()
    proj = Project(project_id="proj_p", name="rbac", root=str(repo))
    ws.save_project(proj)
    task = Task(
        task_id="task_p",
        project_id="proj_p",
        title="RBAC 验收",
        acceptance_items=[
            AcceptanceItem(item_id="ai_crud", text="支持创建、查询列表、更新与删除持久化"),
            AcceptanceItem(item_id="ai_other", text="另一条独立验收项"),
        ],
    )
    write_model(ws.project_dir("proj_p") / "tasks" / "task_p.json", task)

    http_verify = (
        'http:[{"method":"GET","url":"http://127.0.0.1:8000/items",'
        '"expect":[{"path":"status","operator":"equals","value":200},'
        '{"path":"body","operator":"exists","value":true}]}]'
    )

    class _ModelStub:
        def generate_cases(
            self,
            task_dict: dict[str, Any],
            items: list[dict[str, Any]],
            ctx: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]:
            del task_dict, ctx
            return [
                {
                    "acceptance_item_id": item["item_id"],
                    "layer": "L2",
                    "title": f"模型用例 {item['item_id']}",
                    "expected": "操作成功并返回 200",
                    "expectation_source": item["text"],
                    "preconditions": ["后端已启动"],
                    "steps": ["发起请求"],
                    "verify_method": http_verify,
                }
                for item in items
            ]

    monkeypatch.setattr("aitest.runtime.plan_commands.build_client", lambda *_: _ModelStub())
    monkeypatch.setattr("aitest.runtime_commands.build_client", lambda *_: _ModelStub())
    out = cmd_plan_generate(
        ws,
        {
            "project_id": "proj_p",
            "task_id": "task_p",
            "scope": {"acceptance_item_ids": ["ai_crud"]},
        },
        False,
    )
    assert out["coverage"] == {"selected": 1, "applicable": 2}
    plan = ws.project_dir("proj_p") / "plans" / f"{out['plan_id']}.json"
    plan_data = json.loads(plan.read_text(encoding="utf-8"))
    sources = [c.get("generation", {}).get("source") for c in plan_data["cases"]]
    assert "invariant" not in sources
    assert "model" in sources


def test_cli_non_tty_open_pipe_without_data_does_not_hang(tmp_path: Path) -> None:
    """非 TTY 子进程打开 stdin 管道但不写入且不关闭写端时，doctor 不卡死且可正常通过管道传 JSON。"""
    ws_dir = tmp_path / "ws"
    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.Popen(
        [sys.executable, "-m", "aitest.cli", "doctor", "--workspace", str(ws_dir)],
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    started = time.monotonic()
    try:
        stdout_text, _ = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
    elapsed = time.monotonic() - started
    assert proc.returncode == 0
    assert elapsed < 10.0
    parsed = json.loads(stdout_text)
    assert parsed["ok"] is True

    # 通过管道正常传递 JSON 并关闭写端，也必须完整解析
    proc2 = subprocess.Popen(
        [sys.executable, "-m", "aitest.cli", "doctor", "--workspace", str(ws_dir)],
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    out2, _ = proc2.communicate(
        input=json.dumps({"request_id": "req_pipe_ok", "command": "doctor", "payload": {}}),
        timeout=15,
    )
    assert proc2.returncode == 0
    assert json.loads(out2)["request_id"] == "req_pipe_ok"


def test_related_files_by_dependency_single_pass_skips_code_graph(
    tmp_path: Path, monkeypatch
) -> None:
    """验证 related_files_by_dependency 单遍收集多依赖命中且绝不重建 build_code_graph。"""
    import aitest.core.code_graph as cg_mod

    for i in range(12):
        _write(tmp_path, f"src/hot_{i}.py", "import django\n")
    _write(tmp_path, "src/cache.py", "import redis\n")
    _write(tmp_path, "src/queue.py", "import celery\n")
    _write(
        tmp_path,
        "pyproject.toml",
        "[project]\nname='big'\ndependencies=['django','redis','celery']\n",
    )

    def _forbid_graph(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("related_files_by_dependency 不应调用 build_code_graph")

    monkeypatch.setattr(cg_mod, "build_code_graph", _forbid_graph)

    grouped = cg_mod.related_files_by_dependency(
        tmp_path,
        ["django", "redis", "celery", "unused_dep"],
        per_dep_cap=6,
    )
    assert len(grouped["django"]) == 6
    assert any(p.name == "cache.py" for p in grouped["redis"])
    assert any(p.name == "pyproject.toml" for p in grouped["redis"])
    assert any(p.name == "queue.py" for p in grouped["celery"])
    assert grouped["unused_dep"] == []

    # 单依赖超过 max_direct_hits 时 related_files_for_dependencies 同样短路且不建图
    hot_only = cg_mod.related_files_for_dependencies(
        tmp_path,
        ["django"],
        max_direct_hits=5,
    )
    assert len(hot_only) == 12
