"""Worker 拆分模块：lifecycle。"""

from __future__ import annotations

import argparse
import contextvars
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from aitest.adapters.model import TokenLedger, track_token_usage
from aitest.config import known_secret_values
from aitest.core.analysis import analyze_run
from aitest.core.evidence import (
    LocalFileSnapshotIsolator,
    data_prefix,
    evidence_bytes,
)
from aitest.core.issues import draft_failed_issues, prepare_issue_drafts
from aitest.core.judge import (
    build_run_hypotheses,
    compute_case_confidence,
    judge_case_failure,
)
from aitest.core.manual import is_manual_case
from aitest.core.preflight import run_preflight
from aitest.core.probes import run_deterministic_probes
from aitest.core.project_context import (
    detect_services,
    python_dependencies,
)
from aitest.core.reports import build_report, prepare_report_ai
from aitest.core.service_logs import (
    capture_log_offsets,
    case_expects_server_error,
    inspect_service_log_window,
)
from aitest.core.shared_config import resolve_environment_profile
from aitest.errors import AiTestError
from aitest.model_config import model_settings
from aitest.models import Attempt, Case, Plan, Project, Run, RunCase, case_item_ids
from aitest.procutil import process_created_at
from aitest.security import (
    redact,
    redact_bytes,
    redact_tree,
)
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now
from aitest.worker_impl.deps_and_l1 import (
    _case_dependencies_ready,
    _dependency_suggestion,
    _environment_failure,
    _has_product_failure,
    _install_dependencies,
    _missing_command_tool,
    _missing_modules_from_output,
    _run_l1,
)
from aitest.worker_impl.env import (
    DELIVERY_INCOMPLETE_MESSAGE,
    EvidenceSettings,
    _configured_python,
    _delivery_incomplete,
    _dotenv_values,
    _environment_fingerprint,
    _evidence_settings,
    _heartbeat,
    _project_config,
    _request_id_header,
    _run_env,
    _save,
    _save_evidence,
    _truthy,
    _venv_python,
    worker_attr,
)
from aitest.worker_impl.execution import (
    _destructive_case,
    _execute,
    _risky_retry,
    _screenshot_artifacts,
    _screenshot_files,
)
from aitest.worker_impl.services import (
    _case_needs_services,
    _healthy,
    _service_block_reason,
    _services,
    _start_service,
    _stop_service,
)


def _record_postprocess_error(run_dir: Path, stage: str, exc: Exception) -> None:
    with (run_dir / "postprocess-error.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"[{stage}] {exc}\n")



def _set_postprocess(
    ws: Workspace,
    run_dir: Path,
    state: str,
    *,
    project_id: str | None = None,
    token_usage: dict[str, int] | None = None,
) -> None:
    """Update only the postprocess marker, preserving fields written meanwhile."""
    path = run_dir / "run.json"
    lock_ctx = (
        ws.project_lock(project_id, timeout=30.0)
        if project_id
        else ws.lock(timeout=30.0)
    )
    with lock_ctx:
        try:
            current = read_model(path, Run)
        except AiTestError:
            return
        current.postprocess = state  # type: ignore[assignment]
        if token_usage and token_usage.get("calls", 0) > 0:
            current.token_usage = token_usage
        write_model(path, current)



def _postprocess(
    ws: Workspace,
    project: Project,
    run_dir: Path,
    run: Run,
    *,
    token_ledger: TokenLedger | None = None,
) -> None:
    """无锁并发运行后处理 AI 请求，仅在最终落盘阶段短持单项目写锁。"""
    _set_postprocess(ws, run_dir, "running", project_id=project.project_id)
    try:
        has_failure = any(case.result in {"failed", "blocked", "error"} for case in run.cases)
        needs_issues = any(case.result in {"failed", "passed"} for case in run.cases)
        needs_report = run.status in {"completed", "execution_error", "cancelled"}
        precomputed_drafts: dict[str, dict[str, Any]] | None = None
        previous_results: dict[str, str] | None = None
        precomputed_mock: list[dict[str, Any]] | None = None
        precomputed_summary: tuple[str, Literal["ai", "fallback"]] | None = None

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="aitest-post") as pool:
            analysis_fut = (
                pool.submit(
                    contextvars.copy_context().run,
                    analyze_run,
                    ws,
                    project.project_id,
                    run_dir,
                )
                if has_failure
                else None
            )
            issues_fut = (
                pool.submit(
                    contextvars.copy_context().run,
                    prepare_issue_drafts,
                    ws,
                    project.project_id,
                    run_dir,
                )
                if needs_issues
                else None
            )
            report_fut = (
                pool.submit(
                    contextvars.copy_context().run,
                    prepare_report_ai,
                    ws,
                    project,
                    run_dir,
                    use_model=run.kind != "project_check",
                )
                if needs_report
                else None
            )
            if analysis_fut is not None:
                try:
                    analysis_fut.result()
                except Exception as exc:  # noqa: BLE001 - 后处理不改变运行主状态
                    _record_postprocess_error(run_dir, "analysis", exc)
            if issues_fut is not None:
                try:
                    precomputed_drafts, previous_results = issues_fut.result()
                except Exception as exc:  # noqa: BLE001 - 问题草拟失败不阻塞报告
                    _record_postprocess_error(run_dir, "issues", exc)
            if report_fut is not None:
                try:
                    precomputed_mock, precomputed_summary = report_fut.result()
                except Exception as exc:  # noqa: BLE001 - 报告失败不改变运行主状态
                    _record_postprocess_error(run_dir, "report", exc)

        if needs_issues or needs_report:
            try:
                with ws.project_lock(project.project_id, timeout=300.0):
                    if needs_issues:
                        try:
                            draft_failed_issues(
                                ws,
                                project.project_id,
                                run_dir,
                                precomputed_drafts=precomputed_drafts,
                                previous_results=previous_results,
                            )
                        except Exception as exc:  # noqa: BLE001 - 记录错误后继续生成报告
                            _record_postprocess_error(run_dir, "issues", exc)
                    if needs_report:
                        try:
                            build_report(
                                ws,
                                project,
                                run_dir,
                                use_model=run.kind != "project_check",
                                precomputed_mock=precomputed_mock,
                                precomputed_summary=precomputed_summary,
                            )
                        except Exception as exc:  # noqa: BLE001 - 记录错误但不崩溃
                            _record_postprocess_error(run_dir, "report", exc)
            except Exception as exc:  # noqa: BLE001 - 锁获取失败留痕
                _record_postprocess_error(run_dir, "lock", exc)
    finally:
        state = "error" if (run_dir / "postprocess-error.txt").is_file() else "done"
        usage_summary = token_ledger.summary() if token_ledger is not None else None
        _set_postprocess(
            ws,
            run_dir,
            state,
            project_id=project.project_id,
            token_usage=usage_summary,
        )


def _allowed_hosts(
    config: dict[str, Any],
    service_configs: list[tuple[str, dict[str, Any]]],
    run_env: dict[str, str],
) -> list[str]:
    """从项目安全白名单、已配置服务与探测环境变量收集允许访问的 HTTP 主机。"""
    hosts: list[str] = []
    security = config.get("security")
    if isinstance(security, dict):
        raw_allowed = security.get("allowed_hosts")
        if isinstance(raw_allowed, list):
            hosts.extend(
                str(item)
                for item in raw_allowed
                if isinstance(item, str) and item.strip()
            )
    services = config.get("services")
    if isinstance(services, dict):
        for key in ("base_url", "base", "health", "url"):
            val = services.get(key)
            if isinstance(val, str) and val.strip():
                hosts.append(val.strip())
    for _name, svc in service_configs:
        for key in ("base_url", "base", "health", "url"):
            val = svc.get(key)
            if isinstance(val, str) and val.strip():
                hosts.append(val.strip())
    for env_key in ("BASE_URL", "APP_BASE_URL", "BACKEND_BASE_URL", "PLAYWRIGHT_BASE_URL"):
        val = run_env.get(env_key)
        if isinstance(val, str) and val.strip():
            hosts.append(val.strip())
    return list(dict.fromkeys(hosts))



def _snapshot_isolation_enabled(config: dict[str, Any]) -> bool:
    """只有显式配置 data.snapshot_files 时才在每个用例前做文件还原。"""
    data_cfg = config.get("data")
    files = data_cfg.get("snapshot_files") if isinstance(data_cfg, dict) else None
    return isinstance(files, list) and any(
        isinstance(item, str) and item.strip() for item in files
    )


def _reset_case_isolation(
    workdir: Path,
    run: Run,
    run_dir: Path,
    config: dict[str, Any],
    states: list[tuple[LocalFileSnapshotIsolator, dict[str, Any]]],
) -> list[tuple[LocalFileSnapshotIsolator, dict[str, Any]]]:
    """在用例边界还原已配置的本地数据快照，避免前序用例污染后序用例。"""
    if not states:
        return states
    for isolator, state in states:
        isolator.cleanup(state)
    iso = LocalFileSnapshotIsolator()
    return [(iso, iso.prepare(workdir, run.run_id, run_dir, config))]


def _run_service_resets(
    workdir: Path,
    run_dir: Path,
    service_configs: list[tuple[str, dict[str, Any]]],
    run_env: dict[str, str],
    secrets: list[str],
) -> None:
    """在修改/删除类用例后执行声明的 services.reset，重置服务端共享状态。"""
    for name, cfg in service_configs:
        command = str(cfg.get("reset") or "").strip()
        if not command:
            continue
        try:
            timeout = float(cfg.get("timeout_seconds") or 60)
        except (TypeError, ValueError):
            timeout = 60.0
        log_path = run_dir / (
            f"service-{name}-reset.log" if name else "service-reset.log"
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=workdir,
                env=run_env,
                capture_output=True,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            status = result.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            output = str(exc).encode("utf-8", "replace")
            status = -1
        with log_path.open("ab") as handle:
            handle.write(redact_bytes(output, secrets))
            handle.write(f"\n[reset exit={status}]\n".encode())


def _has_reset_command(service_configs: list[tuple[str, dict[str, Any]]]) -> bool:
    return any(str(cfg.get("reset") or "").strip() for _, cfg in service_configs)


def _isolate_after_write(
    workdir: Path,
    run_dir: Path,
    service_configs: list[tuple[str, dict[str, Any]]],
    service_states: list[dict[str, Any]],
    run_env: dict[str, str],
    secrets: list[str],
    cancelled: Callable[[], bool],
    *,
    destructive: bool,
) -> bool:
    del cancelled, destructive
    if _has_reset_command(service_configs):
        _run_service_resets(workdir, run_dir, service_configs, run_env, secrets)
    return all(state.get("ok", True) for state in service_states)




def _downgrade_script_failures(plan: Plan, run: Run) -> None:
    """保持生成脚本失败状态不被覆盖，仅在存在矛盾时补充人工核查提示。

    即使同验收项下项目的命令测试通过，也不能武断认为生成脚本必错或产品无缺陷，
    因此保留 ``failed`` 结果并写入提醒，由人工判断属于脚本期望偏差还是测试覆盖缺口。
    """
    passed_command_items: set[str] = set()
    for case in plan.cases:
        run_case = next(
            (item for item in run.cases if item.case_id == case.case_id), None
        )
        if (
            run_case is not None
            and run_case.result == "passed"
            and case.verify_method.startswith(("command:", "pytest:"))
            and case.coverage_kind not in {"file", "shared", "unverified"}
            and not (case.coverage_kind == "direct" and not case.expectation_verified)
        ):
            passed_command_items.update(case_item_ids(case))
    note = "项目命令测试通过但生成脚本失败，请人工核查脚本预期或测试覆盖缺口"
    for case in plan.cases:
        if not case.verify_method.startswith("script:"):
            continue
        run_case = next(
            (item for item in run.cases if item.case_id == case.case_id), None
        )
        if run_case is None or run_case.result != "failed":
            continue
        items = case_item_ids(case)
        if items and all(item in passed_command_items for item in items):
            if run_case.attempts:
                last = run_case.attempts[-1]
                if note not in last.message:
                    last.message = f"{last.message}；{note}" if last.message else note
            else:
                run_case.attempts.append(
                    Attempt(
                        attempt=1,
                        result="failed",
                        started_at=iso_now(),
                        finished_at=iso_now(),
                        message=note,
                    )
                )



def _mark_cancelled(run: Run) -> None:
    """Stop at a case boundary: executed results stay, the rest are not_run."""
    run.status = "cancelled"
    run.current_case_id = ""
    for case in run.cases:
        if case.result not in {"passed", "failed", "blocked", "error", "manual_pending"}:
            case.result = "not_run"



def _record_boot_failure(
    run_dir: Path,
    run_path: Path,
    exc: Exception,
    secrets: list[str] | None = None,
) -> None:
    secret_values = secrets if secrets is not None else known_secret_values()
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "worker-error.txt").write_text(
            redact(str(exc), secret_values), encoding="utf-8"
        )
    except OSError:
        pass
    try:
        if run_path.is_file():
            run = read_model(run_path, Run)
            run.status = "execution_error"
            run.current_case_id = ""
            run.heartbeat_at = iso_now()
            write_model(run_path, run)
    except Exception:  # noqa: BLE001 - the error file is the last diagnostic
        pass



def _is_build_case(case: Case | None, build_command: str) -> bool:
    return (
        bool(build_command)
        and case is not None
        and case.verify_method.strip().removeprefix("command:").strip() == build_command
    )


def _case_execution_rank(case: Case | None, build_command: str) -> int:
    """先构建检查，再只读，再创建(POST)，最后修改/删除类用例。"""
    if case is not None and _destructive_case(case):
        return 3
    if case is not None and _risky_retry(case):
        return 2
    return 0 if _is_build_case(case, build_command) else 1



def _save_and_track(
    run_dir: Path,
    name: str,
    data: bytes,
    result: str,
    evidence_settings: EvidenceSettings,
    secret_list: list[str],
    used_bytes: int,
    refs: list[str],
) -> tuple[int, bool]:
    """写入证据并增量更新已用字节数；仅当发生覆盖旧文件时回退全目录统计。"""
    target_path = run_dir / "evidence" / name
    overwritten = target_path.is_file()
    ref = _save_evidence(
        run_dir,
        name,
        data,
        result,
        evidence_settings,
        secret_list,
        current_bytes=used_bytes,
    )
    if ref is None:
        return used_bytes, False
    refs.append(ref)
    if overwritten:
        return evidence_bytes(run_dir), True
    try:
        return used_bytes + (run_dir / ref).stat().st_size, False
    except OSError:
        return evidence_bytes(run_dir), True


def _blocked_stage(result: str, message: str) -> Literal["setup", "verify", ""]:
    if result == "blocked" and (
        "前置准备阶段（setup）失败" in message
        or "HTTP 401/403" in message
        or "stage must be 'setup'" in message
    ):
        return "setup"
    if result in {"passed", "failed"}:
        return "verify"
    return ""


_EXPECTED_AUTH_FAILURE_RE = re.compile(
    r"\b40[13]\b|未带\s*authorization|未授权|无权限|没有权限|非法\s*(?:bearer\s*)?token|invalid\s+token",
    re.IGNORECASE,
)


def _case_expects_auth_failure(case: Case) -> bool:
    """判断验收项是否在主动验收 401/403（负向鉴权），此时不能补认证改写。"""
    text = f"{case.title} {case.expected} {case.expectation_source}"
    return bool(_EXPECTED_AUTH_FAILURE_RE.search(text))


def _config_base_url(cfg: dict[str, Any]) -> str:
    """从服务配置的 health/base_url/url 中取出 scheme://netloc。"""
    for key in ("health", "health_url", "base_url", "url"):
        raw = str(cfg.get(key) or "").strip()
        if not raw:
            continue
        parsed = urlsplit(raw)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _resolve_probe_base_url(
    service_configs: list[tuple[str, dict[str, Any]]],
    run_env: dict[str, str],
) -> str:
    for key in ("APP_BASE_URL", "BACKEND_BASE_URL", "BASE_URL"):
        raw = str(run_env.get(key) or "").strip()
        if raw.startswith(("http://", "https://")):
            return raw.rstrip("/")
    for _name, cfg in service_configs:
        base = _config_base_url(cfg)
        if base:
            return base
    return ""


_FRONTEND_SERVICE_HINTS = frozenset(
    {"frontend", "front", "web", "client", "ui", "vite", "spa"}
)
_BACKEND_SERVICE_HINTS = frozenset(
    {"backend", "back", "api", "server", "app", "service"}
)
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'`)<>,{}$]+")


def _service_role(name: str) -> str:
    """按服务名的分词判断角色；无法确定时返回空串，沿用默认目标。"""
    tokens = {token for token in re.split(r"[^a-z0-9]+", name.strip().lower()) if token}
    if not tokens:
        return ""
    if tokens & _BACKEND_SERVICE_HINTS:
        return "backend"
    if tokens & _FRONTEND_SERVICE_HINTS:
        return "frontend"
    return ""


def _probe_targets(
    service_states: list[dict[str, Any]],
    *,
    base_url: str,
    service_ready: bool,
) -> dict[str, tuple[str, bool]]:
    """按探针角色分配目标服务：静态资源打前端，路由边界打后端。

    没有可识别的角色服务时沿用原有单一 base_url，保持单服务项目行为不变。
    """
    default = (base_url, service_ready)
    targets: dict[str, tuple[str, bool]] = {
        "contract_reconciliation": default,
    }
    frontend: tuple[str, bool] | None = None
    backend: tuple[str, bool] | None = None
    for state in service_states:
        name = str(state.get("name") or "")
        cfg = state.get("config")
        if not isinstance(cfg, dict):
            continue
        url = _config_base_url(cfg)
        if not url:
            continue
        ok = bool(state.get("ok"))
        role = _service_role(name)
        if role == "frontend" and frontend is None:
            frontend = (url, ok)
        elif role == "backend" and backend is None:
            backend = (url, ok)
    if frontend is not None:
        targets["static_assets"] = frontend
    else:
        targets["static_assets"] = default
    if backend is not None:
        targets["route_boundary"] = backend
    else:
        targets["route_boundary"] = default
    return targets


def _run_preflight_gate(
    *,
    workdir: Path,
    config: dict[str, Any],
    service_configs: list[tuple[str, dict[str, Any]]],
    run_env: dict[str, str],
    run_dir: Path,
    run: Run,
    evidence_settings: EvidenceSettings,
    secret_list: list[str],
) -> tuple[list[tuple[str, dict[str, Any]]], set[str]]:
    """执行开测前环境安检门，返回按拓扑序排列的服务与端口冲突服务名集合。"""
    preflight = run_preflight(
        workdir,
        config,
        service_configs,
        run_env,
        healthy_fn=_healthy,
    )
    safe_preflight = redact_tree(preflight, secret_list)
    if isinstance(safe_preflight, dict):
        run.preflight = safe_preflight
    by_key = {name: cfg for name, cfg in service_configs}
    ordered_keys = preflight.get("ordered_service_keys") or []
    ordered_configs = [
        (key, by_key[key])
        for key in ordered_keys
        if isinstance(key, str) and key in by_key
    ] or list(service_configs)
    has_signal = bool(
        preflight.get("port_checks")
        or preflight.get("tool_checks")
        or preflight.get("blocking_reasons")
        or preflight.get("warnings")
    )
    if has_signal:
        payload_bytes = json.dumps(
            run.preflight or {}, ensure_ascii=False, indent=2
        ).encode("utf-8")
        pf_result = "passed" if preflight.get("ok") else "failed"
        refs: list[str] = []
        run.media_bytes, _ = _save_and_track(
            run_dir,
            "preflight.json",
            payload_bytes,
            pf_result,
            evidence_settings,
            secret_list,
            run.media_bytes,
            refs,
        )
        for ref in refs:
            if ref not in run.evidence_refs:
                run.evidence_refs.append(ref)
    conflicted = {
        str(name) for name in (preflight.get("conflicted_services") or []) if str(name)
    }
    return ordered_configs, conflicted


def _run_probes_stage(
    *,
    workdir: Path,
    config: dict[str, Any],
    service_configs: list[tuple[str, dict[str, Any]]],
    service_states: list[dict[str, Any]],
    run_env: dict[str, str],
    service_ok: bool,
    allowed_hosts: list[str],
    run_dir: Path,
    run: Run,
    evidence_settings: EvidenceSettings,
    secret_list: list[str],
) -> None:
    """运行免 AI 确定性硬探针并将有实质发现或检查记录保存至 Run 与证据目录。"""
    base_url = _resolve_probe_base_url(service_configs, run_env)
    service_ready = bool(service_ok and base_url)
    probe_targets = _probe_targets(
        service_states,
        base_url=base_url,
        service_ready=service_ready,
    )
    probes = run_deterministic_probes(
        workdir,
        config,
        base_url=base_url,
        allowed_hosts=allowed_hosts,
        service_ready=service_ready,
        probe_targets=probe_targets,
        secrets=secret_list,
    )
    run.probe_results = probes
    if any(probe.get("status") != "skipped" for probe in probes):
        has_failed = any(probe.get("status") == "failed" for probe in probes)
        probe_bytes = json.dumps(probes, ensure_ascii=False, indent=2).encode("utf-8")
        refs: list[str] = []
        run.media_bytes, _ = _save_and_track(
            run_dir,
            "probes.json",
            probe_bytes,
            "failed" if has_failed else "passed",
            evidence_settings,
            secret_list,
            run.media_bytes,
            refs,
        )
        for ref in refs:
            if ref not in run.evidence_refs:
                run.evidence_refs.append(ref)


def _case_target_hosts(case: Case) -> set[str]:
    """提取用例脚本/HTTP 声明中的目标 host:port，用于按目标服务关联后台日志。"""
    hosts: set[str] = set()
    for text in (case.verify_method or "", case.script_content or ""):
        for raw in _URL_IN_TEXT_RE.findall(text):
            try:
                parsed = urlsplit(raw)
            except ValueError:
                continue
            if parsed.netloc:
                hosts.add(parsed.netloc.lower())
    return hosts


def _execute_with_service_logs(
    *,
    case: Case,
    workdir: Path,
    run_dir: Path,
    run_env: dict[str, str],
    project_python: Path,
    request_id_header: str,
    request_id: str,
    cancelled: Callable[[], bool],
    allowed_hosts: list[str],
    service_states: list[dict[str, Any]],
    secret_list: list[str],
) -> tuple[str, list[bytes], str, list[tuple[str, bytes]], list[str], list[str], float]:
    """带后台服务日志时间窗口抓取地执行单轮用例，截获静默 50x/Traceback 假通过。"""
    offsets = capture_log_offsets(service_states)
    started = time.monotonic()
    result, outputs, message, artifacts = _execute(
        case,
        workdir,
        run_dir / "generated",
        run_env,
        project_python,
        request_id_header=request_id_header,
        request_id=request_id,
        run_dir=run_dir,
        cancelled=cancelled,
        allowed_hosts=allowed_hosts,
    )
    elapsed = time.monotonic() - started
    if (case.verify_method or "").startswith(("http:", "script:", "playwright:")) and any(
        state.get("started") and state.get("ok") for state in service_states
    ):
        # 留出短暂窗口给后台日志采集线程把管道中的最后几行 flush 落盘，防止快脚本退出竞态漏抓。
        time.sleep(0.05)
    window = inspect_service_log_window(
        service_states,
        offsets,
        case_method=case.verify_method or "",
        expects_server_error=case_expects_server_error(
            case.title, case.expected, case.expectation_source
        ),
        case_targets=_case_target_hosts(case),
        secrets=secret_list,
    )
    window_bytes = cast(bytes, window.get("window_log") or b"")
    if window_bytes:
        artifacts = [*artifacts, ("service-window.log", window_bytes)]
    fatal_errors = list(cast(list[str], window.get("fatal_errors") or []))
    log_warnings = list(cast(list[str], window.get("warnings") or []))
    # 阶段 2 红线：后台日志仅作诊断附件与告警记录，绝不把已通过断言的用例翻盘改判为 failed
    return result, outputs, message, artifacts, fatal_errors, log_warnings, elapsed


def _persist_case_attempt(
    *,
    run_dir: Path,
    case_id: str,
    outputs: list[bytes],
    artifacts: list[tuple[str, bytes]],
    result: str,
    message: str,
    evidence_settings: EvidenceSettings,
    secret_list: list[str],
    run_case: RunCase,
    run: Run,
    is_http: bool,
    request_id: str,
    attempt_prefix: str = "",
    stage: Literal["setup", "verify", ""] = "",
) -> list[str]:
    """保存单轮执行证据并追加到 run_case.attempts，重跑前可加前缀避免覆盖日志。"""
    refs: list[str] = []
    used_bytes = run.media_bytes
    for position, data in enumerate(outputs):
        used_bytes, _ = _save_and_track(
            run_dir,
            f"{case_id}-{attempt_prefix}{position}.log",
            data,
            result,
            evidence_settings,
            secret_list,
            used_bytes,
            refs,
        )
    for name, data in artifacts:
        used_bytes, _ = _save_and_track(
            run_dir,
            f"{case_id}-{attempt_prefix}{name}",
            data,
            result,
            evidence_settings,
            secret_list,
            used_bytes,
            refs,
        )
    if is_http and request_id not in run.request_ids:
        run.request_ids.append(request_id)
    if result == "passed" and not refs:
        # A silent passing command still needs a citable log, so a
        # pass can never be an evidence-free claim.
        used_bytes, _ = _save_and_track(
            run_dir,
            f"{case_id}-{attempt_prefix}0.log",
            f"用例 {case_id} 通过；命令无输出。".encode(),
            result,
            evidence_settings,
            secret_list,
            used_bytes,
            refs,
        )
    run.media_bytes = used_bytes
    run_case.attempts.append(
        Attempt(
            attempt=len(run_case.attempts) + 1,
            result=result,  # type: ignore[arg-type]
            stage=stage,
            started_at=iso_now(),
            finished_at=iso_now(),
            evidence_refs=refs,
            message=message,
        )
    )
    run_case.evidence_refs.extend(refs)
    run.evidence_refs.extend(refs)
    return refs



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)

    ws = Workspace(Path(args.workspace))
    run_dir = ws.project_dir(args.project) / "runs" / args.run
    run_path = run_dir / "run.json"
    secret_list = known_secret_values()
    try:
        plan = read_model(run_dir / "plan.json", Plan)
        run = read_model(run_path, Run)
        # The worker owns this run directory: record its own pid so a parent race
        # cannot leave run.json pointing at None.
        run.pid = os.getpid()
        run.pid_created_at = process_created_at(run.pid)
        project = read_model(ws.project_path(args.project), Project)
        workdir = Path(project.root)
        cases = {case.case_id: case for case in plan.cases}

        config = _project_config(ws, args.project)
        allowed_role_envs: set[str] = set()
        raw_roles = config.get("test_roles")
        if isinstance(raw_roles, dict):
            for role_spec in raw_roles.values():
                if not isinstance(role_spec, dict):
                    continue
                for rkey, rval in role_spec.items():
                    if str(rkey).lower().endswith(("_env", "_env_var")) and isinstance(rval, str):
                        name_str = rval.strip()
                        if name_str:
                            allowed_role_envs.add(name_str)
        configured_python = _configured_python(config, workdir)
        project_python = configured_python or _venv_python(workdir)
        run_env = _run_env(
            workdir,
            project_python,
            run.run_id,
            allowed_test_role_envs=allowed_role_envs,
        )
        case_python = project_python or Path(sys.executable)
        requested_profile = str(run.environment.get("profile") or "")
        run.environment = worker_attr(
            "_environment_fingerprint", _environment_fingerprint
        )(case_python, workdir)
        dependency_cache: dict[tuple[str, ...], list[str] | None] = {}
        dependency_suggestion_cache: dict[str, dict[str, str]] = {}
        command_cache: dict[str, tuple[str, list[bytes], str, list[tuple[str, bytes]]]] = {}
        env_profile = resolve_environment_profile(config, requested_profile)
        if env_profile.get("name"):
            run.environment["profile"] = str(env_profile["name"])
        for env_key, env_val in (env_profile.get("env") or {}).items():
            run_env[str(env_key)] = str(env_val)
        profile_base_url = str(env_profile.get("base_url") or "")
        if profile_base_url:
            run_env["APP_BASE_URL"] = profile_base_url
            run_env["BASE_URL"] = profile_base_url
            run_env["BACKEND_BASE_URL"] = profile_base_url
        effective_config = dict(config)
        profile_services = env_profile.get("services")
        if isinstance(profile_services, dict) and profile_services:
            raw_services = config.get("services")
            base_services = dict(raw_services) if isinstance(raw_services, dict) else {}
            base_services.update(profile_services)
            effective_config["services"] = base_services
        secret_list = known_secret_values([effective_config, _dotenv_values(workdir)])
        service_configs = [] if run.kind == "project_check" else _services(effective_config)
        if (
            not service_configs
            and run.kind != "project_check"
            and any(_case_needs_services(case) for case in plan.cases)
        ):
            detected = detect_services(workdir)
            if detected:
                service_configs = list(detected.items())
                for preferred in ("app", "backend", "frontend", *detected.keys()):
                    if preferred in {"db", "mailpit"}:
                        continue
                    cfg = detected.get(preferred)
                    if not isinstance(cfg, dict):
                        continue
                    raw_health = str(cfg.get("health") or "").strip()
                    if not raw_health:
                        continue
                    parsed = urlsplit(raw_health)
                    base_url = (
                        f"{parsed.scheme}://{parsed.netloc}"
                        if parsed.scheme and parsed.netloc
                        else raw_health.rstrip("/")
                    )
                    if base_url:
                        run_env.setdefault("APP_BASE_URL", base_url)
                        run_env.setdefault("BASE_URL", base_url)
                        if preferred in {"app", "backend"}:
                            run_env.setdefault("BACKEND_BASE_URL", base_url)
                        break
        delivery_incomplete = _delivery_incomplete(ws, args.project, plan.task_id)
        evidence_settings = _evidence_settings(config)
        request_id_header = _request_id_header(config)
        if not run.data_prefix:
            run.data_prefix = data_prefix(run.run_id)
        run.heartbeat_at = iso_now()
        _save(run_path, run)
    except Exception as exc:  # noqa: BLE001 - startup must leave a visible failure
        _record_boot_failure(run_dir, run_path, exc, secret_list)
        return 1

    def cancelled() -> bool:
        return bool((run_dir / "cancel.requested").exists())

    allowed_hosts = _allowed_hosts(effective_config, service_configs, run_env)
    for host_entry in env_profile.get("allowed_hosts") or []:
        if host_entry and host_entry not in allowed_hosts:
            allowed_hosts.append(str(host_entry))
    raw_budget = model_settings(ws, args.project).get("max_token_budget")
    max_budget = raw_budget if isinstance(raw_budget, int) else None
    token_ctx = track_token_usage(max_budget=max_budget)
    token_ledger = token_ctx.__enter__()
    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(run_path, stop), daemon=True)
    beat.start()
    service_states: list[dict[str, Any]] = []
    isolator_states: list[tuple[LocalFileSnapshotIsolator, dict[str, Any]]] = []
    dependency_ok = True
    try:
        iso = LocalFileSnapshotIsolator()
        isolator_states = [(iso, iso.prepare(workdir, run.run_id, run_dir, config))]
        run.dependencies = python_dependencies(
            sorted({dependency for case in plan.cases for dependency in case.dependencies})
        )
        if run.dependencies and not cancelled():
            runners_cfg = config.get("runners")
            allow_host_install = (
                _truthy(runners_cfg.get("allow_host_install"))
                if isinstance(runners_cfg, dict)
                else False
            )
            dependency_ok, dependency_ref = _install_dependencies(
                ws,
                run_dir,
                project_python or Path(sys.executable),
                run.dependencies,
                evidence_settings,
                secret_list,
                run_env,
                workdir,
                allow_shared_host=allow_host_install,
            )
            if dependency_ref is not None:
                run.evidence_refs.append(dependency_ref)
            run.dependency_install_ok = dependency_ok
            _save(run_path, run)
        l1_config = config.get("l1")
        build_command = (
            str(l1_config.get("build") or "").strip()
            if isinstance(l1_config, dict)
            else ""
        )
        plan_has_build = bool(build_command) and any(
            case.verify_method.strip().removeprefix("command:").strip() == build_command
            for case in plan.cases
        )
        l1 = _run_l1(
            config,
            workdir,
            run_dir,
            run_env,
            project_python or Path(sys.executable),
            evidence_settings,
            secret_list,
            cancelled,
            skip_build=plan_has_build,
        )
        run.l1 = cast(dict[str, Any], l1)
        build_ok = True
        if l1 is not None:
            build_ok = l1["build"]["ok"] and all(entry["ok"] for entry in l1["entries"])
            l1_refs = [
                l1["build"]["evidence_ref"],
                *(entry["evidence_ref"] for entry in l1["entries"]),
            ]
            for ref in l1_refs:
                if ref is not None and ref not in run.evidence_refs:
                    run.evidence_refs.append(ref)
        if l1 is not None and l1["cancelled"]:
            _mark_cancelled(run)
        else:
            conflicted_services: set[str] = set()
            preflight_blocking: list[str] = []
            if run.kind != "project_check":
                service_configs, conflicted_services = _run_preflight_gate(
                    workdir=workdir,
                    config=config,
                    service_configs=service_configs,
                    run_env=run_env,
                    run_dir=run_dir,
                    run=run,
                    evidence_settings=evidence_settings,
                    secret_list=secret_list,
                )
                if isinstance(run.preflight, dict):
                    preflight_blocking = [
                        str(item)
                        for item in (run.preflight.get("blocking_reasons") or [])
                        if str(item).strip()
                    ]
            if not preflight_blocking:
                for name, service_config in service_configs:
                    label = name or "default"
                    log_name = f"service-{name}.log" if name else "service.log"
                    log_path = run_dir / log_name
                    if label in conflicted_services:
                        log_path.parent.mkdir(parents=True, exist_ok=True)
                        with log_path.open("ab") as handle:
                            handle.write(
                                f"环境安检门拦截：服务 {label} 端口占用且不健康\n".encode()
                            )
                        service_states.append(
                            {
                                "name": name,
                                "config": service_config,
                                "started": False,
                                "ok": False,
                                "log": log_path,
                                "process": None,
                            }
                        )
                        break
                    started, ok, service_process = _start_service(
                        service_config,
                        workdir,
                        log_path,
                        secret_list,
                        run_env,
                        cancelled,
                        run_dir=run_dir,
                        service_name=name,
                    )
                    service_states.append(
                        {
                            "name": name,
                            "config": service_config,
                            "started": started,
                            "ok": ok,
                            "log": log_path,
                            "process": service_process,
                        }
                    )
                    if not ok or cancelled():
                        break
            service_ok = (not preflight_blocking) and all(
                state["ok"] for state in service_states
            )
            # 外部已运行服务显式配置 log_path 时，同样纳入单用例日志时间窗防假通过
            seen_log_paths: set[str] = {
                str(state["log"].resolve())
                for state in service_states
                if isinstance(state.get("log"), Path)
            }
            raw_external_logs: list[tuple[str, Any]] = []
            for svc_name, svc_cfg in service_configs:
                raw_external_logs.append((svc_name or "default", svc_cfg.get("log_path")))
            services_section = config.get("services")
            if isinstance(services_section, dict):
                raw_external_logs.append(("services", services_section.get("log_path")))
            link_section = config.get("link")
            if isinstance(link_section, dict):
                raw_external_logs.append(("link", link_section.get("log_path")))
            for ext_label, raw_lp in raw_external_logs:
                if not isinstance(raw_lp, str) or not raw_lp.strip():
                    continue
                cand = Path(raw_lp.strip()).expanduser()
                cand = (workdir / cand).resolve() if not cand.is_absolute() else cand.resolve()
                cand_key = str(cand)
                if cand_key in seen_log_paths or not cand.is_file():
                    continue
                seen_log_paths.add(cand_key)
                service_states.append(
                    {
                        "name": f"{ext_label}:external_log",
                        "config": {},
                        "started": False,
                        "ok": True,
                        "log": cand,
                        "process": None,
                    }
                )
            run.service_started = any(state["started"] for state in service_states)
            run.service_name = "、".join(
                state["name"] or "default" for state in service_states
            )
            if run.kind != "project_check" and not cancelled():
                _run_probes_stage(
                    workdir=workdir,
                    config=config,
                    service_configs=service_configs,
                    service_states=service_states,
                    run_env=run_env,
                    service_ok=service_ok,
                    allowed_hosts=allowed_hosts,
                    run_dir=run_dir,
                    run=run,
                    evidence_settings=evidence_settings,
                    secret_list=secret_list,
                )
            _save(run_path, run)
            if cancelled():
                _mark_cancelled(run)
            else:
                exec_order = sorted(
                    run.cases,
                    key=lambda rc: _case_execution_rank(
                        cases.get(rc.case_id), build_command
                    ),
                )
                exec_cfg = config.get("execution")
                retry_on_failure = (
                    bool(exec_cfg.get("retry_on_failure", True))
                    if isinstance(exec_cfg, dict)
                    else True
                )
                case_isolation = _snapshot_isolation_enabled(config)
                previous_risky = False
                previous_destructive = False
                for index, run_case in enumerate(exec_order):
                    if cancelled():
                        for rest in exec_order[index:]:
                            if rest.result != "manual_pending":
                                rest.result = "not_run"
                        run.status = "cancelled"
                        run.current_case_id = ""
                        break
                    if index > 0 and previous_risky:
                        service_ok = _isolate_after_write(
                            workdir,
                            run_dir,
                            service_configs,
                            service_states,
                            run_env,
                            secret_list,
                            cancelled,
                            destructive=previous_destructive,
                        )
                        previous_risky = False
                        previous_destructive = False
                    if index > 0 and case_isolation:
                        isolator_states = _reset_case_isolation(
                            workdir, run, run_dir, config, isolator_states
                        )
                    case = cases.get(run_case.case_id)
                    previous_risky = case is not None and _risky_retry(case)
                    previous_destructive = case is not None and _destructive_case(case)
                    if case is None:
                        run_case.result = "error"
                        run_case.attempts.append(
                            Attempt(attempt=1, result="error", message="计划中找不到该用例")
                        )
                        continue
                    if run_case.resumed_from_run_id and run_case.result == "passed":
                        continue
                    if is_manual_case(case):
                        if run_case.result != "manual_pending":
                            run_case.result = "manual_pending"
                            run_case.attempts.append(
                                Attempt(
                                    attempt=len(run_case.attempts) + 1,
                                    result="manual_pending",
                                    started_at=iso_now(),
                                    finished_at=iso_now(),
                                    message="L3 不可自动化，等待人工记录",
                                )
                            )
                            _save(run_path, run)
                        continue
                    ready, missing_deps = _case_dependencies_ready(
                        case_python,
                        python_dependencies(case.dependencies),
                        run_env,
                        dependency_cache,
                    )
                    if not ready:
                        message = (
                            "依赖安装失败"
                            if missing_deps is None
                            else "依赖未安装：" + ", ".join(missing_deps)
                        )
                        run_case.result = "blocked"
                        run_case.attempts.append(
                            Attempt(
                                attempt=len(run_case.attempts) + 1,
                                result="blocked",
                                message=message,
                            )
                        )
                        _save(run_path, run)
                        continue
                    if preflight_blocking and (
                        case.depends_on == "service" or _case_needs_services(case)
                    ):
                        block_msg = "开测前环境安检门阻塞：" + "；".join(preflight_blocking)
                        run_case.result = "blocked"
                        run_case.attempts.append(
                            Attempt(
                                attempt=len(run_case.attempts) + 1,
                                result="blocked",
                                message=redact(block_msg, secret_list),
                            )
                        )
                        _save(run_path, run)
                        continue
                    if case.depends_on == "service" and not service_ok:
                        run_case.result = "blocked"
                        run_case.attempts.append(
                            Attempt(attempt=1, result="blocked", message="服务未就绪")
                        )
                        continue
                    if case.depends_on == "build" and delivery_incomplete:
                        run_case.result = "blocked"
                        run_case.attempts.append(
                            Attempt(
                                attempt=len(run_case.attempts) + 1,
                                result="blocked",
                                message=DELIVERY_INCOMPLETE_MESSAGE,
                            )
                        )
                        continue
                    if case.depends_on == "build" and not build_ok:
                        run_case.result = "blocked"
                        run_case.attempts.append(
                            Attempt(attempt=1, result="blocked", message="L1 检查未通过")
                        )
                        continue
                    run.current_case_id = case.case_id
                    run.heartbeat_at = iso_now()
                    _save(run_path, run)
                    is_http = case.verify_method.startswith("http:")
                    request_id = str(uuid.uuid4()) if is_http else ""
                    captures_screenshots = case.verify_method.startswith(
                        ("script:", "playwright:")
                    )
                    screenshots_before = (
                        _screenshot_files(workdir, run_dir, case.case_id)
                        if captures_screenshots
                        else set()
                    )
                    cache_key = (
                        f"{','.join(sorted(case_item_ids(case))) or '_l1_'}::{case.verify_method}"
                        if case.verify_method.startswith("command:")
                        else ""
                    )
                    cached = command_cache.get(cache_key) if cache_key else None
                    missing_tool = (
                        _missing_command_tool(case.verify_method, run_env)
                        if cache_key
                        else ""
                    )
                    service_block = _service_block_reason(
                        case.verify_method, service_configs, service_ok, run_env
                    )
                    all_log_issues: list[str] = []
                    fatal_log_errors: list[str] = []
                    first_elapsed = 0.0
                    from_cache = False
                    if missing_tool:
                        result = "blocked"
                        outputs: list[bytes] = []
                        message = f"环境缺少命令：{missing_tool}（不计为产品失败）"
                        artifacts: list[tuple[str, bytes]] = []
                    elif service_block:
                        result = "blocked"
                        outputs = []
                        message = service_block
                        artifacts = []
                    elif cached is not None:
                        from_cache = True
                        result, outputs, message, artifacts = cached
                        message = (
                            f"{message}；复用同一命令结果" if message else "复用同一命令结果"
                        )
                    else:
                        (
                            result,
                            outputs,
                            message,
                            artifacts,
                            fatal_log_errors,
                            log_warnings,
                            first_elapsed,
                        ) = _execute_with_service_logs(
                            case=case,
                            workdir=workdir,
                            run_dir=run_dir,
                            run_env=run_env,
                            project_python=project_python or Path(sys.executable),
                            request_id_header=request_id_header,
                            request_id=request_id,
                            cancelled=cancelled,
                            allowed_hosts=allowed_hosts,
                            service_states=service_states,
                            secret_list=secret_list,
                        )
                        all_log_issues.extend([*fatal_log_errors, *log_warnings])
                    suggestion: dict[str, str] | None = None
                    if result == "failed" and case.verify_method.startswith("command:"):
                        missing_modules = _missing_modules_from_output(b"".join(outputs))
                        for missing_module in missing_modules[:3]:
                            if missing_module in dependency_suggestion_cache:
                                candidate = dict(
                                    dependency_suggestion_cache[missing_module]
                                )
                            else:
                                candidate = _dependency_suggestion(
                                    ws,
                                    run.project_id,
                                    workdir,
                                    missing_module,
                                    case.verify_method,
                                )
                                dependency_suggestion_cache[missing_module] = dict(candidate)
                            if not candidate:
                                continue
                            candidate["case_id"] = case.case_id
                            if suggestion is None:
                                suggestion = candidate
                            if not any(
                                entry.get("module") == candidate["module"]
                                and entry.get("package") == candidate["package"]
                                for entry in run.dependency_suggestions
                            ):
                                run.dependency_suggestions.append(candidate)
                    if captures_screenshots:
                        artifacts = [
                            *artifacts,
                            *_screenshot_artifacts(case, workdir, run_dir, screenshots_before),
                        ]
                        if result == "passed":
                            # Passing screenshots are evidence noise: keep only the last one.
                            screenshots = [
                                item
                                for item in artifacts
                                if item[0].startswith("screenshot-")
                            ]
                            artifacts = [
                                *[
                                    item
                                    for item in artifacts
                                    if not item[0].startswith("screenshot-")
                                ],
                                *screenshots[-1:],
                            ]
                    if result == "failed" and case.verify_method.startswith("command:"):
                        combined = b"".join(outputs)
                        if suggestion and not _has_product_failure(combined):
                            result = "blocked"
                            message = (
                                f"环境缺少依赖：{suggestion['module']}"
                                f"（建议安装 {suggestion['package']}）"
                            )
                        elif not suggestion:
                            environment_reason = _environment_failure(combined, workdir)
                            if environment_reason:
                                result = "blocked"
                                message = environment_reason
                    if (
                        result == "failed"
                        and retry_on_failure
                        and case.layer != "L1"
                        and not from_cache
                        and not cancelled()
                        and first_elapsed <= 60.0
                        and not fatal_log_errors
                        and not _risky_retry(case)
                    ):
                        _persist_case_attempt(
                            run_dir=run_dir,
                            case_id=case.case_id,
                            outputs=outputs,
                            artifacts=artifacts,
                            result=result,
                            message=redact(message, secret_list),
                            evidence_settings=evidence_settings,
                            secret_list=secret_list,
                            run_case=run_case,
                            run=run,
                            is_http=is_http,
                            request_id=request_id,
                            attempt_prefix=f"try{len(run_case.attempts) + 1}-",
                            stage="verify",
                        )
                        (
                            result,
                            outputs,
                            message,
                            artifacts,
                            retry_fatal,
                            retry_warn,
                            _retry_elapsed,
                        ) = _execute_with_service_logs(
                            case=case,
                            workdir=workdir,
                            run_dir=run_dir,
                            run_env=run_env,
                            project_python=project_python or Path(sys.executable),
                            request_id_header=request_id_header,
                            request_id=request_id,
                            cancelled=cancelled,
                            allowed_hosts=allowed_hosts,
                            service_states=service_states,
                            secret_list=secret_list,
                        )
                        all_log_issues.extend([*retry_fatal, *retry_warn])
                        if result == "passed":
                            note = "首次失败后重跑通过（疑似不稳定）"
                            message = f"{message}；{note}" if message else note
                        elif result == "failed":
                            judgement = judge_case_failure(
                                ws,
                                run.project_id,
                                case=case,
                                outputs=outputs,
                                message=message,
                                service_log_issues=list(dict.fromkeys(all_log_issues)),
                                probe_results=run.probe_results,
                                secrets=secret_list,
                            )
                            run_case.judgement = judgement
                            case_hyps = build_run_hypotheses(case.case_id, judgement)
                            if case_hyps:
                                run.ai_hypotheses = [
                                    h
                                    for h in run.ai_hypotheses
                                    if h.get("case_id") != case.case_id
                                ] + case_hyps
                    elif result == "failed" and _risky_retry(case) and not cancelled():
                        # 严守 D4：PATCH/PUT/DELETE 等不可自动重跑写用例的首次真实失败
                        # 只收集假设与确定性定档，严禁降级为 blocked 或剔出 coverage.failed。
                        judgement = judge_case_failure(
                            ws,
                            run.project_id,
                            case=case,
                            outputs=outputs,
                            message=message,
                            service_log_issues=list(dict.fromkeys(all_log_issues)),
                            probe_results=run.probe_results,
                            secrets=secret_list,
                        )
                        run_case.judgement = judgement
                        case_hyps = build_run_hypotheses(case.case_id, judgement)
                        if case_hyps:
                            run.ai_hypotheses = [
                                h
                                for h in run.ai_hypotheses
                                if h.get("case_id") != case.case_id
                            ] + case_hyps
                    message = redact(message, secret_list)
                    if all_log_issues:
                        run_case.service_log_issues = list(dict.fromkeys(all_log_issues))
                    if cache_key and not from_cache:
                        command_cache[cache_key] = (
                            result,
                            list(outputs),
                            message,
                            list(artifacts),
                        )
                    refs = _persist_case_attempt(
                        run_dir=run_dir,
                        case_id=case.case_id,
                        outputs=outputs,
                        artifacts=artifacts,
                        result=result,
                        message=message,
                        evidence_settings=evidence_settings,
                        secret_list=secret_list,
                        run_case=run_case,
                        run=run,
                        is_http=is_http,
                        request_id=request_id,
                        stage=_blocked_stage(result, message),
                    )
                    run_case.result = result  # type: ignore[assignment]
                    tier, tier_reason = compute_case_confidence(
                        case,
                        run_case,
                        effective_result=result,
                        probe_results=run.probe_results,
                    )
                    run_case.confidence_tier = tier
                    run_case.confidence_reason = redact(tier_reason, secret_list)
                    if case.layer == "L1" and result != "passed":
                        build_ok = False
                    if _is_build_case(case, build_command):
                        if result != "passed":
                            build_ok = False
                        if isinstance(run.l1, dict):
                            run.l1["build"] = {
                                "ok": result == "passed",
                                "exit_code": 0 if result == "passed" else 1,
                                "evidence_ref": refs[0] if refs else None,
                            }
                    run.current_case_id = ""
                    run.heartbeat_at = iso_now()
                    _save(run_path, run)
                else:
                    if cancelled():
                        _mark_cancelled(run)
                    else:
                        _downgrade_script_failures(plan, run)
                        run.status = "completed"
    except Exception as exc:  # noqa: BLE001 - record unexpected failure, do not crash silently
        run.status = "execution_error"
        run.current_case_id = ""
        (run_dir / "worker-error.txt").write_text(
            redact(str(exc), secret_list), encoding="utf-8"
        )
    finally:
        stop.set()
        beat.join(timeout=5.0)
        failed_any = any(case.result in {"failed", "blocked", "error"} for case in run.cases)
        for state in service_states:
            keep = _truthy(state["config"].get("keep_on_failure")) and failed_any
            if state["started"] and not keep:
                _stop_service(
                    state["config"],
                    workdir,
                    state["log"],
                    secret_list,
                    run_env,
                    cast(
                        subprocess.Popen[bytes] | None, state.get("process")
                    ),
                    run_dir=run_dir,
                    service_name=str(state.get("name") or ""),
                )
        if isolator_states:
            for isolator, state in reversed(isolator_states):
                isolator.cleanup(state)
        summary = token_ledger.summary()
        if summary.get("calls", 0) > 0:
            run.token_usage = summary
        run.heartbeat_at = iso_now()
        _save(run_path, run)
        _postprocess(ws, project, run_dir, run, token_ledger=token_ledger)
        token_ctx.__exit__(None, None, None)
    return 0



