"""交付说明、规则预设与模板应用命令处理器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aitest.command_handlers._common import (
    Payload,
    _project,
    _project_secrets,
    _require,
    _safe_id,
    _task,
    _text,
    cmd_facade_attr,
)
from aitest.core.delivery import build_context as build_delivery_context
from aitest.core.templates import apply_template, list_templates, load_template
from aitest.errors import AiTestError, invalid_path, invalid_request
from aitest.model_config import build_client
from aitest.models import Delivery, Plan, Project, RuleVersion, new_id
from aitest.security import redact, redact_tree
from aitest.storage.files import read_model, write_json, write_model
from aitest.storage.workspace import Workspace


def cmd_delivery_save(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    task = _task(ws, payload)
    delivery = Delivery(
        delivery_id=new_id("dlv"),
        task_id=task.task_id,
        done=[str(x) for x in payload.get("done", [])],
        not_done=[str(x) for x in payload.get("not_done", [])],
        changed_modules=[str(x) for x in payload.get("changed_modules", [])],
        interface_changes=[str(x) for x in payload.get("interface_changes", [])],
        run_commands=[str(x) for x in payload.get("run_commands", [])],
        test_data=_text(payload, "test_data"),
        dependencies=[str(x) for x in payload.get("dependencies", [])],
        mocks=[str(x) for x in payload.get("mocks", [])],
        self_test_evidence=[str(x) for x in payload.get("self_test_evidence", [])],
        commit=_text(payload, "commit"),
    )
    delivery.complete = bool(delivery.run_commands and delivery.commit)
    secrets = _project_secrets(ws, task.project_id)
    delivery = Delivery.model_validate(
        redact_tree(delivery.model_dump(mode="json"), secrets)
    )
    path = ws.project_dir(task.project_id) / "deliveries" / f"{task.task_id}.json"
    write_model(path, delivery)
    return {
        "delivery_id": delivery.delivery_id,
        "revision": delivery.revision,
        "complete": delivery.complete,
    }


def cmd_delivery_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    task = _task(ws, payload)
    path = ws.project_dir(task.project_id) / "deliveries" / f"{task.task_id}.json"
    if not path.is_file():
        return {"delivery": None}
    return {"delivery": read_model(path, Delivery).model_dump(mode="json")}


def cmd_delivery_draft(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Draft a delivery note from read-only project facts; never persist it."""
    del confirm
    project = _project(ws, payload)
    task = _task(ws, payload)
    commit = _text(payload, "commit").strip()
    client = cmd_facade_attr("build_client", build_client)(ws, project.project_id)
    if client is None:
        return {
            "draft": None,
            "sources": {"commit": commit},
            "status": "not_generated",
            "reason": "model_missing",
            "message": "未配置模型密钥，交付说明未生成；可手工填写后 delivery.save",
        }
    context = build_delivery_context(project, task, commit)
    try:
        draft = client.draft_delivery(context)
    except Exception:
        return {
            "draft": None,
            "sources": {"commit": commit},
            "status": "not_generated",
            "reason": "model_error",
            "message": "模型调用失败，交付说明未生成；可手工填写后 delivery.save",
        }
    files = context["files"]
    return {
        "draft": draft,
        "sources": {
            "readme": files["readme"]["path"],
            "manifest": files["manifest"]["path"],
            "commit_stat": bool(files["commit_stat"]),
        },
    }


def _task_templates_dir(ws: Workspace, project_id: str, task_id: str) -> Path:
    return ws.project_dir(project_id) / "tasks" / task_id / "templates"


def cmd_template_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del ws, payload, confirm
    return {"templates": list_templates()}


def cmd_template_apply(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Dispose template items with the model and store the result as advice only."""
    del confirm
    project = _project(ws, payload)
    task = _task(ws, payload)
    name = _text({"template_name": _require(payload, "template_name")}, "template_name")
    plan_id = _text(payload, "plan_id").strip()
    plan_path: Path | None = None
    plan: Plan | None = None
    if plan_id:
        safe_plan_id = _safe_id(plan_id, "plan_id")
        plan_path = ws.project_dir(project.project_id) / "plans" / f"{safe_plan_id}.json"
        if not plan_path.is_file():
            raise AiTestError("INVALID_REQUEST", f"计划不存在：{plan_id}", "先 plan.generate")
        plan = read_model(plan_path, Plan)
        if plan.task_id != task.task_id:
            raise invalid_request("计划不属于该任务", "改用同一任务下的计划 ID")
    template = load_template(name)
    client = cmd_facade_attr("build_client", build_client)(ws, project.project_id)
    if client is None:
        raise AiTestError(
            "MODEL_UNAVAILABLE",
            "未配置模型密钥，无法处置模板",
            "设置 AITEST_MODEL_KEY 或在用户配置写入 model.api_key",
        )
    result = apply_template(client, template, project, task)
    secrets = _project_secrets(ws, project.project_id)
    result = redact_tree(result, secrets)
    target = _task_templates_dir(ws, project.project_id, task.task_id) / (
        f"{result['template_id']}.json"
    )
    write_json(target, {"schema_version": 1, **result})
    revision = None
    if plan is not None and plan_path is not None:
        kept = [
            entry
            for entry in plan.templates
            if entry.get("template_id") != result["template_id"]
        ]
        plan.templates = [*kept, result]
        plan.revision += 1
        write_model(plan_path, plan)
        revision = plan.revision
    return {
        "template": result,
        "path": str(target.relative_to(ws.root)).replace("\\", "/"),
        "plan_id": plan_id,
        "plan_revision": revision,
    }


def cmd_rules_save(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    secrets = _project_secrets(ws, project.project_id)
    rule = RuleVersion(
        rules_version_id=new_id("rule"),
        project_id=project.project_id,
        name=redact(_text({"name": _require(payload, "name")}, "name"), secrets),
        version=str(_require(payload, "version")),
        steps_md=redact(_text(payload, "steps_md"), secrets),
        ai_rules=[redact(str(x), secrets) for x in payload.get("ai_rules", [])],
        evidence_requirements=[
            redact(str(x), secrets) for x in payload.get("evidence_requirements", [])
        ],
    )
    write_model(
        ws.project_dir(project.project_id) / "rules" / f"{rule.rules_version_id}.json", rule
    )
    return {"rules_version_id": rule.rules_version_id, "version": rule.version}


def cmd_rules_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    directory = ws.project_dir(project.project_id) / "rules"
    rules: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                rule = read_model(path, RuleVersion)
            except AiTestError as exc:
                skipped.append(
                    {
                        "rules_version_id": path.stem,
                        "code": exc.info.code,
                        "message": exc.info.message,
                    }
                )
                continue
            rules.append(
                {
                    "rules_version_id": rule.rules_version_id,
                    "name": rule.name,
                    "version": rule.version,
                }
            )
    return {"rules": rules, "skipped": skipped}


def _rule(ws: Workspace, project: Project, rules_version_id: str) -> RuleVersion:
    safe_rule_id = _safe_id(rules_version_id, "rules_version_id")
    path = ws.project_dir(project.project_id) / "rules" / f"{safe_rule_id}.json"
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"规则版本不存在：{rules_version_id}", "先保存规则")
    return read_model(path, RuleVersion)


def cmd_rules_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    rule = _rule(ws, project, _text({"id": _require(payload, "rules_version_id")}, "id"))
    return {"rule": rule.model_dump(mode="json")}


def cmd_rules_bind(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    rule = _rule(ws, project, _text({"id": _require(payload, "rules_version_id")}, "id"))
    project.rules_version_id = rule.rules_version_id
    project.revision += 1
    ws.save_project(project)
    return {"project_id": project.project_id, "rules_version_id": rule.rules_version_id}


def cmd_rules_export(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    rule = _rule(ws, project, _text({"id": _require(payload, "rules_version_id")}, "id"))
    target = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rule.steps_md or f"# {rule.name}\n", encoding="utf-8")
    return {"path": str(target), "bytes": target.stat().st_size}


def cmd_rules_import(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    source = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    if not source.is_file():
        raise invalid_path(f"文件不存在：{source}")
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise invalid_path(f"规则文件无法读取：{source}（{exc}）") from exc
    if len(content) > 1024 * 1024:
        raise invalid_request("规则文件超过 1 MiB 上限", "拆分或精简规则文件")
    try:
        steps_md = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise invalid_request("规则文件必须是 UTF-8 文本", "转换编码后重试") from exc
    secrets = _project_secrets(ws, project.project_id)
    rule = RuleVersion(
        rules_version_id=new_id("rule"),
        project_id=project.project_id,
        name=redact(_text({"name": _require(payload, "name")}, "name"), secrets),
        version=str(_require(payload, "version")),
        steps_md=redact(steps_md, secrets),
    )
    write_model(
        ws.project_dir(project.project_id) / "rules" / f"{rule.rules_version_id}.json", rule
    )
    return {"rules_version_id": rule.rules_version_id, "version": rule.version}
