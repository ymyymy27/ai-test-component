"""交付说明命令处理器。"""

from __future__ import annotations

from typing import Any

from aitest.command_handlers._common import (
    Payload,
    _project,
    _project_secrets,
    _task,
    _text,
    cmd_facade_attr,
)
from aitest.core.delivery import build_context as build_delivery_context
from aitest.model_config import build_client
from aitest.models import Delivery, new_id
from aitest.security import redact_tree
from aitest.storage.files import read_model, write_model
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
