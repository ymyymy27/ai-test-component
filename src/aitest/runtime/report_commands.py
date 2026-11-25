"""Runtime 命令模块：report_commands。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from aitest.commands import (
    Payload,
    _project,
    _require,
    _safe_id,
    _text,
)
from aitest.config import known_secret_values
from aitest.core.issues import ensure_run_issues
from aitest.core.reports import build_report
from aitest.errors import AiTestError, invalid_request
from aitest.model_config import load_project_config
from aitest.models import (
    Opinion,
    Report,
)
from aitest.runtime._common import _as_bool, _ensure_settled, _run
from aitest.security import redact
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now


def cmd_report_generate(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_dir, run = _run(ws, payload)
    _ensure_settled(run)
    ensure_run_issues(ws, project.project_id, run_dir)
    report = build_report(ws, project, run_dir)
    return {
        "report_id": report.report_id,
        "run_id": report.run_id,
        "generated_at": report.generated_at,
        "outcome": report.outcome,
        "conclusion": report.conclusion,
        "counts": report.counts,
        "coverage": report.coverage,
        "changed_modules": report.changed_modules,
        "unmapped_files": report.unmapped_files,
        "uncovered_items": report.uncovered_items,
        "unverified_items": report.unverified_items,
        "plan_warnings": report.plan_warnings,
        "layers": report.layers,
        "item_details": report.item_details,
        "l1": report.l1,
        "issue_count": report.issue_count,
        "failure_occurrences": report.failure_occurrences,
        "templates": [
            {
                "template_id": entry.get("template_id"),
                "name": entry.get("name"),
                "summary": entry.get("summary") or {},
            }
            for entry in report.templates
            if isinstance(entry, dict)
        ],
    }



def _report_path(ws: Workspace, project_id: str, report_id: str) -> Path:
    return ws.project_dir(project_id) / "reports" / f"{report_id}.json"



def cmd_report_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    report_id = _safe_id(_text({"id": _require(payload, "report_id")}, "id"), "报告 ID")
    path = _report_path(ws, project.project_id, report_id)
    if not path.is_file():
        raise invalid_request(f"报告不存在：{report_id}")
    report = read_model(path, Report)
    return {"report": report.model_dump(mode="json")}



def cmd_report_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_id = _text({"id": _require(payload, "run_id")}, "id")
    include_body = _as_bool(payload.get("include_body"), False)
    directory = ws.project_dir(project.project_id) / "reports"
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                report = read_model(path, Report)
            except AiTestError as exc:
                skipped.append(
                    {
                        "report_id": path.stem,
                        "code": exc.info.code,
                        "message": exc.info.message,
                    }
                )
                continue
            if report.run_id != run_id:
                continue
            generated_at = report.generated_at
            if not generated_at:
                try:
                    generated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                        timespec="seconds"
                    )
                except OSError:
                    generated_at = ""
            row: dict[str, Any] = {
                "report_id": report.report_id,
                "run_id": report.run_id,
                "generated_at": generated_at,
                "outcome": report.outcome,
                "revision": report.revision,
                "has_opinion": report.opinion is not None,
            }
            if include_body:
                row["report"] = report.model_dump(mode="json")
            rows.append(row)
    rows.sort(key=lambda item: item["generated_at"], reverse=True)
    return {"reports": rows, "skipped": skipped}



def _cell(value: object) -> str:
    text = str(value if value is not None else "").replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|") or "-"



def _case_row(detail: dict[str, Any]) -> str:
    refs = "、".join(_cell(item) for item in detail.get("evidence_refs") or []) or "-"
    return (
        f"| {_cell(detail.get('case_id'))} | {_cell(detail.get('acceptance_item_id') or '-')} "
        f"| {_cell(detail.get('layer'))} | {_cell(detail.get('result'))} | {refs} |"
    )



def _analysis_lines(analysis: dict[str, Any]) -> list[str]:
    status = str(analysis.get("status") or "not_generated")
    lines = [f"### 分析状态：{status}"]
    if status == "invalid":
        lines.append("分析不合格：模型输出缺少必需段落，整条不展示。")
        reason = analysis.get("reason")
        if reason:
            lines.append(f"原因：{reason}")
        return lines
    if status != "generated":
        lines.append("本轮未生成 AI 分析。")
        reason = analysis.get("reason")
        if reason:
            lines.append(f"原因：{reason}")
        return lines
    lines.append(f"**观察**：{analysis.get('observed') or '未记录'}")
    evidence = analysis.get("evidence_refs") or []
    lines.append(
        "**证据引用**：" + ("、".join(str(item) for item in evidence) if evidence else "无")
    )
    lines.append("**候选原因**：")
    causes = analysis.get("candidate_causes") or []
    if not causes:
        lines.append("- 无")
    else:
        for cause in causes:
            if isinstance(cause, dict):
                cause_text = cause.get("cause") or ""
                confidence = cause.get("confidence") or "unknown"
                lines.append(f"- {cause_text}（把握程度：{confidence}）")
            else:
                lines.append(f"- {cause}")
    lines.append("**尚未验证**：")
    unverified = analysis.get("unverified") or []
    if unverified:
        lines.extend(f"- {item}" for item in unverified)
    else:
        lines.append("- 无")
    lines.append("**下一步**：")
    next_steps = analysis.get("next_steps") or []
    if next_steps:
        lines.extend(f"- {item}" for item in next_steps)
    else:
        lines.append("- 无")
    return lines



def _export_lines(report: Report) -> list[str]:
    lines = [
        f"# 测试报告 {report.report_id}",
        "",
        "## 结论",
        report.conclusion or f"{report.outcome}：未生成结论摘要",
        "",
        f"- 结论值：{report.outcome}",
        f"- 运行：{report.run_id}",
        f"- 范围：{report.counts.get('selected', 0)}/{report.coverage.get('applicable', 0)}",
        f"- 未覆盖：{len(report.uncovered_items)}",
        f"- 失败：{report.counts.get('failed', 0)}",
        f"- 源码提交：{report.source_commit or '未记录'}",
        f"- 规则版本：{report.rules_version_id or '未记录'}",
    ]
    python_version = report.environment.get("python")
    lines.append(f"- 环境 Python：{python_version or '未记录'}")
    for key, label in (
        ("python_version", "Python 版本"),
        ("platform", "平台"),
        ("venv", "项目 venv"),
        ("node", "Node 版本"),
        ("node_path", "Node 路径"),
        ("node_lock", "Node 锁文件"),
        ("cwd", "工作目录"),
    ):
        value = report.environment.get(key)
        if value:
            lines.append(f"- 环境 {label}：{value}")
    services = report.environment.get("services")
    if isinstance(services, list) and services:
        names = [
            str(item.get("name") or "")
            for item in services
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        lines.append("- 服务：" + ("、".join(names) if names else "未配置"))
    else:
        lines.append("- 服务：未配置")
    if isinstance(report.github, dict):
        repo = report.github.get("repo") or "未记录"
        commit = report.github.get("commit") or "未记录"
        branch = report.github.get("branch") or "未记录"
        deployment = report.github.get("deployment") or "未确认"
        lines.append(f"- GitHub：{repo}@{commit}（{branch}）")
        lines.append(f"- 部署版本：{deployment}")
    lines.append("")
    if report.summary:
        if report.summary_source == "fallback":
            lines.append("AI 摘要未采用，以下为事实摘要：")
        else:
            lines.append("AI 摘要（仅事实）：")
        lines.extend(f"> {line}" if line else ">" for line in str(report.summary).splitlines())
        lines.append("")
    lines.append("## 范围与覆盖")
    lines.append(f"- 已选验收项：{report.counts.get('selected', 0)}")
    lines.append(f"- 适用验收项：{report.coverage.get('applicable', 0)}")
    lines.append(f"- 覆盖验收项：{report.counts.get('covered', 0)}")
    lines.append(f"- 通过：{report.counts.get('passed', 0)}")
    lines.append(f"- 失败：{report.counts.get('failed', 0)}")
    lines.append(f"- 阻塞或未完成：{report.counts.get('incomplete', 0)}")
    lines.append("")
    lines.append("## 验收项主表")
    lines.append("| 验收项 | 内容 | 模块 | 结果 | 用例 |")
    lines.append("| --- | --- | --- | --- | --- |")
    if report.item_details:
        for item in report.item_details:
            lines.append(
                f"| {_cell(item.get('item_id'))} | {_cell(item.get('text'))} "
                f"| {_cell(item.get('module_id') or '-')} | {_cell(item.get('result'))} "
                f"| {_cell('、'.join(item.get('case_ids') or []) or '-')} |"
            )
    else:
        lines.append("| 无 | - | - | - | - |")
    lines.append("")
    lines.append("## 未覆盖清单")
    if report.uncovered_items:
        lines.extend(f"- {item}" for item in report.uncovered_items)
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 未验证项")
    if report.unverified_items:
        lines.extend(f"- {item}" for item in report.unverified_items)
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 分层计数")
    for layer in ("L1", "L2", "L3"):
        lines.append(f"- {layer}：{report.layers.get(layer, 0)} 个用例")
    lines.append("")
    lines.append("## 项目级检查（L1，不计验收项覆盖）")
    if report.l1 is None:
        lines.append("- 未配置项目级检查")
    else:
        build = report.l1.get("build") if isinstance(report.l1.get("build"), dict) else None
        if build is None:
            lines.append("- 构建：未配置")
        else:
            lines.append(
                f"- 构建：{'通过' if build.get('ok') else '失败'}"
                f"（退出码 {build.get('exit_code')}，证据 {build.get('evidence_ref') or '无'}）"
            )
        for entry in report.l1.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            lines.append(
                f"- 入口 {entry.get('module')}："
                f"{'通过' if entry.get('ok') else '失败'}"
                f"（证据 {entry.get('evidence_ref') or '无'}）"
            )
        for case in report.l1.get("cases") or []:
            if not isinstance(case, dict):
                continue
            lines.append(
                f"- 检查 {_cell(case.get('case_id'))}：{_cell(case.get('result'))}"
            )
        for reason in report.l1.get("blocking_reasons") or []:
            lines.append(f"- 阻塞原因：{reason}")
    lines.append("")
    lines.append("## 用例明细")
    lines.append("| 用例 | 验收项 | 分层 | 结果 | 证据 |")
    lines.append("| --- | --- | --- | --- | --- |")
    if report.case_details:
        lines.extend(_case_row(detail) for detail in report.case_details)
    else:
        lines.append("| 无 | - | - | - | - |")
    lines.append("")
    lines.append("## 失败证据")
    failed = [item for item in report.case_details if item.get("result") == "failed"]
    if not failed:
        lines.append("- 无失败用例")
    else:
        for detail in failed:
            refs = detail.get("evidence_refs") or []
            rendered = "、".join(_cell(item) for item in refs) or "未采集证据"
            lines.append(f"- {_cell(detail.get('case_id'))}：{rendered}")
    lines.append("")
    lines.append("## 涉及模块")
    lines.append("- 已映射模块：" + ("、".join(report.changed_modules) or "无"))
    lines.append("- 未映射文件：" + ("、".join(report.unmapped_files) or "无"))
    lines.append("")
    lines.append("## AI 分析")
    if not report.analysis:
        lines.append("- 未生成")
    else:
        for analysis in report.analysis:
            lines.extend(_analysis_lines(analysis))
            lines.append("")
    lines.append("")
    lines.append("## 问题")
    lines.append(
        f"- 问题数：{report.issue_count}；本运行失败次数：{report.failure_occurrences}"
    )
    if report.issues:
        for issue in report.issues:
            lines.append(
                f"- {_cell(issue.get('issue_id'))} [{_cell(issue.get('severity'))}"
                f"/{_cell(issue.get('status'))}] {_cell(issue.get('title'))}"
                f"（发生 {issue.get('occurrences', 0)} 次，本运行 "
                f"{issue.get('run_occurrences', 0)} 次）"
            )
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## Mock 判定")
    if report.mock_judgement:
        for item in report.mock_judgement:
            lines.append(
                f"- {_cell(item.get('name'))}：{_cell(item.get('kind'))}"
                f"（{_cell(item.get('reason'))}）"
            )
    else:
        lines.append("- 未判定")
    lines.append("")
    lines.append("## 模板处置")
    if not report.templates:
        lines.append("- 未应用模板")
    else:
        for entry in report.templates:
            if not isinstance(entry, dict):
                continue
            raw_summary = entry.get("summary")
            summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
            lines.append(
                f"- {entry.get('name') or entry.get('template_id') or '模板'}"
                f"（适用 {summary.get('applicable', 0)}，"
                f"不适用 {summary.get('not_applicable', 0)}，"
                f"不支持 {summary.get('unsupported', 0)}，"
                f"未知 {summary.get('unknown', 0)}）"
            )
            raw_items = entry.get("items")
            items: list[Any] = raw_items if isinstance(raw_items, list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                label = item.get("objective") or item.get("item_id") or ""
                reason = item.get("reason") or "未提供理由"
                lines.append(f"  - {label}：{item.get('disposition', 'unknown')}（{reason}）")
    lines.append("")
    lines.append("## 验收意见")
    if report.opinion is None:
        lines.append("未填写验收意见")
    else:
        lines.append(
            f"验收意见（{report.opinion.by}，{report.opinion.at}）：{report.opinion.text}"
        )
    lines.append("")
    lines.append("## 证据索引")
    lines.append("| 引用 | 存在 | 字节 | 媒体类型 | sha256 |")
    lines.append("| --- | --- | --- | --- | --- |")
    if report.evidence_index:
        for item in report.evidence_index:
            exists = "是" if item.get("exists") else "否"
            lines.append(
                f"| {_cell(item.get('ref'))} | {exists} | {item.get('bytes', 0)} "
                f"| {_cell(item.get('media_type'))} | {_cell(item.get('sha256'))} |"
            )
    else:
        lines.append("| 无 | - | 0 | - | - |")
    lines.append("")
    return lines



def cmd_report_export(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    report_id = _safe_id(
        _text({"id": _require(payload, "report_id")}, "id"), "报告 ID"
    )
    path = _report_path(ws, project.project_id, report_id)
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"报告不存在：{report_id}", "先生成报告")
    report = read_model(path, Report)
    target = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(_export_lines(report))
    secrets = known_secret_values([load_project_config(ws, project.project_id)])
    target.write_text(redact(text, secrets), encoding="utf-8")
    data = target.read_bytes()
    return {
        "path": str(target),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "lines": text.count("\n") + 1,
    }



def cmd_report_opinion(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    report_id = _safe_id(
        _text({"id": _require(payload, "report_id")}, "id"), "报告 ID"
    )
    path = _report_path(ws, project.project_id, report_id)
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"报告不存在：{report_id}", "先生成报告")
    report = read_model(path, Report)
    secrets = known_secret_values([load_project_config(ws, project.project_id)])
    report.opinion = Opinion(
        by=redact(_text({"by": _require(payload, "by")}, "by"), secrets),
        at=iso_now(),
        text=redact(_text(payload, "text"), secrets),
    )
    report.revision += 1
    write_model(path, report)
    return {"opinion": report.opinion.model_dump(mode="json")}



