"""12 题跨类型埋雷基准考题库（CLI / Python 库 / 纯 API / 单文件 Web / 全栈）。

在临时工作区生成真实源码仓库、运行记录、硬探针、代码图与 Git 提交变更，
完全通过真实核心模块离线执行，定量统计漏测率（1 - recall）、误报率与多轮一致率。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aitest.commands import cmd_project_add, cmd_task_add
from aitest.core.code_graph import build_code_graph, load_or_build_code_graph
from aitest.core.context.contracts import _api_routes_with_meta, _frontend_api_calls
from aitest.core.git_engine import inspect_local_git
from aitest.core.probes import ContractReconciliationProbe, run_deterministic_probes
from aitest.core.script_linter import blocking_lint_errors
from aitest.core.service_logs import capture_log_offsets, inspect_service_log_window
from aitest.core.shared_config import load_shared_repo_config, merge_project_configs
from aitest.runtime_commands import cmd_plan_generate, cmd_run_start
from aitest.storage.workspace import Workspace
from aitest.worker_impl.lifecycle import main as worker_main


@dataclass(frozen=True, slots=True)
class BenchmarkOutcome:
    """单题基准测试评估结果。"""

    case_id: str
    project_type: str
    title: str
    has_planted_bug: bool
    detected_bug: bool
    signal: str
    passed_expectation: bool


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    """12 题全量基准汇总指标。"""

    total: int
    planted_bugs: int
    detected_bugs: int
    clean_cases: int
    false_positives: int
    recall_rate: float
    false_positive_rate: float
    outcomes: tuple[BenchmarkOutcome, ...]


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def _execute_worker_case(
    case_dir: Path,
    *,
    item_text: str,
    script_name: str,
    script_content: str,
) -> dict[str, Any]:
    """用真实 CLI+Worker 闭环执行单题脚本并返回 run_data 的首条用例。"""
    repo = case_dir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    ws = Workspace(case_dir / "ws")
    ws.init()
    proj = cmd_project_add(ws, {"name": case_dir.name, "root": str(repo)}, True)
    pid = str(proj["project_id"])
    task = cmd_task_add(
        ws,
        {
            "project_id": pid,
            "title": f"基准考题-{case_dir.name}",
            "intent": "business",
            "acceptance_items": [{"text": item_text, "observable": True}],
        },
        True,
    )
    tid = str(task["task_id"])
    item_id = str(task["acceptance_items"][0]["item_id"])
    plan = cmd_plan_generate(
        ws,
        {
            "project_id": pid,
            "task_id": tid,
            "scope": {"preset": "all_applicable"},
            "cases": [
                {
                    "case_id": f"c_{case_dir.name}",
                    "acceptance_item_id": item_id,
                    "layer": "L2",
                    "title": item_text,
                    "expected": "按契约或源码规范执行通过",
                    "expectation_source": item_text,
                    "preconditions": ["已创建临时项目"],
                    "verify_method": f"script:{script_name}",
                    "script_name": script_name,
                    "script_content": script_content,
                }
            ],
        },
        True,
    )
    # 先写脚本生成计划，再使用 worker_main 同步内联跑完避免轮询延迟
    import unittest.mock as _mock

    with _mock.patch(
        "aitest.runtime.run_commands._spawn_worker", return_value=object()
    ):
        run = cmd_run_start(
            ws, {"project_id": pid, "plan_id": str(plan["plan_id"])}, True
        )
    rc = worker_main(
        ["--workspace", str(ws.root), "--project", pid, "--run", str(run["run_id"])]
    )
    if rc != 0:
        raise RuntimeError(f"worker_main 返回非零码 {rc}")
    run_file = ws.project_dir(pid) / "runs" / str(run["run_id"]) / "run.json"
    return json.loads(run_file.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _case_01_cli_bug_exit_code(base_dir: Path) -> BenchmarkOutcome:
    cdir = base_dir / "01_cli_bug"
    repo = cdir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "cli_tool.py").write_text(
        "import sys\n"
        "if len(sys.argv) > 1 and sys.argv[1] == '--bad':\n"
        "    print('FATAL: invalid input')\n"
        "    sys.exit(0)  # 埋雷：报错却返回退出码 0\n"
        "print('OK')\n",
        encoding="utf-8",
    )
    script = (
        f"import subprocess, sys\n"
        f"proc = subprocess.run([sys.executable, {str(repo / 'cli_tool.py')!r}, '--bad'], "
        "capture_output=True, text=True)\n"
        "assert proc.returncode != 0, f'期望非零退出码，实际 {proc.returncode}'\n"
    )
    run_data = _execute_worker_case(
        cdir,
        item_text="非法 CLI 参数应返回非零退出码",
        script_name="verify_cli_err.py",
        script_content=script,
    )
    res = str(run_data["cases"][0]["result"])
    detected = res == "failed"
    return BenchmarkOutcome(
        case_id="cli_bug_exit_code",
        project_type="CLI 工具",
        title="非法参数报错却误返回退出码 0",
        has_planted_bug=True,
        detected_bug=detected,
        signal=f"case.result={res}",
        passed_expectation=detected,
    )


def _case_02_cli_clean(base_dir: Path) -> BenchmarkOutcome:
    cdir = base_dir / "02_cli_clean"
    repo = cdir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "cli_tool.py").write_text(
        "import json\nprint(json.dumps({'status': 'ok', 'version': '1.0'}))\n",
        encoding="utf-8",
    )
    script = (
        f"import json, subprocess, sys\n"
        f"proc = subprocess.run([sys.executable, {str(repo / 'cli_tool.py')!r}], "
        "capture_output=True, text=True)\n"
        "assert proc.returncode == 0\n"
        "data = json.loads(proc.stdout)\n"
        "assert data['status'] == 'ok'\n"
    )
    run_data = _execute_worker_case(
        cdir,
        item_text="正常 CLI 调用返回退出码 0 与合法 JSON",
        script_name="verify_cli_ok.py",
        script_content=script,
    )
    res = str(run_data["cases"][0]["result"])
    detected = res != "passed"
    return BenchmarkOutcome(
        case_id="cli_clean",
        project_type="CLI 工具",
        title="健康 CLI 正常调用（零误报对照）",
        has_planted_bug=False,
        detected_bug=detected,
        signal=f"case.result={res}",
        passed_expectation=not detected,
    )


def _case_03_pylib_bug_boundary(base_dir: Path) -> BenchmarkOutcome:
    cdir = base_dir / "03_pylib_bug"
    repo = cdir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "pricing.py").write_text(
        "def discount_price(amount: float) -> float:\n"
        "    # 埋雷：未校验负数金额直接返回负折后价\n"
        "    return round(amount * 0.9, 2)\n",
        encoding="utf-8",
    )
    script = (
        f"import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('pricing', {str(repo / 'pricing.py')!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "raised = False\n"
        "try:\n"
        "    mod.discount_price(-10)\n"
        "except ValueError:\n"
        "    raised = True\n"
        "assert raised is True, '负数金额应抛出 ValueError'\n"
    )
    run_data = _execute_worker_case(
        cdir,
        item_text="Python 库负数金额输入必须抛 ValueError",
        script_name="verify_pylib.py",
        script_content=script,
    )
    res = str(run_data["cases"][0]["result"])
    detected = res == "failed"
    return BenchmarkOutcome(
        case_id="pylib_bug_boundary",
        project_type="Python 库",
        title="负数边界逻辑漏洞未抛出参数异常",
        has_planted_bug=True,
        detected_bug=detected,
        signal=f"case.result={res}",
        passed_expectation=detected,
    )


def _case_04_pylib_shallow_script_caught(base_dir: Path) -> BenchmarkOutcome:
    del base_dir
    # 埋雷：用空断言与吞异常的“水脚本”企图混过测试
    shallow_script = (
        "def run_check():\n"
        "    try:\n"
        "        value = 1 / 0\n"
        "    except Exception:\n"
        "        pass\n"
        "    return True\n"
    )
    errors = blocking_lint_errors("water_check.py", shallow_script)
    detected = any("assert" in err or "断言" in err or "except" in err for err in errors)
    return BenchmarkOutcome(
        case_id="pylib_shallow_script_caught",
        project_type="Python 库",
        title="拦截无断言与吞异常水脚本防漏测",
        has_planted_bug=True,
        detected_bug=detected,
        signal=f"lint_errors={len(errors)}",
        passed_expectation=detected,
    )


def _case_05_api_bug_contract_mismatch(base_dir: Path) -> BenchmarkOutcome:
    cdir = base_dir / "05_api_mismatch"
    repo = cdir / "repo"
    (repo / "frontend").mkdir(parents=True, exist_ok=True)
    (repo / "backend").mkdir(parents=True, exist_ok=True)
    (repo / "frontend" / "client.js").write_text(
        'fetch("/api/users", { method: "POST", body: JSON.stringify({ username: "a" }) });\n'
        'fetch("/api/ghost-endpoint");\n',
        encoding="utf-8",
    )
    (repo / "backend" / "api.py").write_text(
        "class R:\n"
        "    def post(self, _p): return lambda f: f\n"
        "router = R()\n"
        '@router.post("/api/users")\n'
        "def create_user(email: str):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    api_routes, routes_meta = _api_routes_with_meta(repo, max_routes=2000)
    frontend_calls = _frontend_api_calls(repo, max_files=2000, max_calls=1000)
    contract = ContractReconciliationProbe().run(
        root=repo,
        api_routes=api_routes,
        frontend_api_calls=frontend_calls,
        has_dynamic_loader=bool(routes_meta.get("has_dynamic_loader")),
    )
    detected = contract["status"] == "failed" and any(
        f["kind"] == "missing_route" for f in contract["findings"]
    )
    return BenchmarkOutcome(
        case_id="api_bug_contract_mismatch",
        project_type="纯 API 服务",
        title="前后端 API 路由缺失与契约错位",
        has_planted_bug=True,
        detected_bug=detected,
        signal=f"probe.status={contract['status']}",
        passed_expectation=detected,
    )


def _case_06_api_bug_silent_traceback(base_dir: Path) -> BenchmarkOutcome:
    cdir = base_dir / "06_api_silent_500"
    cdir.mkdir(parents=True, exist_ok=True)
    log_path = cdir / "service-api.log"
    log_path.write_text("INFO server listening on 127.0.0.1:8000\n", encoding="utf-8")
    states = [{"name": "api", "log": log_path, "started": True, "ok": True}]
    offsets = capture_log_offsets(states)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("Traceback (most recent call last):\nKeyError: 'order_id'\n")
        handle.write('"POST /api/pay HTTP/1.1" 500 -\n')
    window = inspect_service_log_window(states, offsets, case_method="http:POST /api/pay")
    fatal = list(window.get("fatal_errors") or [])
    detected = len(fatal) >= 1
    return BenchmarkOutcome(
        case_id="api_bug_silent_traceback",
        project_type="纯 API 服务",
        title="窗口期后台静默 Traceback 与 500 假通过捕获",
        has_planted_bug=True,
        detected_bug=detected,
        signal=f"fatal_errors={len(fatal)}",
        passed_expectation=detected,
    )


def _case_07_api_clean(base_dir: Path) -> BenchmarkOutcome:
    cdir = base_dir / "07_api_clean"
    repo = cdir / "repo"
    (repo / "frontend").mkdir(parents=True, exist_ok=True)
    (repo / "backend").mkdir(parents=True, exist_ok=True)
    (repo / "frontend" / "client.js").write_text('fetch("/api/health");\n', encoding="utf-8")
    (repo / "backend" / "api.py").write_text(
        "class R:\n"
        "    def get(self, _p): return lambda f: f\n"
        "router = R()\n"
        '@router.get("/api/health")\n'
        "def health():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    log_path = cdir / "service-api.log"
    log_path.write_text("INFO ready\n", encoding="utf-8")
    states = [{"name": "api", "log": log_path, "started": True, "ok": True}]
    offsets = capture_log_offsets(states)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write('"GET /api/health HTTP/1.1" 200 -\n')

    api_routes, routes_meta = _api_routes_with_meta(repo, max_routes=2000)
    frontend_calls = _frontend_api_calls(repo, max_files=2000, max_calls=1000)
    contract = ContractReconciliationProbe().run(
        root=repo,
        api_routes=api_routes,
        frontend_api_calls=frontend_calls,
        has_dynamic_loader=bool(routes_meta.get("has_dynamic_loader")),
    )
    window = inspect_service_log_window(states, offsets, case_method="http:GET /api/health")
    has_false_positive = contract["status"] == "failed" or bool(window.get("fatal_errors"))
    return BenchmarkOutcome(
        case_id="api_clean",
        project_type="纯 API 服务",
        title="健康 API 契约与日志干净（零误报对照）",
        has_planted_bug=False,
        detected_bug=has_false_positive,
        signal=f"probe={contract['status']},fatal={len(window.get('fatal_errors') or [])}",
        passed_expectation=not has_false_positive,
    )


def _case_08_single_web_bug_missing_asset(base_dir: Path) -> BenchmarkOutcome:
    cdir = base_dir / "08_web_missing_asset"
    repo = cdir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "index.html").write_text(
        "<!doctype html><html><head>"
        '<link rel="stylesheet" href="./missing.css">'
        '<script src="./missing.js"></script>'
        "</head><body><h1>Demo</h1></body></html>\n",
        encoding="utf-8",
    )
    graph = build_code_graph(repo, {})
    conflicts = graph.get("conflicts") or []
    detected = any(c.get("kind") == "missing_static_asset" for c in conflicts)
    return BenchmarkOutcome(
        case_id="single_web_bug_missing_asset",
        project_type="单文件 Web",
        title="HTML 引用缺失 JS/CSS 静态资源断链",
        has_planted_bug=True,
        detected_bug=detected,
        signal=f"conflicts={len(conflicts)}",
        passed_expectation=detected,
    )


def _case_09_single_web_clean(base_dir: Path) -> BenchmarkOutcome:
    cdir = base_dir / "09_web_clean"
    repo = cdir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "app.css").write_text("body { color: #111; }\n", encoding="utf-8")
    (repo / "app.js").write_text("console.log('ready');\n", encoding="utf-8")
    (repo / "index.html").write_text(
        "<!doctype html><html><head>"
        '<link rel="stylesheet" href="./app.css">'
        '<script src="./app.js"></script>'
        "</head><body><h1>Demo</h1></body></html>\n",
        encoding="utf-8",
    )
    graph = build_code_graph(repo, {})
    conflicts = graph.get("conflicts") or []
    detected = len(conflicts) > 0
    return BenchmarkOutcome(
        case_id="single_web_clean",
        project_type="单文件 Web",
        title="健康单页静态资源全命中（零误报对照）",
        has_planted_bug=False,
        detected_bug=detected,
        signal=f"conflicts={len(conflicts)}",
        passed_expectation=not detected,
    )


def _case_10_fullstack_bug_contract_and_git_impact(base_dir: Path) -> BenchmarkOutcome:
    cdir = base_dir / "10_fullstack_git"
    repo = cdir / "repo"
    (repo / "frontend").mkdir(parents=True, exist_ok=True)
    (repo / "backend").mkdir(parents=True, exist_ok=True)
    (repo / "frontend" / "app.js").write_text(
        'fetch("/api/orders");\n', encoding="utf-8"
    )
    (repo / "backend" / "models.py").write_text(
        'class Order:\n    __tablename__ = "orders"\n', encoding="utf-8"
    )
    (repo / "backend" / "routes.py").write_text(
        "from backend import models\n"
        "class R:\n    def get(self, _p): return lambda f: f\n"
        "router = R()\n"
        '@router.get("/api/orders")\n'
        "def list_orders():\n    return models.Order.__tablename__\n",
        encoding="utf-8",
    )
    _run_git(repo, "init")
    _run_git(repo, "config", "user.name", "BenchTester")
    _run_git(repo, "config", "user.email", "bench@example.invalid")
    _run_git(repo, "add", ".")
    _run_git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "initial fullstack")

    # 埋雷：修改后端路由路径为 /api/v2/orders，破坏与前端 /api/orders 的跨层契约
    (repo / "backend" / "routes.py").write_text(
        "from backend import models\n"
        "class R:\n    def get(self, _p): return lambda f: f\n"
        "router = R()\n"
        '@router.get("/api/v2/orders")\n'
        "def list_orders():\n    return models.Order.__tablename__\n",
        encoding="utf-8",
    )
    ws = Workspace(cdir / "ws")
    ws.init()
    git_info = inspect_local_git(repo)
    graph = load_or_build_code_graph(ws, "proj_fs", repo, {}, local_git=git_info)
    conflicts = graph.get("conflicts") or []
    impact = graph.get("impact") or {}
    detected = (
        git_info.get("dirty") is True
        and len(conflicts) >= 1
        and "backend/routes.py" in (impact.get("changed_files") or [])
        and bool(impact.get("direct_node_ids"))
    )
    return BenchmarkOutcome(
        case_id="fullstack_bug_contract_and_git_impact",
        project_type="前后端分离全栈",
        title="修改后端接口破坏全栈契约并定位 Git 影响范围",
        has_planted_bug=True,
        detected_bug=detected,
        signal=f"conflicts={len(conflicts)},direct={len(impact.get('direct_node_ids') or [])}",
        passed_expectation=detected,
    )


def _case_11_fullstack_clean_with_convention(base_dir: Path) -> BenchmarkOutcome:
    cdir = base_dir / "11_fullstack_convention"
    repo = cdir / "repo"
    (repo / "frontend").mkdir(parents=True, exist_ok=True)
    (repo / "backend").mkdir(parents=True, exist_ok=True)
    (repo / "frontend" / "app.js").write_text(
        'fetch("/api/external-proxy");\n', encoding="utf-8"
    )
    (repo / "backend" / "routes.py").write_text(
        "class R:\n    def get(self, _p): return lambda f: f\n"
        "router = R()\n"
        '@router.get("/api/ping")\n'
        "def ping(): return {'ok': True}\n",
        encoding="utf-8",
    )
    (repo / ".aitest.json").write_text(
        json.dumps(
            {
                "conventions": [
                    {
                        "id": "conv_proxy",
                        "pattern": "/api/external-proxy",
                        "reason": "由前置反向代理转发，不由本服务实现",
                        "scope": "probe",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shared = load_shared_repo_config(repo)
    effective_cfg = merge_project_configs(shared, {})
    probes = run_deterministic_probes(
        repo, effective_cfg, probes=(ContractReconciliationProbe(),)
    )
    contract = next(p for p in probes if p["probe_id"] == "contract_reconciliation")
    # 命中 .aitest.json 错题本豁免后，不得再误报为 failed
    has_false_positive = contract["status"] != "passed" or bool(contract["findings"])
    suppressed_cnt = len(contract.get("suppressed_findings") or [])
    return BenchmarkOutcome(
        case_id="fullstack_clean_with_convention",
        project_type="前后端分离全栈",
        title="读取 .aitest.json 项目错题本精准消噪（零误报对照）",
        has_planted_bug=False,
        detected_bug=has_false_positive,
        signal=f"probe={contract['status']},suppressed={suppressed_cnt}",
        passed_expectation=not has_false_positive,
    )


def _case_12_fullstack_setup_failure_isolated(base_dir: Path) -> BenchmarkOutcome:
    cdir = base_dir / "12_fullstack_setup_iso"
    script = (
        "def setup():\n"
        "    raise RuntimeError('种子数据库尚未初始化')\n"
        "def verify(ctx):\n"
        "    assert ctx.get('ready') is True\n"
    )
    run_data = _execute_worker_case(
        cdir,
        item_text="前置数据未就绪时必须隔离收敛为 blocked 而不误报业务缺陷",
        script_name="verify_two_stage.py",
        script_content=script,
    )
    rc = run_data["cases"][0]
    stage = (rc.get("attempts") or [{}])[-1].get("stage")
    detected = rc.get("result") == "blocked" and stage == "setup"
    return BenchmarkOutcome(
        case_id="fullstack_setup_failure_isolated",
        project_type="前后端分离全栈",
        title="setup 阶段异常准确收敛为 blocked 避免跨模块连坐",
        has_planted_bug=True,
        detected_bug=detected,
        signal=f"result={rc.get('result')},stage={stage}",
        passed_expectation=detected,
    )


def evaluate_benchmark_suite(base_dir: Path) -> BenchmarkSummary:
    """运行全部 12 道跨类型基准考题并返回量化统计指标。"""
    runners = (
        _case_01_cli_bug_exit_code,
        _case_02_cli_clean,
        _case_03_pylib_bug_boundary,
        _case_04_pylib_shallow_script_caught,
        _case_05_api_bug_contract_mismatch,
        _case_06_api_bug_silent_traceback,
        _case_07_api_clean,
        _case_08_single_web_bug_missing_asset,
        _case_09_single_web_clean,
        _case_10_fullstack_bug_contract_and_git_impact,
        _case_11_fullstack_clean_with_convention,
        _case_12_fullstack_setup_failure_isolated,
    )
    outcomes = tuple(fn(base_dir) for fn in runners)
    planted = [o for o in outcomes if o.has_planted_bug]
    clean = [o for o in outcomes if not o.has_planted_bug]
    detected_bugs = sum(1 for o in planted if o.detected_bug)
    false_positives = sum(1 for o in clean if o.detected_bug)
    recall = detected_bugs / len(planted) if planted else 1.0
    fp_rate = false_positives / len(clean) if clean else 0.0
    return BenchmarkSummary(
        total=len(outcomes),
        planted_bugs=len(planted),
        detected_bugs=detected_bugs,
        clean_cases=len(clean),
        false_positives=false_positives,
        recall_rate=recall,
        false_positive_rate=fp_rate,
        outcomes=outcomes,
    )
