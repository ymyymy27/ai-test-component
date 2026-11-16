"""Command handlers for the lightweight CLI. Each handler validates input and calls storage."""

from __future__ import annotations

import os
import platform
import secrets
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aitest import __version__
from aitest.config import model_key_source
from aitest.errors import AiTestError, invalid_path, invalid_request
from aitest.model_config import build_client
from aitest.models import (
    AcceptanceItem,
    Delivery,
    Dependency,
    Module,
    Project,
    RuleVersion,
    Task,
    new_id,
)
from aitest.storage.files import read_json, read_model, write_json, write_model
from aitest.storage.workspace import Workspace

Payload = dict[str, Any]
Handler = Callable[[Workspace, Payload, bool], dict[str, Any]]


def _require(payload: Payload, key: str) -> Any:
    value = payload.get(key)
    if value in (None, "", [], {}):
        raise invalid_request(f"缺少字段：{key}")
    return value


def _text(payload: Payload, key: str, default: str = "") -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise invalid_request(f"字段必须是字符串：{key}")
    return value


def _project(ws: Workspace, payload: Payload) -> Project:
    return ws.load_project(_text({"project_id": _require(payload, "project_id")}, "project_id"))


def _task_path(ws: Workspace, project_id: str, task_id: str) -> Path:
    return ws.project_dir(project_id) / "tasks" / f"{task_id}.json"


def _project_dir_for_task(ws: Workspace, task: Task) -> Path:
    return ws.project_dir(task.project_id)


# -- doctor -----------------------------------------------------------------


def cmd_doctor(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    meta = ws.init()
    writable = os.access(ws.root, os.W_OK)
    python_ok = sys.version_info >= (3, 11)
    gh_path = shutil.which("gh")
    repairs: list[dict[str, Any]] = []
    if not writable:
        repairs.append({"risk": "high", "command": "修正工作空间权限", "auto": False})
    if gh_path is None:
        repairs.append(
            {
                "risk": "high",
                "command": "winget install --id GitHub.cli",
                "auto": False,
            }
        )
    if model_key_source() == "none":
        repairs.append(
            {
                "risk": "high",
                "command": "设置 AITEST_MODEL_KEY 或在用户配置写入 model.api_key",
                "auto": False,
            }
        )
    done: list[str] = []
    if bool(payload.get("repair")):
        for name in ("projects", "requests", "backups"):
            directory = ws.root / name
            directory.mkdir(parents=True, exist_ok=True)
            done.append(f"ensure:{name}")
        probe = ws.root / "requests" / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        done.append("write-probe")
    facts: dict[str, Any] = {
        "workspace": {"ok": writable, "path": str(ws.root), "workspace_id": meta.workspace_id},
        "python": {"ok": python_ok, "version": platform.python_version()},
        "package": {"ok": True, "version": __version__},
        "model": {"ok": model_key_source() != "none", "source": model_key_source()},
        "github": {"ok": gh_path is not None, "path": gh_path},
        "proxy": {"ok": True},
        "repairs": repairs,
        "repairs_done": done,
    }
    if not all(
        bool(facts[key]["ok"]) for key in ("workspace", "python", "package", "model", "github")
    ) or bool(payload.get("explain")):
        projects = ws.list_projects()
        client = build_client(ws, projects[0].project_id) if projects else None
        if client is not None:
            try:
                explanation = client.explain_environment(facts)
            except AiTestError:
                explanation = None
            if explanation is not None:
                facts["explanation"] = explanation
    return facts


# -- project ----------------------------------------------------------------


def cmd_project_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    root = Path(_text({"root": _require(payload, "root")}, "root")).expanduser().resolve()
    if not root.is_dir():
        raise invalid_path(f"目录不存在：{root}")
    if not os.access(root, os.W_OK):
        raise invalid_path(f"目录不可写：{root}")
    existing = ws.find_by_root(str(root))
    if existing is not None:
        return {"project_id": existing.project_id, "revision": existing.revision, "existing": True}
    overlap = ws.find_overlapping(str(root))
    if overlap:
        raise invalid_request(
            "根目录与已登记项目重叠：" + "、".join(item.project_id for item in overlap),
            "明确选择要登记的项目根目录",
        )
    project = Project(
        project_id=new_id("proj"),
        name=_text(payload, "name") or root.name,
        root=str(root),
        goal=_text(payload, "goal"),
    )
    ws.save_project(project)
    return {"project_id": project.project_id, "revision": project.revision, "existing": False}


def cmd_project_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del payload, confirm
    return {
        "projects": [
            {"project_id": p.project_id, "name": p.name, "root": p.root, "revision": p.revision}
            for p in ws.list_projects()
        ]
    }


def cmd_project_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    return {"project": project.model_dump(mode="json")}


def cmd_project_update(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    expected = payload.get("expected_revision")
    if expected is not None and expected != project.revision:
        raise AiTestError("RUN_STATE_CONFLICT", "项目修订号不匹配", "刷新后重试")
    patch = payload.get("patch")
    if not isinstance(patch, dict):
        raise invalid_request("patch 必须是对象")
    data = project.model_dump(mode="json")
    for key in ("name", "goal"):
        if key in patch:
            data[key] = patch[key]
    project = Project.model_validate(data)
    project.revision += 1
    ws.save_project(project)
    return {"project_id": project.project_id, "revision": project.revision}


# -- module -----------------------------------------------------------------


def cmd_module_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    deps = [
        Dependency.model_validate(item)
        for item in payload.get("deps", [])
        if isinstance(item, dict)
    ]
    module = Module(
        name=_text({"name": _require(payload, "name")}, "name"),
        responsibility=_text(payload, "responsibility"),
        owner=_text(payload, "owner"),
        status=_text(payload, "status", "开发中"),
        deps=deps,
    )
    project.modules.append(module)
    project.revision += 1
    ws.save_project(project)
    return {"module_id": module.module_id, "revision": project.revision}


def cmd_module_update(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    module_id = _text({"module_id": _require(payload, "module_id")}, "module_id")
    for index, module in enumerate(project.modules):
        if module.module_id == module_id:
            data = module.model_dump(mode="json")
            patch = payload.get("patch", {})
            if not isinstance(patch, dict):
                raise invalid_request("patch 必须是对象")
            data.update({k: v for k, v in patch.items() if k in data})
            project.modules[index] = Module.model_validate(data)
            project.revision += 1
            ws.save_project(project)
            return {"module_id": module_id, "revision": project.revision}
    raise AiTestError("INVALID_REQUEST", f"模块不存在：{module_id}", "检查模块 ID")


def cmd_module_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    return {"modules": [m.model_dump(mode="json") for m in project.modules]}


# -- task -------------------------------------------------------------------


def cmd_task_suggest(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    fallback = "验证项目能否启动并跑通主流程"
    client = build_client(ws, project.project_id)
    if client is None:
        return {"candidates": [], "fallback": fallback, "ai_generated": False}
    modules = "、".join(module.name for module in project.modules) or "未登记模块"
    context = f"项目：{project.name}\n目标：{project.goal}\n模块：{modules}"
    try:
        candidates = client.suggest_targets(context)
    except AiTestError:
        return {"candidates": [], "fallback": fallback, "ai_generated": False}
    return {
        "candidates": candidates,
        "fallback": fallback,
        "ai_generated": bool(candidates),
    }


def cmd_task_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    raw_items = payload.get("acceptance_items", [])
    if not isinstance(raw_items, list):
        raise invalid_request("acceptance_items 必须是数组")
    if not raw_items:
        requirement = _text(payload, "requirement")
        client = build_client(ws, project.project_id)
        if requirement and client is not None:
            try:
                raw_items = client.extract_acceptance_items(requirement)
            except AiTestError:
                raw_items = []
    items: list[AcceptanceItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise invalid_request("验收项必须是对象")
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        observable = bool(raw.get("observable", True))
        items.append(
            AcceptanceItem(
                text=text,
                observable=observable,
                status="active" if observable else "pending",
                source=str(raw.get("source", "")),
            )
        )
    task = Task(
        task_id=new_id("task"),
        project_id=project.project_id,
        title=_text({"title": _require(payload, "title")}, "title"),
        goal=_text(payload, "goal"),
        scope=_text(payload, "scope"),
        inputs=_text(payload, "inputs"),
        outputs=_text(payload, "outputs"),
        preconditions=[str(x) for x in payload.get("preconditions", [])],
        executor=_text(payload, "executor"),
        reviewer=_text(payload, "reviewer"),
        acceptance_items=items,
    )
    write_model(_task_path(ws, project.project_id, task.task_id), task)
    return {
        "task_id": task.task_id,
        "revision": task.revision,
        "acceptance_items": [
            {"item_id": i.item_id, "status": i.status, "text": i.text}
            for i in task.acceptance_items
        ],
    }


def _task(ws: Workspace, payload: Payload) -> Task:
    project = _project(ws, payload)
    task_id = _text({"task_id": _require(payload, "task_id")}, "task_id")
    path = _task_path(ws, project.project_id, task_id)
    if not path.is_file():
        raise AiTestError("TASK_NOT_FOUND", f"任务不存在：{task_id}", "先创建任务")
    return read_model(path, Task)


def cmd_task_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    directory = ws.project_dir(project.project_id) / "tasks"
    tasks: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            task = read_model(path, Task)
            tasks.append({"task_id": task.task_id, "title": task.title, "revision": task.revision})
    return {"tasks": tasks}


def cmd_task_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    return {"task": _task(ws, payload).model_dump(mode="json")}


# -- delivery ---------------------------------------------------------------


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


# -- rules ------------------------------------------------------------------


def cmd_rules_save(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    rule = RuleVersion(
        rules_version_id=new_id("rule"),
        project_id=project.project_id,
        name=_text({"name": _require(payload, "name")}, "name"),
        version=str(_require(payload, "version")),
        steps_md=_text(payload, "steps_md"),
        ai_rules=[str(x) for x in payload.get("ai_rules", [])],
        evidence_requirements=[str(x) for x in payload.get("evidence_requirements", [])],
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
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            rule = read_model(path, RuleVersion)
            rules.append(
                {
                    "rules_version_id": rule.rules_version_id,
                    "name": rule.name,
                    "version": rule.version,
                }
            )
    return {"rules": rules}


def _rule(ws: Workspace, project: Project, rules_version_id: str) -> RuleVersion:
    path = ws.project_dir(project.project_id) / "rules" / f"{rules_version_id}.json"
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
    rule = RuleVersion(
        rules_version_id=new_id("rule"),
        project_id=project.project_id,
        name=_text({"name": _require(payload, "name")}, "name"),
        version=str(_require(payload, "version")),
        steps_md=source.read_text(encoding="utf-8"),
    )
    write_model(
        ws.project_dir(project.project_id) / "rules" / f"{rule.rules_version_id}.json", rule
    )
    return {"rules_version_id": rule.rules_version_id, "version": rule.version}


REGISTRY: dict[str, Handler] = {
    "doctor": cmd_doctor,
    "project.add": cmd_project_add,
    "project.list": cmd_project_list,
    "project.show": cmd_project_show,
    "project.update": cmd_project_update,
    "module.add": cmd_module_add,
    "module.update": cmd_module_update,
    "module.list": cmd_module_list,
    "task.suggest": cmd_task_suggest,
    "task.add": cmd_task_add,
    "task.list": cmd_task_list,
    "task.show": cmd_task_show,
    "delivery.save": cmd_delivery_save,
    "delivery.show": cmd_delivery_show,
    "rules.save": cmd_rules_save,
    "rules.list": cmd_rules_list,
    "rules.show": cmd_rules_show,
    "rules.bind": cmd_rules_bind,
    "rules.import": cmd_rules_import,
    "rules.export": cmd_rules_export,
}

MUTATING = {
    "project.add",
    "project.update",
    "module.add",
    "module.update",
    "task.add",
    "delivery.save",
    "rules.save",
    "rules.bind",
    "rules.import",
    "rules.export",
}


def request_cache_path(ws: Workspace, request_id: str) -> Path:
    safe = "".join(ch for ch in request_id if ch.isalnum() or ch in "-_")[:80]
    if not safe:
        safe = secrets.token_hex(8)
    return ws.root / "requests" / f"{safe}.json"


def cached_result(ws: Workspace, request_id: str) -> dict[str, Any] | None:
    path = request_cache_path(ws, request_id)
    if not path.is_file():
        return None
    return read_json(path)


def store_result(ws: Workspace, request_id: str, result: dict[str, Any]) -> None:
    write_json(request_cache_path(ws, request_id), result)
