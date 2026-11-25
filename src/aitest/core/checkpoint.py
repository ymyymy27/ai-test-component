"""用例级 Checkpoint 持久化与中断断点续跑模块。

支持在长运行中途保留已完成用例的 checkpoint，再次从同一计划与源码签名恢复时，
安全跳过已稳定通过的 L2/L3 独立用例并拷贝其历史证据，避免全量从头重跑。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from aitest.core.evidence import evidence_path
from aitest.errors import AiTestError
from aitest.models import Case, Plan, Run, RunCase
from aitest.security import ensure_within, redact_tree
from aitest.storage.files import read_json, read_model, write_json
from aitest.storage.workspace import iso_now


def checkpoint_path(run_dir: Path) -> Path:
    """返回本次运行断点文件路径。"""
    return run_dir / "checkpoint.json"


def save_checkpoint(
    run_dir: Path,
    plan: Plan,
    run: Run,
    secrets: list[str] | None = None,
) -> None:
    """原子落盘当前已完成用例的状态快照，用于后续故障恢复与断点续跑。"""
    secret_list = list(secrets or [])
    completed: dict[str, Any] = {}
    for run_case in run.cases:
        if run_case.result == "not_run":
            continue
        completed[run_case.case_id] = {
            "result": run_case.result,
            "confidence_tier": run_case.confidence_tier,
            "confidence_reason": run_case.confidence_reason,
            "service_log_issues": list(run_case.service_log_issues),
            "judgement": run_case.judgement,
            "evidence_refs": list(run_case.evidence_refs),
            "attempts": [att.model_dump(mode="json") for att in run_case.attempts],
        }
    payload = {
        "schema_version": 1,
        "run_id": run.run_id,
        "plan_id": plan.plan_id,
        "plan_revision": plan.revision,
        "source_signature": plan.source_signature,
        "updated_at": iso_now(),
        "completed": completed,
    }
    write_json(checkpoint_path(run_dir), redact_tree(payload, secret_list))


def load_resumable_cases(
    source_run_dir: Path,
    current_plan: Plan,
    selected_cases: list[Case],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """从来源运行校验兼容性并提取可安全复用的稳定通过用例与关联证据。"""
    run_file = source_run_dir / "run.json"
    plan_file = source_run_dir / "plan.json"
    if not run_file.is_file() or not plan_file.is_file():
        raise AiTestError(
            "RUN_STATE_CONFLICT",
            f"来源运行目录不完整，无法续跑：{source_run_dir.name}",
            "请确认 run_id 是否正确后重新发起完整运行",
        )
    source_run = read_model(run_file, Run)
    if source_run.status == "running":
        raise AiTestError(
            "RUN_STATE_CONFLICT",
            f"来源运行仍在执行中（{source_run.run_id}），不能基于运行中实例续跑",
            "等待其结束或先取消后再续跑",
        )
    source_plan = read_model(plan_file, Plan)
    if (
        source_plan.plan_id != current_plan.plan_id
        or source_plan.revision != current_plan.revision
        or source_plan.source_signature != current_plan.source_signature
    ):
        raise AiTestError(
            "PLAN_STALE",
            "来源运行的计划 ID、修订号或源码签名与当前不一致，不能断点续跑",
            "请重新发起完整执行以确保结果对应最新代码与计划",
        )

    completed_map: dict[str, dict[str, Any]] = {}
    ckpt_file = checkpoint_path(source_run_dir)
    if ckpt_file.is_file():
        try:
            ckpt = read_json(ckpt_file)
            raw_completed = ckpt.get("completed")
            if isinstance(raw_completed, dict):
                for cid, entry in raw_completed.items():
                    if isinstance(cid, str) and isinstance(entry, dict):
                        completed_map[cid] = entry
        except AiTestError:
            completed_map = {}

    if not completed_map:
        for rc in source_run.cases:
            if rc.result != "not_run":
                completed_map[rc.case_id] = {
                    "result": rc.result,
                    "confidence_tier": rc.confidence_tier,
                    "confidence_reason": rc.confidence_reason,
                    "service_log_issues": list(rc.service_log_issues),
                    "judgement": rc.judgement,
                    "evidence_refs": list(rc.evidence_refs),
                    "attempts": [att.model_dump(mode="json") for att in rc.attempts],
                }

    eligible_ids = {
        case.case_id
        for case in selected_cases
        if case.layer != "L1"
    }
    resumable: dict[str, dict[str, Any]] = {}
    refs: list[str] = []
    seen_refs: set[str] = set()
    for cid in eligible_ids:
        entry = completed_map.get(cid)
        if not entry:
            continue
        # 仅复用稳定通过的用例；失败、阻塞、人工待记或疑似抖动通过均需要重跑。
        if entry.get("result") != "passed" or entry.get("confidence_tier") == "suspected":
            continue
        resumable[cid] = entry
        for ref in entry.get("evidence_refs") or []:
            if isinstance(ref, str) and ref and ref not in seen_refs:
                seen_refs.add(ref)
                refs.append(ref)
    return resumable, refs


def copy_resumed_evidence(
    source_run_dir: Path,
    target_run_dir: Path,
    refs: list[str],
) -> list[str]:
    """将来源运行中的通过证据安全复制到新运行目录，保持历史记录不可变。"""
    copied: list[str] = []
    for ref in refs:
        src = evidence_path(source_run_dir, ref)
        if src is None or not src.is_file():
            continue
        rel_name = ref[len("evidence/") :] if ref.startswith("evidence/") else Path(ref).name
        try:
            dest_root = target_run_dir / "evidence"
            dest = ensure_within(dest_root, dest_root / rel_name)
        except ValueError:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(f"evidence/{dest.relative_to(dest_root).as_posix()}")
    return copied


def build_resumed_run_cases(
    *,
    filtered_cases: list[Case],
    resumable: dict[str, dict[str, Any]],
    source_run_id: str,
    is_manual_fn: Any,
) -> tuple[list[RunCase], list[str]]:
    """按计划切片生成新运行的 RunCase 列表并预填充断点复用用例。"""
    run_cases: list[RunCase] = []
    collected_refs: list[str] = []
    for case in filtered_cases:
        entry = resumable.get(case.case_id)
        if entry is None:
            run_cases.append(
                RunCase(
                    case_id=case.case_id,
                    result="manual_pending" if is_manual_fn(case) else "not_run",
                )
            )
            continue
        case_refs = [
            str(ref)
            for ref in (entry.get("evidence_refs") or [])
            if isinstance(ref, str) and ref
        ]
        for ref in case_refs:
            if ref not in collected_refs:
                collected_refs.append(ref)
        rc = RunCase.model_validate(
            {
                "case_id": case.case_id,
                "result": "passed",
                "attempts": entry.get("attempts") or [],
                "evidence_refs": case_refs,
                "confidence_tier": entry.get("confidence_tier"),
                "confidence_reason": str(entry.get("confidence_reason") or ""),
                "judgement": entry.get("judgement"),
                "service_log_issues": list(entry.get("service_log_issues") or []),
                "resumed_from_run_id": source_run_id,
            }
        )
        run_cases.append(rc)
    return run_cases, collected_refs
