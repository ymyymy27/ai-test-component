"""阶段 1A 底层并发、流式证据、L1 口径、快照固化与安全基建回归测试。"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from aitest.adapters.model import ModelClient, track_token_usage
from aitest.command_handlers.map_task_commands import _task_signal_context
from aitest.commands import (
    cmd_project_add,
    cmd_project_map,
    cmd_project_map_refresh,
    cmd_task_add,
)
from aitest.core.evidence import save_evidence_stream, sha256_file
from aitest.core.reports import build_report
from aitest.core.verdict import coverage, verdict
from aitest.errors import AiTestError
from aitest.integration_commands import cmd_github_read
from aitest.models import (
    AcceptanceItem,
    Case,
    Delivery,
    Plan,
    Run,
    RunCase,
    Scope,
    Task,
)
from aitest.runners.command_runner import run_command
from aitest.runners.http_runner import run_http_sequence
from aitest.runners.package_install import InstallOutcome
from aitest.runtime.backup_cleanup import cmd_backup_create, cmd_backup_restore
from aitest.runtime.plan_commands import cmd_plan_generate
from aitest.runtime.run_commands import (
    cmd_run_cancel,
    cmd_run_repair,
    cmd_run_start,
    cmd_run_status,
)
from aitest.runtime.snapshot import cmd_project_snapshot
from aitest.security import is_ignored_path, load_ignore_patterns
from aitest.storage.files import read_json, write_model
from aitest.storage.workspace import Workspace, iso_now


def _setup_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "stage1a-proj"
) -> tuple[Workspace, str, Path]:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)
    monkeypatch.delenv("AITEST_GITHUB_TOKEN", raising=False)
    ws = Workspace(tmp_path / "ws")
    ws.init()
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    record = cmd_project_add(ws, {"root": str(root), "name": name}, True)
    return ws, str(record["project_id"]), root


def test_project_locks_allow_different_projects_and_respect_global_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不同项目的 project_lock 可并发持有，同项目与全局锁互斥。"""
    ws, proj_a, _ = _setup_project(tmp_path, monkeypatch, "proj-a")
    root_b = tmp_path / "proj-b"
    root_b.mkdir()
    proj_b = str(cmd_project_add(ws, {"root": str(root_b), "name": "proj-b"}, True)["project_id"])

    # 1. 全局锁占有期间，project_lock 必须报 WORKSPACE_LOCKED
    with ws.lock(timeout=2.0):
        with pytest.raises(AiTestError) as excinfo, ws.project_lock(proj_a, timeout=0.1):
            pass
        assert excinfo.value.info.code == "WORKSPACE_LOCKED"

    # 2. 不同项目的 project_lock 可同时并发持有
    entered_b = threading.Event()
    release_a = threading.Event()
    errors: list[Exception] = []

    def hold_a() -> None:
        try:
            with ws.project_lock(proj_a, timeout=2.0):
                release_a.wait(timeout=3.0)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def hold_b() -> None:
        try:
            with ws.project_lock(proj_b, timeout=2.0):
                entered_b.set()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t_a = threading.Thread(target=hold_a, daemon=True)
    t_a.start()
    time.sleep(0.05)
    t_b = threading.Thread(target=hold_b, daemon=True)
    t_b.start()
    assert entered_b.wait(timeout=2.0)

    # 3. 同一项目锁占有期间，再次取同项目 lock 必须超时报错
    with pytest.raises(AiTestError) as same_exc, ws.project_lock(proj_a, timeout=0.1):
        pass
    assert same_exc.value.info.code == "WORKSPACE_LOCKED"

    release_a.set()
    t_a.join(timeout=2.0)
    t_b.join(timeout=2.0)
    assert errors == []


def test_backup_create_releases_lock_before_zip_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """backup.create 在锁外完成压缩与校验和生成，且恢复目录校验 100% 通过。"""
    ws, project_id, _ = _setup_project(tmp_path, monkeypatch, "backup-proj")
    cmd_task_add(
        ws,
        {
            "project_id": project_id,
            "title": "备份任务",
            "acceptance_items": [{"item_id": "i1", "text": "核心功能可用"}],
        },
        True,
    )
    backup_zip = tmp_path / "out" / "ws-backup.zip"
    created = cmd_backup_create(ws, {"path": str(backup_zip)}, True)
    assert backup_zip.is_file()
    assert created["sha256"] == sha256_file(backup_zip)

    restored_dir = tmp_path / "restored-ws"
    restored = cmd_backup_restore(
        ws, {"path": str(backup_zip), "target": str(restored_dir)}, True
    )
    assert restored["verified"] is True
    assert (restored_dir / "projects" / project_id / "project.json").is_file()


def test_run_repair_releases_workspace_and_project_lock_during_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run.repair 执行 pip 安装期间不持有全局工作空间锁与项目业务锁。"""
    ws, project_id, root = _setup_project(tmp_path, monkeypatch, "repair-proj")
    run_dir = ws.project_dir(project_id) / "runs" / "run_r1"
    run_dir.mkdir(parents=True)
    run = Run(
        run_id="run_r1",
        plan_id="plan_r1",
        project_id=project_id,
        scope=Scope(),
        status="completed",
        started_at=iso_now(),
        dependency_suggestions=[
            {"module": "httpx", "package": "httpx>=0.27", "installed": "false"}
        ],
    )
    write_model(run_dir / "run.json", run)

    observed_locks: dict[str, bool] = {}

    def fake_install(*_args: Any, **_kwargs: Any) -> InstallOutcome:
        # 安装期间工作空间全局锁与该项目锁均可无阻塞立即获取
        with ws.lock(timeout=0.5):
            observed_locks["global_free"] = True
        with ws.project_lock(project_id, timeout=0.5):
            observed_locks["project_free"] = True
        return InstallOutcome(
            ok=True,
            exit_code=0,
            output=b"Successfully installed httpx-0.27.0\n",
            installer="pip",
            attempts=["pip"],
        )

    monkeypatch.setattr("aitest.runtime_commands.install_packages", fake_install)
    monkeypatch.setattr(
        "aitest.runtime_commands._installed_versions",
        lambda _py, pkgs, _env: {spec: "0.27.0" for spec in pkgs},
    )
    monkeypatch.setattr(
        "aitest.worker._environment_fingerprint",
        lambda py, cwd: {"python": str(py), "cwd": str(cwd)},
    )

    repaired = cmd_run_repair(
        ws,
        {
            "project_id": project_id,
            "run_id": "run_r1",
            "packages": ["httpx>=0.27"],
            "confirmed_by": "tester",
        },
        True,
    )
    assert observed_locks == {"global_free": True, "project_free": True}
    assert repaired["repair"]["ok"] is True


def test_save_evidence_stream_truncates_and_redacts_across_chunk_boundary(
    tmp_path: Path,
) -> None:
    """流式保存应在跨越 64KiB 块边界处完整脱敏、严格控制 cap 并写截断标记。"""
    run_dir = tmp_path / "run_stream"
    run_dir.mkdir()
    secret = "sk-supersecretvalueacrossboundary987654321"
    half = len(secret) // 2
    # 精准令密钥跨越 65536 字节块切点
    prefix = "A" * (65536 - half)
    suffix = "B" * 40000
    src_file = tmp_path / "huge.log"
    raw_text = prefix + secret + suffix
    src_file.write_text(raw_text, encoding="utf-8")

    cap = 70 * 1024
    ref = save_evidence_stream(
        run_dir,
        "stream.log",
        src_file,
        result="failed",
        file_cap_bytes=cap,
        secrets=[secret],
    )
    assert ref == "evidence/stream.log"
    saved = run_dir / "evidence" / "stream.log"
    content = saved.read_text(encoding="utf-8")
    assert secret not in content
    assert "***" in content
    assert saved.stat().st_size <= cap
    truncated_marker = run_dir / "evidence" / "stream.log.truncated"
    assert truncated_marker.is_file()
    marker_text = truncated_marker.read_text(encoding="utf-8")
    assert f"原始大小 {src_file.stat().st_size} 字节" in marker_text
    assert sha256_file(saved) == hashlib.sha256(saved.read_bytes()).hexdigest()

    # 命令分块流式截断上限测试
    cmd = f'"{sys.executable}" -c "print(\'X\' * 20000)"'
    res = run_command(cmd, cwd=tmp_path, max_output_bytes=4096)
    assert res.status == "passed"
    assert len(res.stdout) == 4096
    assert b"capture limit and was truncated" in res.stderr


def test_b14_l1_isolated_from_coverage_and_smoke_plan_uses_pure_l1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-14：所有 L1 用例隔离出 S/M 并计入报告 l1 区；smoke 计划纯 L1 可空范围启动。"""
    ws, project_id, root = _setup_project(tmp_path, monkeypatch, "b14-proj")
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-q"\n', "utf-8")

    # 1. 手工构建包含“误绑验收项的 L1”和正常 L2 的计划，校验 verdict/coverage/reports 口径
    item_a = AcceptanceItem(item_id="ai_a", text="接口可用")
    item_b = AcceptanceItem(item_id="ai_b", text="列表渲染")
    task = Task(
        task_id="task_mix",
        project_id=project_id,
        title="混合检查",
        acceptance_items=[item_a, item_b],
    )
    write_model(ws.project_dir(project_id) / "tasks" / "task_mix.json", task)
    plan = Plan(
        plan_id="plan_mix",
        task_id="task_mix",
        scope=Scope(acceptance_item_ids=["ai_a", "ai_b"]),
        applicable_count=2,
        cases=[
            Case(
                case_id="c_l1_bound",
                acceptance_item_id="ai_a",
                layer="L1",
                expected="构建通过",
                expectation_source="构建通过",
                expectation_verified=True,
                verify_method="command:python --version",
            ),
            Case(
                case_id="c_l2",
                acceptance_item_id="ai_b",
                layer="L2",
                expected="列表渲染",
                expectation_source="列表渲染",
                expectation_verified=True,
                verify_method="command:python --version",
            ),
        ],
    )
    run = Run(
        run_id="run_mix",
        plan_id="plan_mix",
        project_id=project_id,
        scope=plan.scope,
        status="completed",
        started_at=iso_now(),
        cases=[
            RunCase(case_id="c_l1_bound", result="passed"),
            RunCase(case_id="c_l2", result="passed"),
        ],
    )
    run_dir = ws.project_dir(project_id) / "runs" / "run_mix"
    run_dir.mkdir(parents=True)
    write_model(run_dir / "plan.json", plan)
    write_model(run_dir / "task.json", task)
    write_model(run_dir / "run.json", run)

    counts = coverage(plan, run)
    assert counts["selected"] == 2
    assert counts["covered"] == 1
    assert counts["passed"] == 1
    assert verdict(plan, run) == "incomplete"

    project = ws.load_project(project_id)
    report = build_report(ws, project, run_dir, use_model=False)
    assert report.l1 is not None
    assert [c["case_id"] for c in report.l1["cases"]] == ["c_l1_bound"]

    # 2. 新生成 smoke 计划：纯 L1 无项绑定、selected 与 applicable 均为 0，且允许直接启动
    smoke = cmd_task_add(
        ws,
        {
            "project_id": project_id,
            "title": "冒烟检查",
            "goal": "验证项目能否启动",
            "intent": "smoke",
            "acceptance_items": [{"item_id": "ai_s1", "text": "可启动"}],
        },
        True,
    )
    smoke_plan = cmd_plan_generate(
        ws,
        {
            "project_id": project_id,
            "task_id": smoke["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": ["ai_s1"]},
        },
        True,
    )
    assert smoke_plan["scope"]["coverage"] == {"selected": 0, "applicable": 0}
    monkeypatch.setattr("aitest.runtime.run_commands._spawn_worker", lambda *_a, **_kw: object())
    started = cmd_run_start(
        ws,
        {"project_id": project_id, "plan_id": smoke_plan["plan_id"], "confirmed_by": "tester"},
        True,
    )
    assert started["status"] == "running"


def test_f21_excluded_items_snapshot_frozen_across_task_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-21：plan 与 run 固化 excluded_items，修改任务后历史未纳入清单不漂移。"""
    ws, project_id, _ = _setup_project(tmp_path, monkeypatch, "f21-proj")
    added = cmd_task_add(
        ws,
        {
            "project_id": project_id,
            "title": "范围快照任务",
            "acceptance_items": [
                {"item_id": "i1", "text": "创建用户"},
                {"item_id": "i2", "text": "删除用户"},
            ],
        },
        True,
    )
    task_id = added["task_id"]
    plan_res = cmd_plan_generate(
        ws,
        {
            "project_id": project_id,
            "task_id": task_id,
            "scope": {"preset": "selected", "acceptance_item_ids": ["i1"]},
            "cases": [
                {
                    "case_id": "c1",
                    "acceptance_item_id": "i1",
                    "layer": "L2",
                    "expected": "创建用户",
                    "expectation_source": "创建用户",
                    "preconditions": ["已建项目"],
                    "verify_method": "command:python --version",
                }
            ],
        },
        True,
    )
    monkeypatch.setattr("aitest.runtime.run_commands._spawn_worker", lambda *_a, **_kw: object())
    started = cmd_run_start(
        ws,
        {"project_id": project_id, "plan_id": plan_res["plan_id"], "confirmed_by": "tester"},
        True,
    )

    # 篡改原任务：删除 i2 并追加 i3，旧运行快照不得变化
    task_path = ws.project_dir(project_id) / "tasks" / f"{task_id}.json"
    from aitest.storage.files import read_model

    task_obj = read_model(task_path, Task)
    task_obj.acceptance_items = [
        AcceptanceItem(item_id="i1", text="创建用户"),
        AcceptanceItem(item_id="i3", text="后来新增项"),
    ]
    write_model(task_path, task_obj)

    status = cmd_run_status(ws, {"project_id": project_id, "run_id": started["run_id"]}, False)
    assert status["excluded_items"] == [
        {"item_id": "i2", "no": "02", "text": "删除用户", "module_id": None}
    ]


def test_fd23_run_cancel_mark_interrupted_updates_dead_worker_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """补-FD23：run.cancel 传入 mark_interrupted 时将失活 worker 的运行状态置为 interrupted。"""
    ws, project_id, _ = _setup_project(tmp_path, monkeypatch, "fd23-proj")
    run_dir = ws.project_dir(project_id) / "runs" / "run_dead"
    run_dir.mkdir(parents=True)
    run = Run(
        run_id="run_dead",
        plan_id="plan_dead",
        project_id=project_id,
        scope=Scope(),
        status="running",
        pid=None,
        started_at=iso_now(),
        heartbeat_at=iso_now(),
    )
    write_model(run_dir / "run.json", run)

    res = cmd_run_cancel(
        ws,
        {"project_id": project_id, "run_id": "run_dead", "mark_interrupted": True},
        True,
    )
    assert res["cancel_requested"] is True
    assert res["status"] == "interrupted"
    reloaded = read_json(run_dir / "run.json")
    assert reloaded["status"] == "interrupted"


def test_fd29_github_snapshot_persists_and_enters_report_and_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """补-FD29：通过 github.read 保存快照后，project.snapshot 与报告都能读出该 commit 快照。"""
    ws, project_id, _ = _setup_project(tmp_path, monkeypatch, "fd29-proj")
    snap = {
        "repo": "team/app",
        "branch": "main",
        "commit": "abc123def",
        "files": ["src/app.py"],
        "prs": ["#42 修复登录"],
        "checks": ["ci / test"],
        "account": "alice",
        "host": "github.com",
        "fetched_at": iso_now(),
    }
    saved = cmd_github_read(ws, {"project_id": project_id, "snapshot": snap}, False)
    assert saved["commit"] == "abc123def"

    ws_snap = cmd_project_snapshot(ws, {"project_id": project_id}, False)
    assert ws_snap["github"] is not None
    assert ws_snap["github"]["commit"] == "abc123def"

    added = cmd_task_add(
        ws,
        {
            "project_id": project_id,
            "title": "GitHub 绑定报告",
            "acceptance_items": [{"item_id": "g1", "text": "登录可用"}],
        },
        True,
    )
    delivery = Delivery(
        delivery_id="deliv_1",
        task_id=added["task_id"],
        commit="abc123def",
        complete=True,
    )
    write_model(ws.project_dir(project_id) / "deliveries" / f"{added['task_id']}.json", delivery)
    run_dir = ws.project_dir(project_id) / "runs" / "run_gh"
    run_dir.mkdir(parents=True)
    plan = Plan(
        plan_id="plan_gh",
        task_id=added["task_id"],
        scope=Scope(acceptance_item_ids=["g1"]),
        applicable_count=1,
        cases=[
            Case(
                case_id="c1",
                acceptance_item_id="g1",
                layer="L2",
                expected="登录可用",
                expectation_source="登录可用",
                expectation_verified=True,
                coverage_kind="direct",
                verify_method="command:python --version",
            )
        ],
    )
    run = Run(
        run_id="run_gh",
        plan_id="plan_gh",
        project_id=project_id,
        scope=plan.scope,
        status="completed",
        started_at=iso_now(),
        cases=[RunCase(case_id="c1", result="passed")],
    )
    write_model(run_dir / "plan.json", plan)
    write_model(run_dir / "run.json", run)
    report = build_report(ws, ws.load_project(project_id), run_dir, use_model=False)
    assert report.github is not None
    assert report.github["commit"] == "abc123def"


def test_fv28_fd30_ignored_features_persist_and_survive_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """补-FV28/FD30：忽略业务功能持久化到 project_map.json 并在刷新与推荐上下文保留。"""
    ws, project_id, _ = _setup_project(tmp_path, monkeypatch, "fv28-proj")
    updated = cmd_project_map(
        ws,
        {
            "project_id": project_id,
            "action": "set_ignored",
            "ignored_features": ["旧统计报表", "旧统计报表", " "],
        },
        False,
    )
    assert updated["map"]["ignored_features"] == ["旧统计报表"]

    class FakeMapClient:
        def project_map(self, _ctx: str) -> dict[str, Any]:
            return {
                "business_functions": [
                    {
                        "name": "登录鉴权",
                        "modules": ["auth"],
                        "entry_points": ["app.py"],
                        "test_commands": ["pytest"],
                        "testable_items": ["用户登录"],
                        "evidence": ["app.py"],
                        "confidence": "high",
                    }
                ],
                "unknowns": [],
            }

    monkeypatch.setattr("aitest.commands.build_client", lambda *_a: FakeMapClient())
    refreshed = cmd_project_map_refresh(ws, {"project_id": project_id}, False)
    assert refreshed["map"]["ignored_features"] == ["旧统计报表"]
    ctx = _task_signal_context(ws, ws.load_project(project_id))
    assert "旧统计报表" in ctx
    assert "已忽略的业务功能" in ctx


def test_security_ignore_patterns_http_allowlist_and_model_token_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证 .gitignore/.env 忽略过滤、HTTP 主机白名单、模型二次脱敏与 Token 预算刹车。"""
    root = tmp_path / "sec-repo"
    root.mkdir()
    (root / ".gitignore").write_text("secrets_dir/\n*.dump\n", encoding="utf-8")
    (root / ".aitestignore").write_text("internal_keys.txt\n", encoding="utf-8")
    patterns = load_ignore_patterns(root)

    ignored_rels = (
        ".env",
        ".env.prod",
        "cert.pem",
        "secrets_dir/a.py",
        "db.dump",
        "internal_keys.txt",
    )
    for rel in ignored_rels:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("SECRET=1", encoding="utf-8")
        assert is_ignored_path(root, target, patterns), rel
    normal = root / "app" / "main.py"
    normal.parent.mkdir(parents=True, exist_ok=True)
    normal.write_text("print('ok')", encoding="utf-8")
    assert not is_ignored_path(root, normal, patterns)

    # HTTP 安全白名单：拦截元数据地址与未授权外网，允许显式白名单 host
    blocked_meta = run_http_sequence(
        [{"method": "GET", "url": "http://169.254.169.254/latest/meta-data/"}]
    )
    assert blocked_meta.status == "blocked"
    assert "云元数据" in blocked_meta.error

    blocked_ext = run_http_sequence([{"method": "GET", "url": "https://evil.example.com/hook"}])
    assert blocked_ext.status == "blocked"
    assert "白名单" in blocked_ext.error

    # 验证白名单放行后会真实进入发包层
    sent_urls: list[str] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 10) -> Any:
        del timeout
        sent_urls.append(req.full_url)
        body = json.dumps({"ok": True}).encode("utf-8")

        class Resp(io.BytesIO):
            status = 200

            def __enter__(self) -> Resp:
                return self

            def __exit__(self, *_a: object) -> None:
                self.close()

        return Resp(body)

    with contextlib.ExitStack():
        monkeypatch.setattr(
            "aitest.runners.http_runner._SAFE_REDIRECT_OPENER.open", fake_urlopen
        )
        monkeypatch.setattr("aitest.runners.http_runner._NO_REDIRECT_OPENER.open", fake_urlopen)
        ok_res = run_http_sequence(
            [{"method": "GET", "url": "https://staging.internal.local:8443/health"}],
            allowed_hosts=["https://staging.internal.local:8443"],
        )
    assert ok_res.status == "passed"
    assert sent_urls == ["https://staging.internal.local:8443/health"]

    # ModelClient 二次脱敏、Token 聚合与超预算拦截
    captured_prompts: list[str] = []

    def fake_model_urlopen(req: urllib.request.Request, timeout: float = 60) -> Any:
        del timeout
        captured_prompts.append((req.data or b"").decode("utf-8"))
        payload = {
            "choices": [{"message": {"content": '{"targets": []}'}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
        }

        class Resp(io.BytesIO):
            status = 200

            def __enter__(self) -> Resp:
                return self

            def __exit__(self, *_a: object) -> None:
                self.close()

        return Resp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("aitest.adapters.model.urllib.request.urlopen", fake_model_urlopen)
    client = ModelClient(
        "https://api.example.com",
        "demo-model",
        "key-x",
        secrets=["my-raw-db-secret"],
        max_token_budget=60,
    )
    with track_token_usage(max_budget=60) as ledger:
        client.complete_json("sys", "payload with my-raw-db-secret and sk-1234567890abcdef1234")
        assert "my-raw-db-secret" not in captured_prompts[0]
        assert "sk-1234567890abcdef1234" not in captured_prompts[0]
        assert "***" in captured_prompts[0]
        assert ledger.summary() == {
            "prompt_tokens": 30,
            "completion_tokens": 20,
            "total_tokens": 50,
            "calls": 1,
        }
        client.complete_json("sys", "second call reaches 100 tokens")
        with pytest.raises(AiTestError) as budget_exc:
            client.complete_json("sys", "third call blocked")
        assert budget_exc.value.info.code == "TOKEN_BUDGET_EXCEEDED"
