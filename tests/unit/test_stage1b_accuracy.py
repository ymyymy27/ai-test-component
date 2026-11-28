"""阶段 1B 高准确度测试闭环内核单元测试。

覆盖开测前环境安检门、盲写预期物理隔离、免 AI 确定性硬探针、
setup/verify 两阶段与 AST 安检、后台日志窗口联动、防抖重跑+独立裁判+三档可信度、
以及用例级 Checkpoint 断点续跑。
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from aitest.cli import main as cli_main
from aitest.core.preflight import order_services, run_preflight
from aitest.core.probes import run_deterministic_probes
from aitest.core.script_linter import blocking_lint_errors, lint_script
from aitest.errors import AiTestError
from aitest.models import Issue
from aitest.runtime_commands import cmd_plan_generate, cmd_run_start
from aitest.storage.files import read_model, write_json
from aitest.storage.workspace import Workspace
from aitest.worker_impl.lifecycle import main as worker_main


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)
    monkeypatch.delenv("AITEST_GITHUB_TOKEN", raising=False)


def _call_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str], request: dict[str, Any]
) -> dict[str, Any]:
    req_file = tmp_path / "req.json"
    req_file.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    cli_main([*argv, "--workspace", str(tmp_path / "ws"), "--input", str(req_file)])
    return json.loads(capsys.readouterr().out.strip())  # type: ignore[no-any-return]


def _bootstrap_project_task(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    proj = _call_cli(
        tmp_path,
        capsys,
        ["project.add"],
        {
            "command": "project.add",
            "request_id": "p1",
            "payload": {"name": "s1b", "root": str(root)},
        },
    )["data"]
    task = _call_cli(
        tmp_path,
        capsys,
        ["task.add"],
        {
            "command": "task.add",
            "request_id": "t1",
            "payload": {
                "project_id": proj["project_id"],
                "title": "阶段 1B 闭环验证",
                "intent": "business",
                "acceptance_items": items,
            },
        },
    )["data"]
    return {"root": root, "project": proj, "task": task}


def _wait_run_settled(
    ws: Workspace, project_id: str, run_id: str, *, timeout: float = 20.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    run_file = ws.project_dir(project_id) / "runs" / run_id / "run.json"
    while time.monotonic() < deadline:
        if run_file.is_file():
            data = json.loads(run_file.read_text(encoding="utf-8"))
            if data.get("status") != "running" and data.get("postprocess") in {
                "done",
                "error",
            }:
                return data  # type: ignore[no-any-return]
        time.sleep(0.05)
    raise TimeoutError(f"等待运行 {run_id} 结束超时")


def test_preflight_order_port_conflict_reuse_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. 多服务拓扑排序：默认 DB -> 后端 -> 前端，并支持显式 depends_on
    ordered = order_services(
        [
            ("frontend", {"start": "npm run dev"}),
            ("worker", {"start": "python worker.py", "depends_on": ["frontend"]}),
            ("backend", {"start": "uvicorn app:app"}),
            ("postgres", {"start": "pg_ctl start"}),
        ]
    )
    assert [name for name, _ in ordered] == ["postgres", "backend", "frontend", "worker"]

    # 2. 端口检查：healthy 服务走 reuse，占用但健康检查失败走 port_conflict
    class _OkHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    ok_server = ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    ok_port = ok_server.server_address[1]
    ok_thread = threading.Thread(target=ok_server.serve_forever, daemon=True)
    ok_thread.start()

    busy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy_sock.bind(("127.0.0.1", 0))
    busy_sock.listen(1)
    busy_port = busy_sock.getsockname()[1]

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]

    try:
        (tmp_path / ".env.example").write_text(
            "PRESENT_ENV=\nOPTIONAL_MISSING=\nWITH_DEFAULT=demo\n", encoding="utf-8"
        )
        run_env = {
            "PATH": str(Path(sys.executable).parent),
            "PRESENT_ENV": "super-secret-value-999",
        }
        monkeypatch.setenv("PATH", run_env["PATH"])
        result = run_preflight(
            tmp_path,
            {"required_env": ["PRESENT_ENV", "REQUIRED_SECRET_TOKEN"]},
            [
                (
                    "frontend",
                    {"start": f'"{sys.executable}" -V', "health": f"http://127.0.0.1:{free_port}/"},
                ),
                (
                    "backend",
                    {"start": f'"{sys.executable}" -V', "health": f"http://127.0.0.1:{busy_port}/health"},
                ),
                (
                    "redis",
                    {"start": "nonexistent_tool_xyz_123", "health": f"http://127.0.0.1:{ok_port}/"},
                ),
            ],
            run_env,
        )
    finally:
        ok_server.shutdown()
        ok_server.server_close()
        busy_sock.close()

    assert result["ok"] is False
    assert result["reused_services"] == ["redis"]
    assert result["conflicted_services"] == ["backend"]
    assert any("REQUIRED_SECRET_TOKEN" in reason for reason in result["blocking_reasons"])
    assert any("OPTIONAL_MISSING" in w for w in result["warnings"])
    assert "super-secret-value-999" not in json.dumps(result, ensure_ascii=False)


def test_deterministic_probes_catch_contract_assets_and_500(tmp_path: Path) -> None:
    repo = tmp_path / "fullstack"
    (repo / "frontend").mkdir(parents=True)
    (repo / "frontend" / "index.html").write_text(
        "<!doctype html><html>"
        "<link rel='stylesheet' href='/static/missing.css'>"
        "<script src='/static/spa_fake.js'></script>"
        "<script src='/static/ok.js'></script>"
        "</html>",
        encoding="utf-8",
    )
    (repo / "frontend" / "api.js").write_text(
        "fetch('/api/items', {method: 'POST', body: JSON.stringify({name: 'n1'})});\n"
        "fetch('/api/typo', {method: 'GET'});\n",
        encoding="utf-8",
    )
    (repo / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/api/items": {
                        "post": {
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {"properties": {"title": {"type": "string"}}}
                                    }
                                }
                            },
                            "responses": {"201": {"description": "created"}},
                        }
                    },
                    "/api/boom": {"get": {"responses": {"200": {"description": "boom"}}}},
                },
            }
        ),
        encoding="utf-8",
    )

    class _BuggyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<link rel='stylesheet' href='/static/missing.css'>"
                    b"<script src='/static/spa_fake.js'></script>"
                    b"<script src='/static/ok.js'></script>"
                )
                return
            if self.path == "/static/missing.css":
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/static/spa_fake.js":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body>SPA Index Fallback</body></html>")
                return
            if self.path == "/static/ok.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                self.wfile.write(b"console.log('ok');")
                return
            if self.path == "/api/boom":
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'Traceback (most recent call last):\nRuntimeError: boom')
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        results = run_deterministic_probes(
            repo,
            {},
            base_url=f"http://127.0.0.1:{port}",
            service_ready=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    by_id = {entry["probe_id"]: entry for entry in results}
    assert "contract_reconciliation" not in by_id

    assert by_id["static_assets"]["status"] == "failed"
    asset_kinds = {f["kind"] for f in by_id["static_assets"]["findings"]}
    assert {"asset_http_error", "asset_mime_mismatch"} <= asset_kinds

    assert by_id["route_boundary"]["status"] == "failed"
    assert any(
        f["kind"] == "server_error_or_traceback" and f["path"] == "/api/boom"
        for f in by_id["route_boundary"]["findings"]
    )


def test_ast_linter_and_two_stage_setup_verify_isolation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 1. AST 静态安检阻断水脚本与保留合法 4xx 检验
    assert blocking_lint_errors("bad1.py", "def f(:\n  pass\n")
    assert blocking_lint_errors("bad2.py", "x = 1 + 1\nprint(x)\n")
    assert blocking_lint_errors("bad3.py", "assert True\n")
    assert blocking_lint_errors("bad4.py", "x = 1\nassert x == x\n")
    assert blocking_lint_errors(
        "bad5.py",
        "try:\n    do_work()\nexcept Exception:\n    pass\nassert 1 != 2\n",
    )
    assert not blocking_lint_errors(
        "good_4xx.py",
        "import urllib.error\ntry:\n    call()\nexcept urllib.error.HTTPError as e:\n"
        "    assert 400 <= e.code < 500\n",
    )
    write_issues = lint_script(
        "shallow_post.py",
        "r = session.post('http://127.0.0.1:8000/items', json={'a': 1})\n"
        "assert r.status_code == 201\n",
    )
    assert any(i.rule == "write_without_verify" and i.severity == "warning" for i in write_issues)

    # 2. 两阶段 setup/verify 执行隔离
    setup = _bootstrap_project_task(
        tmp_path,
        capsys,
        [
            {"text": "验收项一：前置准备异常时不误报产品缺陷", "observable": True},
            {"text": "验收项二：前置成功但核心断言失败时准确报失败", "observable": True},
        ],
    )
    items = setup["task"]["acceptance_items"]
    setup_fail_script = (
        "def setup():\n"
        "    raise RuntimeError('测试前置数据尚未准备好')\n"
        "def verify(ctx):\n"
        "    assert ctx['ok'] is True\n"
    )
    verify_fail_script = (
        "def setup():\n"
        "    return {'value': 10}\n"
        "def verify(ctx):\n"
        "    assert ctx['value'] == 999\n"
    )
    ws = Workspace(tmp_path / "ws")
    plan_res = cmd_plan_generate(
        ws,
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "all_applicable"},
            "cases": [
                {
                    "case_id": "case_setup_fail",
                    "acceptance_item_id": items[0]["item_id"],
                    "layer": "L2",
                    "title": "setup 失败",
                    "expected": "前置通过后验证",
                    "expectation_source": items[0]["text"],
                    "preconditions": ["可访问"],
                    "verify_method": "script:s1.py",
                    "script_name": "s1.py",
                    "script_content": setup_fail_script,
                },
                {
                    "case_id": "case_verify_fail",
                    "acceptance_item_id": items[1]["item_id"],
                    "layer": "L2",
                    "title": "verify 失败",
                    "expected": "等于 999",
                    "expectation_source": items[1]["text"],
                    "preconditions": ["可访问"],
                    "verify_method": "script:s2.py",
                    "script_name": "s2.py",
                    "script_content": verify_fail_script,
                },
            ],
        },
        True,
    )
    run_res = cmd_run_start(
        ws,
        {"project_id": setup["project"]["project_id"], "plan_id": plan_res["plan_id"]},
        True,
    )
    run_data = _wait_run_settled(ws, setup["project"]["project_id"], run_res["run_id"])
    by_case = {entry["case_id"]: entry for entry in run_data["cases"]}
    assert by_case["case_setup_fail"]["result"] == "blocked"
    assert by_case["case_setup_fail"]["attempts"][-1]["stage"] == "setup"
    assert by_case["case_verify_fail"]["result"] == "failed"
    assert by_case["case_verify_fail"]["attempts"][-1]["stage"] == "verify"


def test_worker_service_log_window_catches_silent_500(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = _bootstrap_project_task(
        tmp_path,
        capsys,
        [{"text": "提交数据不应引发后端未捕获异常", "observable": True}],
    )
    root: Path = setup["root"]
    (root / "silent_srv.py").write_text(
        "import sys, time\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
        "    def do_POST(self):\n"
        "        sys.stdout.write(\n"
        "            'Traceback (most recent call last):\\nRuntimeError: db oops\\n'\n"
        "        )\n"
        "        sys.stdout.write('\"POST /api/save HTTP/1.1\" 500 -\\n')\n"
        "        sys.stdout.flush()\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'{\"ok\": true}')\n"
        "    def log_message(self, *a): return\n"
        "ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()\n",
        encoding="utf-8",
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    ws = Workspace(tmp_path / "ws")
    pid = setup["project"]["project_id"]
    write_json(
        ws.project_dir(pid) / "project.config.json",
        {
            "schema_version": 1,
            "services": {
                "backend": {
                    "start": f'"{sys.executable}" silent_srv.py {port}',
                    "health": f"http://127.0.0.1:{port}/",
                }
            },
        },
    )
    item = setup["task"]["acceptance_items"][0]
    plan_res = cmd_plan_generate(
        ws,
        {
            "project_id": pid,
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "all_applicable"},
            "cases": [
                {
                    "case_id": "case_silent_500",
                    "acceptance_item_id": item["item_id"],
                    "layer": "L2",
                    "title": "保存接口不崩溃",
                    "expected": "后台正常处理且无 500 堆栈",
                    "expectation_source": item["text"],
                    "preconditions": ["服务已启动"],
                    "depends_on": "service",
                    "verify_method": "script:call_save.py",
                    "script_name": "call_save.py",
                    "script_content": (
                        "import json, urllib.request\n"
                        f"url = 'http://127.0.0.1:{port}/api/save'\n"
                        "req = urllib.request.Request(url, data=b'{}', method='POST')\n"
                        "with urllib.request.urlopen(req) as resp:\n"
                        "    body = json.loads(resp.read().decode())\n"
                        "    assert resp.status == 200 and body['ok'] is True\n"
                    ),
                }
            ],
        },
        True,
    )
    monkeypatch.setattr("aitest.runtime.run_commands._spawn_worker", lambda *a, **k: object())
    run_res = cmd_run_start(ws, {"project_id": pid, "plan_id": plan_res["plan_id"]}, True)
    rc_code = worker_main(
        ["--workspace", str(ws.root), "--project", pid, "--run", run_res["run_id"]]
    )
    assert rc_code == 0

    run_data = json.loads(
        (ws.project_dir(pid) / "runs" / run_res["run_id"] / "run.json").read_text(encoding="utf-8")
    )
    rc = run_data["cases"][0]
    # 阶段 2：后台日志 500/Traceback 作为诊断附件保留，不再强行翻盘已通过断言的用例
    assert rc["result"] == "passed"
    assert any("500" in msg or "未捕获异常" in msg for msg in rc["service_log_issues"])
    assert any(ref.endswith("service-window.log") for ref in rc["evidence_refs"])


def test_retry_independent_judge_and_confidence_tiers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = _bootstrap_project_task(
        tmp_path,
        capsys,
        [
            {"text": "项1：首次失败第二次重跑通过应标疑似不稳定", "observable": True},
            {"text": "项2：连续失败裁判判脚本错可修一次重跑通过", "observable": True},
            {"text": "项3：裁判判证据不足应收敛为阻塞不洗白", "observable": True},
            {"text": "项4：连续失败且确认产品缺陷写入铁证缺陷", "observable": True},
        ],
    )
    items = setup["task"]["acceptance_items"]
    root: Path = setup["root"]
    ws = Workspace(tmp_path / "ws")
    pid = setup["project"]["project_id"]
    flaky_flag = root / "flaky_once.flag"

    flaky_script = (
        f"from pathlib import Path\n"
        f"flag = Path({str(flaky_flag)!r})\n"
        "if not flag.exists():\n"
        "    flag.write_text('1', encoding='utf-8')\n"
        "    assert False, 'transient timeout'\n"
        "val = 42\n"
        "assert val == 42\n"
    )
    bad_script_to_fix = "val = 1\nassert val == 999\n"
    fixed_script_code = "val = 999\nassert val == 999\n"
    inconclusive_script = "raise RuntimeError('环境数据未就绪: 依赖服务连接拒绝')\n"
    confirmed_script = "actual = 'wrong'\nassert actual == 'right'\n"

    plan_res = cmd_plan_generate(
        ws,
        {
            "project_id": pid,
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "all_applicable"},
            "cases": [
                {
                    "case_id": "c_flaky",
                    "acceptance_item_id": items[0]["item_id"],
                    "layer": "L2",
                    "title": "抖动用例",
                    "expected": "通过",
                    "expectation_source": items[0]["text"],
                    "preconditions": ["无"],
                    "verify_method": "script:flaky.py",
                    "script_name": "flaky.py",
                    "script_content": flaky_script,
                },
                {
                    "case_id": "c_script_err",
                    "acceptance_item_id": items[1]["item_id"],
                    "layer": "L2",
                    "title": "脚本可修用例",
                    "expected": "val == 999",
                    "expectation_source": items[1]["text"],
                    "preconditions": ["无"],
                    "verify_method": "script:fixable.py",
                    "script_name": "fixable.py",
                    "script_content": bad_script_to_fix,
                },
                {
                    "case_id": "c_inconclusive",
                    "acceptance_item_id": items[2]["item_id"],
                    "layer": "L2",
                    "title": "证据不足用例",
                    "expected": "通过",
                    "expectation_source": items[2]["text"],
                    "preconditions": ["无"],
                    "verify_method": "script:inc.py",
                    "script_name": "inc.py",
                    "script_content": inconclusive_script,
                },
                {
                    "case_id": "c_confirmed",
                    "acceptance_item_id": items[3]["item_id"],
                    "layer": "L2",
                    "title": "铁证缺陷用例",
                    "expected": "right",
                    "expectation_source": items[3]["text"],
                    "preconditions": ["无"],
                    "verify_method": "script:conf.py",
                    "script_name": "conf.py",
                    "script_content": confirmed_script,
                },
            ],
        },
        True,
    )

    class _MockJudgeAndRepairClient:
        def judge_failure(self, context: dict[str, Any]) -> dict[str, Any]:
            cid = context.get("case_id")
            if cid == "c_script_err":
                return {
                    "verdict": "script_error",
                    "reason": "脚本赋值错误导致断言误报",
                    "evidence_points": ["script_excerpt"],
                    "suggested_fix": "将 val 修正为 999 后验证",
                }
            if cid == "c_inconclusive":
                return {
                    "verdict": "inconclusive",
                    "reason": "环境数据未就绪，证据不足以断定产品缺陷",
                    "evidence_points": ["output_tail"],
                    "suggested_fix": "",
                }
            return {
                "verdict": "confirmed_defect",
                "reason": "实际返回 wrong 与预期 right 不符",
                "evidence_points": ["AssertionError"],
                "suggested_fix": "",
            }

        def repair_script(self, **kwargs: object) -> str:
            del kwargs
            return fixed_script_code

        def analyze_failure(self, run: dict[str, Any], evidence: list[str]) -> dict[str, Any]:
            del run, evidence
            return {
                "observed": "部分失败",
                "evidence_refs": [],
                "candidate_causes": [{"cause": "产品问题", "confidence": "high"}],
                "unverified": ["需进一步排查"],
                "next_steps": [],
            }

        def draft_issue(self, context: dict[str, Any]) -> dict[str, Any]:
            return {
                "title": f"问题：{context['case']['case_id']}",
                "steps": ["执行验证"],
                "expected": "right",
                "actual": "wrong",
            }

        def summarize(self, context: dict[str, Any]) -> str:
            del context
            return "本轮发现 1 项缺陷与 1 项阻塞。"

    monkeypatch.setattr("aitest.runtime.run_commands._spawn_worker", lambda *a, **k: object())
    mock_cli = _MockJudgeAndRepairClient()
    for target_mod in (
        "aitest.core.judge.build_client",
        "aitest.model_config.build_client",
        "aitest.core.issues.build_client",
        "aitest.core.analysis.build_client",
        "aitest.core.reports.build_client",
    ):
        monkeypatch.setattr(target_mod, lambda *a, **k: mock_cli)

    run_res = cmd_run_start(ws, {"project_id": pid, "plan_id": plan_res["plan_id"]}, True)
    rc_code = worker_main(
        ["--workspace", str(ws.root), "--project", pid, "--run", run_res["run_id"]]
    )
    assert rc_code == 0

    run_data = json.loads(
        (ws.project_dir(pid) / "runs" / run_res["run_id"] / "run.json").read_text(encoding="utf-8")
    )
    by_case = {entry["case_id"]: entry for entry in run_data["cases"]}

    # 1. 抖动用例：保留 2 次尝试（先 failed 后 passed），可信度为 suspected
    assert by_case["c_flaky"]["result"] == "passed"
    assert [a["result"] for a in by_case["c_flaky"]["attempts"]] == ["failed", "passed"]
    assert by_case["c_flaky"]["confidence_tier"] == "suspected"

    # 2. 运行期不改冻结脚本，script_error 保留 failed 供后续 plan.repair_case 审阅
    assert by_case["c_script_err"]["result"] == "failed"
    assert len(by_case["c_script_err"]["attempts"]) == 2
    assert by_case["c_script_err"]["judgement"]["verdict"] == "script_error"

    # 3. 裁判判证据不足不自动降级为 blocked，保持 failed 并留存裁判上下文
    assert by_case["c_inconclusive"]["result"] == "failed"
    assert by_case["c_inconclusive"]["judgement"]["verdict"] == "inconclusive"

    # 4. 裁判判真缺陷 + 复现两次 -> confirmed，同步到 Issue 与 Report
    assert by_case["c_confirmed"]["result"] == "failed"
    assert by_case["c_confirmed"]["confidence_tier"] == "confirmed"

    issue_files = list((ws.project_dir(pid) / "issues").glob("*.json"))
    assert len(issue_files) >= 1
    assert any(read_model(f, Issue).confidence_tier == "confirmed" for f in issue_files)

    report = json.loads(
        (
            ws.project_dir(pid) / "reports" / f"{run_data['latest_report_id']}.json"
        ).read_text(encoding="utf-8")
    )
    assert report["confidence_summary"]["confirmed"] >= 1
    assert report["confidence_summary"]["suspected"] >= 1


def test_checkpoint_resume_skips_stable_passed_cases_and_checks_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = _bootstrap_project_task(
        tmp_path,
        capsys,
        [
            {"text": "稳定用例 A 应只执行一次", "observable": True},
            {"text": "断点恢复用例 B 修复前提后继续完成", "observable": True},
        ],
    )
    items = setup["task"]["acceptance_items"]
    root: Path = setup["root"]
    ws = Workspace(tmp_path / "ws")
    pid = setup["project"]["project_id"]

    count_file = root / "case_a_count.txt"
    gate_file = root / "case_b_ready.flag"
    script_a = (
        f"from pathlib import Path\n"
        f"p = Path({str(count_file)!r})\n"
        "cur = int(p.read_text(encoding='utf-8')) if p.is_file() else 0\n"
        "p.write_text(str(cur + 1), encoding='utf-8')\n"
        "assert cur + 1 >= 1\n"
    )
    script_b = (
        f"from pathlib import Path\n"
        f"assert Path({str(gate_file)!r}).is_file()\n"
    )

    plan_res = cmd_plan_generate(
        ws,
        {
            "project_id": pid,
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "all_applicable"},
            "cases": [
                {
                    "case_id": "c_resume_a",
                    "acceptance_item_id": items[0]["item_id"],
                    "layer": "L2",
                    "title": "稳定用例 A",
                    "expected": "通过且计数+1",
                    "expectation_source": items[0]["text"],
                    "preconditions": ["无"],
                    "verify_method": "script:ca.py",
                    "script_name": "ca.py",
                    "script_content": script_a,
                },
                {
                    "case_id": "c_resume_b",
                    "acceptance_item_id": items[1]["item_id"],
                    "layer": "L2",
                    "title": "恢复用例 B",
                    "expected": "flag 存在后通过",
                    "expectation_source": items[1]["text"],
                    "preconditions": ["无"],
                    "verify_method": "script:cb.py",
                    "script_name": "cb.py",
                    "script_content": script_b,
                },
            ],
        },
        True,
    )
    monkeypatch.setattr("aitest.runtime.run_commands._spawn_worker", lambda *a, **k: object())

    run1 = cmd_run_start(ws, {"project_id": pid, "plan_id": plan_res["plan_id"]}, True)
    rc1 = worker_main(["--workspace", str(ws.root), "--project", pid, "--run", run1["run_id"]])
    assert rc1 == 0
    assert count_file.read_text(encoding="utf-8") == "1"
    run1_dir = ws.project_dir(pid) / "runs" / run1["run_id"]
    assert not (run1_dir / "checkpoint.json").exists()

    # 让用例 B 的外部前置满足，通过断点续跑跳过已稳定通过的 A
    gate_file.write_text("ready", encoding="utf-8")
    run2 = cmd_run_start(
        ws,
        {
            "project_id": pid,
            "plan_id": plan_res["plan_id"],
            "resume_from_run_id": run1["run_id"],
        },
        True,
    )
    assert run2["resumed_from_run_id"] == run1["run_id"]
    assert run2["resumed_cases"] == 1
    rc2 = worker_main(["--workspace", str(ws.root), "--project", pid, "--run", run2["run_id"]])
    assert rc2 == 0

    # 用例 A 未被重复执行，计数仍为 1；且总报告全部通过
    assert count_file.read_text(encoding="utf-8") == "1"
    run2_data = json.loads(
        (ws.project_dir(pid) / "runs" / run2["run_id"] / "run.json").read_text(encoding="utf-8")
    )
    by_case = {entry["case_id"]: entry for entry in run2_data["cases"]}
    assert by_case["c_resume_a"]["result"] == "passed"
    assert by_case["c_resume_a"]["resumed_from_run_id"] == run1["run_id"]
    assert by_case["c_resume_b"]["result"] == "passed"
    for ref in by_case["c_resume_a"]["evidence_refs"]:
        assert (ws.project_dir(pid) / "runs" / run2["run_id"] / ref).is_file()

    # 若计划签名或修订发生变化，断点续跑必须拒绝并返回 PLAN_STALE
    plan_file = ws.project_dir(pid) / "plans" / f"{plan_res['plan_id']}.json"
    plan_raw = json.loads(plan_file.read_text(encoding="utf-8"))
    plan_raw["revision"] = int(plan_raw["revision"]) + 1
    plan_file.write_text(json.dumps(plan_raw, ensure_ascii=False), encoding="utf-8")
    write_json(
        ws.project_dir(pid) / "project.config.json",
        {"schema_version": 1, "changed": True},
    )
    with pytest.raises(AiTestError) as exc_info:
        cmd_run_start(
            ws,
            {
                "project_id": pid,
                "plan_id": plan_res["plan_id"],
                "resume_from_run_id": run1["run_id"],
            },
            True,
        )
    assert exc_info.value.info.code == "PLAN_STALE"
