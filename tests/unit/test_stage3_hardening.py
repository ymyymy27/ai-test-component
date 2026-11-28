"""阶段 3 加固核心测试：精准脱敏、三分类裁判、稳定账本、setup 修订草稿与锁分层。"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from aitest.adapters.link import _read_text, import_link
from aitest.adapters.model import ModelClient, track_token_usage
from aitest.ai_features import judge_mock
from aitest.cli import _SELF_LOCKED
from aitest.cli import main as cli_main
from aitest.core.issues import _mark_unstable, _signature, add_issue
from aitest.core.judge import judge_case_failure
from aitest.core.script_linter import blocking_lint_errors
from aitest.core.verdict import coverage, verdict
from aitest.errors import AiTestError
from aitest.models import (
    AcceptanceItem,
    Attempt,
    Case,
    Plan,
    Project,
    Report,
    Run,
    RunCase,
    Scope,
    Task,
)
from aitest.runtime.issue_commands import cmd_issue_close, cmd_issue_update
from aitest.runtime.plan_commands import cmd_plan_generate, cmd_plan_repair_case
from aitest.runtime.plan_validation import _validate_plan_cases
from aitest.runtime.report_commands import cmd_report_export
from aitest.security import is_secret_field, redact, redact_bytes, redact_tree
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace


def test_precision_redaction_short_passwords_nul_and_meta_keys() -> None:
    """短密码不误伤标识符/文件名、仅认证上下文脱敏，NUL 不绕过，元字段不误杀。"""
    secrets = ["test", "1234", "sk-real-secret-999999999999"]
    tree = {
        "case_id": "case_test_1234",
        "evidence_refs": ["evidence/test_login_1234.log"],
        "file": "tests/test_api.py",
        "note": "running test case 1234 with sk-real-secret-999999999999",
        "password": "test",
        "log": (
            "Authorization: Bearer test\n"
            "POST http://admin:1234@127.0.0.1:8000/login\n"
            '{"password": "test", "msg": "test ok"}'
        ),
    }
    redacted = redact_tree(tree, secrets)
    assert redacted["case_id"] == "case_test_1234"
    assert redacted["evidence_refs"] == ["evidence/test_login_1234.log"]
    assert redacted["file"] == "tests/test_api.py"
    assert "running test case 1234" in redacted["note"]
    assert "sk-real-secret-999999999999" not in redacted["note"]
    assert redacted["password"] == "***"
    assert "Bearer ***" in redacted["log"]
    assert "1234" not in redacted["log"]
    assert "127.0.0.1:8000/login" in redacted["log"]
    assert '"password": "***"' in redacted["log"]
    assert '"msg": "test ok"' in redacted["log"]

    # \x00 不可绕过文本字节脱敏
    payload = b"ok\x00Authorization: Bearer test\x00token=sk-real-secret-999999999999"
    masked_bytes = redact_bytes(payload, secrets)
    assert b"Bearer ***" in masked_bytes
    assert b"sk-real-secret" not in masked_bytes

    # 元配置后缀不受 is_secret_field 误杀
    assert is_secret_field("auth_header") is False
    assert is_secret_field("login_url") is False
    assert is_secret_field("token_env") is False
    assert is_secret_field("password") is True
    assert redact("普通文案 test", ["test"]) == "普通文案 test"


def test_objective_three_way_judge_no_blind_confirmed_or_word_misfire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """废除兜底即 confirmed_defect、废除正文 expected/passed 误杀、允许 http 构造归 error。"""
    ws = Workspace(tmp_path / "ws")
    ws.init()
    proj = Project(project_id="p_j", name="j", root=str(tmp_path))
    ws.save_project(proj)

    monkeypatch.setattr("aitest.core.judge.build_client", lambda *_a, **_k: None)

    http_case = Case(
        case_id="c_http_bad",
        layer="L2",
        expected="创建成功",
        expectation_source="创建成功",
        verify_method='http:[{"method":"POST","url":"http://127.0.0.1:8000/items"}]',
    )
    # 1. 通用失败含英文词 expected / passed 时，不再升级为 confirmed_defect
    generic_j = judge_case_failure(
        ws,
        "p_j",
        case=http_case,
        outputs=[b"not passed yet, expected value missing"],
        message="request failed with unexpected output",
        service_log_issues=[],
        probe_results=None,
        secrets=[],
    )
    assert generic_j["verdict"] == "suspected_defect"

    # 2. http 请求明显缺少变量 / 字段构造错可客观判 script_error
    construct_j = judge_case_failure(
        ws,
        "p_j",
        case=http_case,
        outputs=[b'{"detail": [{"msg": "Field required"}]}'],
        message="request 1 引用了未定义变量：vars.token",
        service_log_issues=[],
        probe_results=None,
        secrets=[],
    )
    assert construct_j["verdict"] == "script_error"

    # 3. 环境连接拒绝阻塞判 inconclusive
    env_j = judge_case_failure(
        ws,
        "p_j",
        case=http_case,
        outputs=[b"ECONNREFUSED"],
        message="ConnectionRefusedError: 127.0.0.1:8000",
        service_log_issues=[],
        probe_results=None,
        secrets=[],
    )
    assert env_j["verdict"] == "inconclusive"


def test_issues_signature_isolation_mark_unstable_and_timezone_close(tmp_path: Path) -> None:
    """问题签名绑定 case_id 不跨用例撞签、同 run 不误标 unstable、UTC/naive 混用关闭不崩溃。"""
    ws = Workspace(tmp_path / "ws")
    ws.init()
    proj = Project(project_id="p_iss", name="iss", root=str(tmp_path))
    ws.save_project(proj)

    sig_a = _signature("case_a", "assertionerror", "same_err", target_key="ai_1")
    sig_b = _signature("case_b", "assertionerror", "same_err", target_key="ai_1")
    assert sig_a != sig_b

    res = add_issue(
        ws,
        "p_iss",
        "run_1",
        "case_fail",
        "标题",
        error_type="assertionerror",
        stack_key="e1",
        target_key="ai_shared",
    )
    # 同一验收项下的其他用例通过，绝不污染 case_fail 的问题状态
    _mark_unstable(ws, "p_iss", "case_pass", target_key="ai_shared", current_run_id="run_1")
    # 同 run_id 下即便同名也不应标 passed + unstable
    _mark_unstable(ws, "p_iss", "case_fail", target_key="ai_shared", current_run_id="run_1")
    issue_path = ws.project_dir("p_iss") / "issues" / f"{res['issue_id']}.json"
    from aitest.models import Issue

    loaded = read_model(issue_path, Issue)
    assert loaded.last_result == "failed"
    assert loaded.unstable is False

    # 将状态切为 in_progress -> retest，测试 Z 结尾 UTC 与本地时间比较不报 TypeError
    cmd_issue_update(
        ws, {"project_id": "p_iss", "issue_id": loaded.issue_id, "status": "in_progress"}, False
    )
    cmd_issue_update(
        ws, {"project_id": "p_iss", "issue_id": loaded.issue_id, "status": "retest"}, False
    )
    loaded = read_model(issue_path, Issue)
    loaded.occurrences[-1].at = "2026-11-27T01:00:00Z"
    write_model(issue_path, loaded)

    run_dir = ws.project_dir("p_iss") / "runs" / "run_2"
    ev_dir = run_dir / "evidence"
    ev_dir.mkdir(parents=True)
    (ev_dir / "pass.log").write_text("ok", encoding="utf-8")
    plan = Plan(
        plan_id="pl_1",
        task_id="t_1",
        scope=Scope(preset="all_applicable", acceptance_item_ids=["ai_shared"]),
        applicable_count=1,
        cases=[
            Case(
                case_id="case_fail",
                acceptance_item_id="ai_shared",
                layer="L2",
                expected="ok",
                expectation_source="ok",
                expectation_verified=True,
                verify_method="command: echo ok",
            )
        ],
    )
    run2 = Run(
        run_id="run_2",
        plan_id="pl_1",
        project_id="p_iss",
        scope=plan.scope,
        status="completed",
        started_at="2099-01-01T12:00:00",  # naive 时间混合 Z 字符串比较
        cases=[RunCase(case_id="case_fail", result="passed", evidence_refs=["evidence/pass.log"])],
    )
    write_model(run_dir / "plan.json", plan)
    write_model(run_dir / "run.json", run2)

    closed = cmd_issue_close(
        ws,
        {"project_id": "p_iss", "issue_id": loaded.issue_id, "run_id": "run_2", "reason": "已修复"},
        False,
    )
    assert closed["status"] == "closed"


def test_verdict_l1_exit_code_and_coverage_breakdown() -> None:
    """L1 检查只要 exit_code != 0 即使 ok=True 也判失败；coverage 细分有效覆盖与阻塞计数。"""
    plan = Plan(
        plan_id="pl_v",
        task_id="t_v",
        scope=Scope(preset="all_applicable", acceptance_item_ids=["a1", "a2"]),
        applicable_count=2,
        cases=[
            Case(
                case_id="c1",
                acceptance_item_id="a1",
                layer="L2",
                expected="e1",
                expectation_source="e1",
                expectation_verified=True,
                verify_method="command: echo 1",
            ),
            Case(
                case_id="c2",
                acceptance_item_id="a2",
                layer="L2",
                expected="e2",
                expectation_source="e2",
                expectation_verified=True,
                verify_method="command: echo 2",
            ),
        ],
    )
    run = Run(
        run_id="r_v",
        plan_id="pl_v",
        project_id="p_v",
        scope=plan.scope,
        status="completed",
        l1={"build": {"ok": True, "exit_code": 2}},
        cases=[
            RunCase(case_id="c1", result="passed"),
            RunCase(case_id="c2", result="blocked"),
        ],
    )
    assert verdict(plan, run) == "failed"
    cov = coverage(plan, run)
    assert cov["covered"] == 2
    assert cov["effective_covered"] == 1
    assert cov["blocked"] == 1
    assert cov["passed"] == 1


def test_plan_repair_case_batch_setup_draft_and_ast_guard(tmp_path: Path) -> None:
    """批量 setup 修订草稿：自动识别 setup 失败用例、放行改 setup、严防改动 verify() AST。"""
    ws = Workspace(tmp_path / "ws")
    repo = tmp_path / "repo"
    repo.mkdir()
    ws.save_project(Project(project_id="p_b", name="b", root=str(repo)))
    task = Task(
        task_id="t_b",
        project_id="p_b",
        title="批量 setup 修复",
        acceptance_items=[
            AcceptanceItem(item_id="ai_1", text="查询条目返回 200"),
            AcceptanceItem(item_id="ai_2", text="创建条目返回 201"),
        ],
    )
    write_model(ws.project_dir("p_b") / "tasks" / "t_b.json", task)

    two_stage_orig = (
        "def setup():\n    return {'name': ''}\n\n"
        "def verify(ctx):\n    assert ctx['name'] == 'ok'\n"
    )
    http_orig = (
        'http:[{"stage":"setup","method":"POST","url":"http://127.0.0.1:8000/login","json":{"u":""}},'
        '{"stage":"assert","method":"POST","url":"http://127.0.0.1:8000/items","expect":[{"path":"status","operator":"equals","value":201}]}]'
    )
    out = cmd_plan_generate(
        ws,
        {
            "project_id": "p_b",
            "task_id": "t_b",
            "scope": {"preset": "all_applicable"},
            "cases": [
                {
                    "case_id": "c_py",
                    "acceptance_item_id": "ai_1",
                    "layer": "L2",
                    "expected": "查询条目返回 200",
                    "expectation_source": "查询条目返回 200",
                    "preconditions": ["已登录"],
                    "verify_method": "script:c_py.py",
                    "script_name": "c_py.py",
                    "script_content": two_stage_orig,
                },
                {
                    "case_id": "c_http",
                    "acceptance_item_id": "ai_2",
                    "layer": "L2",
                    "expected": "创建条目返回 201",
                    "expectation_source": "创建条目返回 201",
                    "preconditions": ["已登录"],
                    "verify_method": http_orig,
                },
            ],
        },
        True,
    )
    plan_id = out["plan_id"]
    plan = read_model(ws.project_dir("p_b") / "plans" / f"{plan_id}.json", Plan)
    run_dir = ws.project_dir("p_b") / "runs" / "run_setup_fail"
    write_model(run_dir / "plan.json", plan)
    write_model(
        run_dir / "run.json",
        Run(
            run_id="run_setup_fail",
            plan_id=plan_id,
            project_id="p_b",
            scope=plan.scope,
            status="completed",
            cases=[
                RunCase(
                    case_id="c_py",
                    result="blocked",
                    attempts=[
                        Attempt(
                            attempt=1,
                            result="blocked",
                            stage="setup",
                            message="前置准备阶段（setup）失败",
                        )
                    ],
                ),
                RunCase(
                    case_id="c_http",
                    result="blocked",
                    attempts=[
                        Attempt(
                            attempt=1,
                            result="blocked",
                            stage="setup",
                            message="request 1 返回 422",
                        )
                    ],
                ),
            ],
        ),
    )

    # 1. 企图修改 verify() AST 或修改 http: assert 步骤必须被硬拦截
    bad_preview = cmd_plan_repair_case(
        ws,
        {
            "project_id": "p_b",
            "plan_id": plan_id,
            "run_id": "run_setup_fail",
            "mode": "preview",
            "repaired_scripts": {
                "c_py": (
                    "def setup():\n    return {'name': 'ok'}\n\n"
                    "def verify(ctx):\n    assert ctx['name'] != 'bad'\n"
                )
            },
            "repaired_methods": {
                "c_http": 'http:[{"stage":"setup","method":"POST","url":"http://127.0.0.1:8000/login","json":{"u":"ok"}},{"stage":"assert","method":"POST","url":"http://127.0.0.1:8000/items","expect":[{"path":"status","operator":"equals","value":500}]}]'
            },
        },
        False,
    )
    assert bad_preview["all_ok"] is False
    assert "verify_ast_changed" in bad_preview["guard_blocked"]
    assert "assert_step_changed" in bad_preview["guard_blocked"]

    # 2. 仅修复 setup 后 preview 通过并 apply 升版，返回定向重跑 acceptance_item_ids
    good_py = (
        "def setup():\n    return {'name': 'ok'}\n\n"
        "def verify(ctx):\n    assert ctx['name'] == 'ok'\n"
    )
    good_http = (
        'http:[{"stage":"setup","method":"POST","url":"http://127.0.0.1:8000/login","json":{"u":"ok"}},'
        '{"stage":"assert","method":"POST","url":"http://127.0.0.1:8000/items","expect":[{"path":"status","operator":"equals","value":201}]}]'
    )
    good_apply = cmd_plan_repair_case(
        ws,
        {
            "project_id": "p_b",
            "plan_id": plan_id,
            "run_id": "run_setup_fail",
            "mode": "apply",
            "setup_only": True,
            "repaired_scripts": {"c_py": good_py},
            "repaired_methods": {"c_http": good_http},
        },
        True,
    )
    assert good_apply["ok"] is True
    assert good_apply["plan_revision"] == plan.revision + 1
    assert set(good_apply["rerun_acceptance_item_ids"]) == {"ai_1", "ai_2"}


def test_lock_layering_and_concurrent_idempotent_self_locked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """长耗时命令进入 _SELF_LOCKED，同 request_id 并发双击只执行一次不穿透。"""
    for cmd in (
        "plan.generate",
        "project.map.refresh",
        "report.generate",
        "github.read",
        "link.import",
        "plan.repair_case",
    ):
        assert cmd in _SELF_LOCKED

    ws_dir = tmp_path / "ws"
    Workspace(ws_dir).init()
    calls: list[int] = []
    lock = threading.Lock()
    from aitest.commands import MUTATING, REGISTRY

    def fake_cmd(ws: Workspace, payload: dict[str, Any], confirm: bool) -> dict[str, Any]:
        del ws, payload, confirm
        time.sleep(0.05)
        with lock:
            calls.append(1)
        return {"count": len(calls)}

    REGISTRY["test.self_locked_probe"] = fake_cmd
    MUTATING.add("test.self_locked_probe")
    req_file = tmp_path / "req.json"
    req_file.write_text(
        json.dumps({"command": "test.self_locked_probe", "request_id": "dup_req_1", "payload": {}}),
        encoding="utf-8",
    )
    try:
        # 并发两次使用相同 request_id 调用同一个 self-locked 命令（临时在集合中添加）
        _orig = set(_SELF_LOCKED)
        import aitest.cli as cli_mod

        cli_mod._SELF_LOCKED = frozenset({*_orig, "test.self_locked_probe"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            args = [
                "test.self_locked_probe",
                "--workspace",
                str(ws_dir),
                "--input",
                str(req_file),
            ]
            f1 = pool.submit(cli_main, args)
            f2 = pool.submit(cli_main, args)
            assert f1.result() == 0
            assert f2.result() == 0
        assert len(calls) == 1
        _ = capsys.readouterr()
    finally:
        import aitest.cli as cli_mod

        cli_mod._SELF_LOCKED = frozenset(
            x for x in cli_mod._SELF_LOCKED if x != "test.self_locked_probe"
        )
        REGISTRY.pop("test.self_locked_probe", None)
        MUTATING.discard("test.self_locked_probe")


def test_model_adapter_task_kind_truncation_ledger_context_and_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """补全 task_kind、finish_reason=length 报截断、并发 ledger 统计与预算异常不被吞。"""
    captured_kinds: list[str] = []
    client = ModelClient(
        "http://127.0.0.1:9999/v1",
        "base-model",
        "sk-dummy-1234567890",
        model_tier="auto",
        fast_model="fast-m",
        strong_model="strong-m",
    )

    def spy_complete(
        _system: str, _user: str, *, task_kind: str = "", use_cache: bool = True
    ) -> dict[str, Any]:
        del use_cache
        captured_kinds.append(task_kind)
        if task_kind == "draft_issue":
            return {"title": "issue", "steps": ["s"], "expected": "e", "actual": "a"}
        if task_kind == "suggest_dependency":
            return {"package": "pydantic"}
        return {"items": []}

    monkeypatch.setattr(client, "complete_json", spy_complete)
    client.draft_issue({"case": {}})
    client.suggest_dependency("pydantic", {})
    client.extract_acceptance_items("reqs")
    assert captured_kinds == ["draft_issue", "suggest_dependency", "extract_items"]

    # finish_reason == length 报截断清晰错误消息（保持 MODEL_OUTPUT_INVALID 兼容重试逻辑）
    from aitest.adapters.model import _extract_content

    with pytest.raises(AiTestError) as exc_trunc:
        _extract_content(
            json.dumps(
                {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
            ).encode("utf-8")
        )
    assert exc_trunc.value.info.code == "MODEL_OUTPUT_INVALID"
    assert "截断" in exc_trunc.value.info.message

    # copy_context() 正确跨线程累计 TokenLedger
    import contextvars

    with track_token_usage(max_budget=5000) as ledger:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f = pool.submit(contextvars.copy_context().run, ledger.record, 10, 20, 30)
            f.result()
        assert ledger.summary()["total_tokens"] == 30

    # judge_mock 脱敏与不吞 TOKEN_BUDGET_EXCEEDED
    proj_dir = tmp_path / "proj_m"
    proj_dir.mkdir()
    (proj_dir / "app.py").write_text(
        'import redis\nAPI_KEY = "sk-leak-mock-9876543210"\n', encoding="utf-8"
    )

    def budget_complete(
        system: str, user: str, *, task_kind: str = "", use_cache: bool = True
    ) -> dict[str, Any]:
        del system, use_cache
        assert task_kind == "judge_mock"
        assert "sk-leak-mock-9876543210" not in user
        raise AiTestError("TOKEN_BUDGET_EXCEEDED", "超预算", "调大预算")

    monkeypatch.setattr(client, "complete_json", budget_complete)
    with pytest.raises(AiTestError) as exc_budget:
        judge_mock(proj_dir, ["redis"], [], client=client)
    assert exc_budget.value.info.code == "TOKEN_BUDGET_EXCEEDED"


def test_linter_playwright_expect_parametrized_pytest_report_md_and_tail_log(
    tmp_path: Path,
) -> None:
    """Linter 识别 Python Playwright expect()、支持 [..] 参数化节点、报告内联排错、大日志尾读。"""
    py_pw = (
        "from playwright.sync_api import expect\n"
        "expect(page.get_by_text('OK')).to_be_visible()\n"
    )
    assert not blocking_lint_errors("check_ui.py", py_pw)

    cases = [
        Case(
            case_id="c_param",
            acceptance_item_id="a1",
            layer="L2",
            expected="参数化通过",
            expectation_source="参数化通过",
            verify_method="command: python -m pytest tests/test_auth.py::test_login[admin-200]",
        )
    ]
    ctx = {
        "root": str(tmp_path),
        "test_functions": {"tests/test_auth.py": ["test_login"]},
        "framework": ["pytest"],
        "services": {},
    }
    from aitest.runtime.plan_validation import _annotate_case_coverage

    _validate_plan_cases(cases, "business", ctx)
    notes = _annotate_case_coverage(cases, ctx)
    assert not notes
    assert cases[0].coverage_kind == "direct"

    # Markdown 报告导出内联标题、可信度、报错、问题坐标
    ws = Workspace(tmp_path / "ws")
    ws.init()
    ws.save_project(Project(project_id="p_rep", name="rep", root=str(tmp_path)))
    report = Report(
        report_id="rep_md",
        run_id="run_md",
        generated_at="2026-11-27T12:00:00Z",
        outcome="failed",
        conclusion="存在 1 项失败",
        counts={
            "selected": 1,
            "applicable": 1,
            "covered": 1,
            "passed": 0,
            "failed": 1,
            "incomplete": 0,
        },
        coverage={"selected": 1, "applicable": 1},
        issues=[
            {
                "issue_id": "issue_99",
                "severity": "P1",
                "status": "pending",
                "title": "标题：创建订单返回 500",
                "file": "app/orders.py",
                "line": 42,
                "occurrences": 1,
                "run_occurrences": 1,
            }
        ],
        case_details=[
            {
                "case_id": "case_ord_1",
                "title": "创建订单返回 500",
                "acceptance_item_id": "ai_ord",
                "layer": "L2",
                "result": "failed",
                "confidence_tier": "confirmed",
                "confidence_reason": "后台返回 500 且复现失败",
                "service_log_issues": ["HTTP 500 at /orders"],
                "evidence_refs": ["evidence/case_ord_1.log"],
                "attempts": [{"attempt": 1, "result": "failed", "message": "HTTP 500"}],
            }
        ],
    )
    rep_path = ws.project_dir("p_rep") / "reports" / "rep_md.json"
    write_model(rep_path, report)
    out_md = tmp_path / "report.md"
    cmd_report_export(
        ws, {"project_id": "p_rep", "report_id": "rep_md", "path": str(out_md)}, False
    )
    md = out_md.read_text(encoding="utf-8")
    assert "创建订单返回 500" in md
    assert "confirmed" in md
    assert "issue_99 @ app/orders.py:42" in md
    assert "后台服务日志异常：HTTP 500 at /orders" in md

    # 大日志（>32 MiB）从尾部读取，捕获末尾请求记录而非头部旧行
    big_log = tmp_path / "huge.jsonl"
    with big_log.open("wb") as handle:
        handle.write(b'{"request_id": "old_req", "message": "stale"}\n')
        handle.write(b"x" * (33 * 1024 * 1024))
        tail_line = (
            b'\n{"request_id": "req_tail_win", "module": "api", '
            b'"status": "ok", "message": "tail"}\n'
        )
        handle.write(tail_line)
    tail_txt = _read_text(big_log)
    assert tail_txt is not None
    assert "req_tail_win" in tail_txt
    assert "old_req" not in tail_txt

    # import_link 拦截 SQL 写关键字
    link_res = import_link(
        "r1",
        "req_tail_win",
        log_paths=[big_log],
        log_format="jsonl",
        log_fields=None,
        db_query="DELETE FROM users WHERE id = '{request_id}'",
    )
    assert any(n.get("request_id") or n.get("message") == "tail" for n in link_res["nodes"])
    assert any("只读安全策略拦截" in g.get("reason", "") for g in link_res["gaps"])
