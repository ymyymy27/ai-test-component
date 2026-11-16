"""Core-owned Windows gesture adapter; callers can request a prompt, never grant it."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from aitest.application.approval_service import _digest
from aitest.application.ports import ApprovalRecords
from aitest.contracts.commands import CHALLENGE_ACTIONS, Command
from aitest.contracts.responses import Response
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session

PANEL_CONFIRMATION_VERSION = "panel-confirmation/2"
PANEL_CONFIRMATION_TIMEOUT_MS = 120_000
MAX_PROMPT_CHARS = 128_000

#: Prepared panel challenges keep their interactive session alive between the
#: prepare and confirm control frames.  A separate CLI process cannot reuse a
#: session that was already finalized, so the core owns this short-lived map.
_PANEL_SESSIONS: dict[tuple[str, str], tuple[Session, threading.Timer, str]] = {}
_PANEL_SESSIONS_LOCK = threading.Lock()


def _expire_panel_session(api: LocalAPI, key: tuple[str, str]) -> None:
    with _PANEL_SESSIONS_LOCK:
        entry = _PANEL_SESSIONS.pop(key, None)
    if entry is not None:
        api.close_session(entry[0])


def _remember_panel_session(
    api: LocalAPI, key: tuple[str, str], session: Session, expires_at: str
) -> None:
    timer = threading.Timer(
        PANEL_CONFIRMATION_TIMEOUT_MS / 1000.0,
        _expire_panel_session,
        args=(api, key),
    )
    timer.daemon = True
    with _PANEL_SESSIONS_LOCK:
        old = _PANEL_SESSIONS.pop(key, None)
        _PANEL_SESSIONS[key] = (session, timer, expires_at)
    if old is not None:
        old[1].cancel()
    timer.start()

_ACTION_LABELS = {
    "save_context": "保存项目名称与测试目标",
    "save_binding": "绑定被测项目目录",
    "save_environment": "保存测试环境与数据复位说明",
    "save_model_outbound_policy": "确认模型接收方与允许发送的材料",
    "generate_draft": "请求模型准备测试建议",
    "save_case": "保存待核对的测试用例",
    "confirm_basis": "确认本用例的独立预期结果",
    "save_acceptance": "保存本次必测范围",
    "publish_plan": "发布核对后的测试计划",
    "analyze_project": "保存本次被测源码快照",
    "prepare_run": "冻结本次测试的计划、环境和来源",
    "register_run": "创建本次测试运行",
    "prepare_execution": "准备并查看本步骤将执行的操作",
    "authorize_step": "授权本步骤的准确操作",
    "execute_step": "执行已授权的测试步骤",
    "verify_pending": "依据已保存证据核验独立预期",
    "create_report": "保存本次测试报告",
    "export_report": "导出已保存的报告修订",
}


@dataclass(frozen=True, slots=True)
class ConfirmationPrompt:
    """What the human confirms: readable facts first, exact input on demand.

    ``details`` keeps the frozen exact material, so the gesture still binds to
    something verifiable; it is simply no longer the first thing shown.
    """

    title: str
    note: str
    facts: tuple[tuple[str, str], ...]
    details: str


_HUMAN_FIELDS: Final = {
    "binding": (("canonical_path", "被测目录"),),
    "case": (("objective", "检查目标"), ("expected", "独立预期")),
}

_ACTION_IMPACT = {
    "execute_step": "会在本机执行被测程序，可能产生副作用。",
    "save_binding": "会把本项目绑定到下面列出的实际目录。",
    "publish_plan": "发布后计划冻结，后续修改会产生新的修订。",
    "analyze_project": "会保存被测目录的源码快照。",
    "confirm_basis": "确认后本用例的独立预期冻结，不能用运行结果改写。",
    "authorize_step": "授权只对本次列出的准确操作有效。",
    "export_report": "会把报告与证据导出到本机目录。",
    "verify_pending": "只依据已保存证据核验，不会重新执行程序。",
}

_DEFAULT_IMPACT = "仅在理解下面内容后确认；取消不会提交任何更改。"


def _plain(value: object) -> str:
    """Render one JSON value without code punctuation."""
    if value is True:
        return "是"
    if value is False:
        return "否"
    if value is None:
        return "（空）"
    return str(value)


def _readable(value: object) -> str:
    if isinstance(value, Mapping):
        return "；".join(f"{key} = {_plain(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return "、".join(_readable(item) for item in value)
    return _plain(value)


def _short_project(command: Mapping[str, object]) -> str:
    text = str(command.get("project_id") or "")
    return "…" + text[-8:] if len(text) > 8 else (text or "未知")


def _confirmation_prompt(projected: Mapping[str, object]) -> ConfirmationPrompt:
    """Build the human view from the same redacted projection, adding no new facts."""
    command = projected.get("command")
    if not isinstance(command, Mapping):
        command = {}
    action = str(command.get("action", ""))
    parameters = command.get("parameters")
    if not isinstance(parameters, Mapping):
        parameters = {}
    facts: list[tuple[str, str]] = []
    project = parameters.get("project")
    if isinstance(project, Mapping):
        for field, label in (("name", "项目名称"), ("goal", "测试目标")):
            if field in project:
                facts.append((label, _readable(project[field])))
    if not facts:
        facts.append(("项目", _short_project(command)))
    for key, fields in _HUMAN_FIELDS.items():
        material = parameters.get(key)
        if not isinstance(material, Mapping):
            continue
        for field, label in fields:
            if field in material:
                facts.append((label, _readable(material[field])))
    if action in {"prepare_execution", "authorize_step", "execute_step"}:
        materials = projected.get("saved_materials")
        if isinstance(materials, list):
            for material in materials:
                if not isinstance(material, Mapping):
                    continue
                reference = material.get("reference")
                payload = material.get("payload")
                if (
                    not isinstance(reference, Mapping)
                    or reference.get("aggregate_kind") != "execution_authorization"
                    or not isinstance(payload, Mapping)
                ):
                    continue
                resolved = payload.get("action")
                request = resolved.get("request") if isinstance(resolved, Mapping) else None
                if not isinstance(request, Mapping):
                    continue
                for field, label in (
                    ("step_id", "步骤"),
                    ("side_effect_class", "副作用"),
                ):
                    if field in request:
                        facts.append((label, _readable(request[field])))
                entry = request.get("registered_entry")
                if isinstance(entry, Mapping):
                    if "entrypoint" in entry:
                        facts.append(("入口", _readable(entry["entrypoint"])))
                    if "arguments" in entry:
                        facts.append(("参数", _readable(entry["arguments"])))
                if "timeout_ms" in request:
                    facts.append(("超时毫秒", _readable(request["timeout_ms"])))
                digest = request.get("resolved_input_digest")
                if isinstance(digest, str) and digest:
                    facts.append(
                        ("输入摘要", digest if len(digest) <= 18 else digest[:18] + "…")
                    )
                break
    details = json.dumps(projected, ensure_ascii=False, indent=2, allow_nan=False).translate(
        {
            code: f"\\u{code:04x}"
            for code in (
                *range(0x202A, 0x202F),
                *range(0x2066, 0x206A),
                0x200E,
                0x200F,
            )
        }
    )
    return ConfirmationPrompt(
        title=_ACTION_LABELS.get(action, "核对本次操作"),
        note=_ACTION_IMPACT.get(action, _DEFAULT_IMPACT),
        facts=tuple(facts),
        details=details,
    )


if TYPE_CHECKING:
    pass


def _panel_prepare_material(
    api: LocalAPI,
    command: Command,
    records: ApprovalRecords | None,
    session: Session,
) -> tuple[object, str, str, list[object]] | Response:
    """Prepare the exact challenge without showing a core-owned window.

    The return value is either the frozen material or an already-final response
    (idempotent recall / conflict).  The challenge stays persisted in the core;
    the panel only receives a human-readable projection and returns a gesture.
    """
    if session.entry_kind is not EntryKind.HUMAN_UI or not session.interactive:
        return api._error(
            command, "ENTRY_ACTION_NOT_ALLOWED", "面板确认只接受已核实的宿主连接"
        )
    if command.action not in CHALLENGE_ACTIONS:
        return api._error(
            command, "ENTRY_ACTION_NOT_ALLOWED", "该动作不属于受控确认动作"
        )
    if (
        not command.project_id
        or not command.intent_id
        or type(command.expected_revision) is not int
        or command.expected_revision < 0
        or command.request_id == command.intent_id
    ):
        return api._error(command, "INVALID_COMMAND", "受控写入须有项目、稳定意图和准确预期修订")
    if any(
        key in command.parameters
        for key in ("approval_challenge_id", "user_confirmed", "interaction", "input_digest")
    ):
        return api._error(command, "INVALID_COMMAND", "入口不接受调用方提交的确认事实")
    if not command.target:
        return api._error(command, "INVALID_COMMAND", "受控动作须指定准确目标 target")
    original = api.dispatch(command.model_copy(update={"request_id": uuid4().hex}), session)
    if original.error is None:
        return original.model_copy(update={"request_id": command.request_id})
    if original.error.code == "INTENT_CONFLICT":
        return api._error(command, original.error.code, original.error.message)
    prepared = api.dispatch(
        Command(
            request_id=uuid4().hex,
            intent_id=uuid4().hex,
            action="prepare_approval",
            project_id=command.project_id,
            expected_revision=0,
            parameters={
                "action": command.action,
                "action_intent_id": command.intent_id,
                "target": command.target,
                "parameters": command.parameters,
            },
        ),
        session,
    )
    if prepared.error is not None:
        return api._error(command, prepared.error.code, prepared.error.message)
    if prepared.result is None:
        raise ValueError("核心未返回确认依据")
    basis = prepared.result["basis"]
    challenge_id = str(prepared.result["challenge_id"])
    if not isinstance(basis, dict) or not isinstance(basis.get("input_digest"), str):
        raise ValueError("核心确认依据无效")
    input_digest = basis["input_digest"]
    if not isinstance(input_digest, str) or not input_digest.strip():
        raise ValueError("核心确认依据缺少输入摘要")
    raw_materials = basis.get("materials")
    if records is None or not isinstance(raw_materials, list):
        raise ValueError("无法读取核心保存的准确确认材料")
    saved_materials: list[object] = []
    for ref in raw_materials:
        if not isinstance(ref, dict):
            raise ValueError("确认材料引用无效")
        kind, record_id, revision = (
            ref.get("aggregate_kind"),
            ref.get("record_id"),
            ref.get("record_revision"),
        )
        if (
            not isinstance(kind, str)
            or not kind.strip()
            or not isinstance(record_id, str)
            or not record_id.strip()
            or type(revision) is not int
            or revision < 1
        ):
            raise ValueError("确认材料引用无效")
        saved = records.read(
            aggregate_kind=kind,
            record_id=record_id,
            revision=revision,
        )
        payload = getattr(saved, "payload", None)
        if not isinstance(payload, Mapping) or _digest(payload) != ref["digest"]:
            raise ValueError("确认材料摘要不一致")
        saved_materials.append({"reference": ref, "payload": dict(payload)})
    return basis, challenge_id, input_digest, saved_materials


def prepare_panel_confirmation(
    api: LocalAPI,
    session: Session,
    command: Command,
    *,
    records: ApprovalRecords | None = None,
) -> Response:
    """Freeze the challenge and return only its redacted human projection.

    This is called through the host bridge after the editor has an actual panel
    gesture available.  It does not confirm anything; a separate confirm call
    carries the user's click back for the same challenge.
    """
    if session.entry_kind is not EntryKind.HUMAN_UI or not session.interactive:
        return api._error(
            command, "ENTRY_ACTION_NOT_ALLOWED", "面板确认只接受已核实的宿主连接"
        )
    try:
        material = _panel_prepare_material(api, command, records, session)
        if isinstance(material, Response):
            return material
        basis, challenge_id, input_digest, saved_materials = material
        projected = api.safe_projection(
            {
                "workspace_id": api.workspace_id,
                "instance_id": api.instance_id,
                "command": command.model_dump(mode="json"),
                "saved_basis": basis,
                "saved_materials": saved_materials,
            }
        )
        view = _confirmation_prompt(projected)
        if len(view.details) > MAX_PROMPT_CHARS:
            return api._error(
                command, "CONFIRMATION_PREVIEW_LIMIT", "操作材料过大，无法完整展示确认"
            )
        expires_at = (
            datetime.now(UTC) + timedelta(milliseconds=PANEL_CONFIRMATION_TIMEOUT_MS)
        ).isoformat()
        _remember_panel_session(api, (session.session_id, command.request_id), session, expires_at)
        return Response(
            request_id=command.request_id,
            instance_id=api.instance_id,
            workspace_id=api.workspace_id,
            project_id=command.project_id,
            binding_revision=command.binding_revision,
            intent_id=command.intent_id,
            result={
                "challenge_id": challenge_id,
                "input_digest": input_digest,
                "panel_session_id": session.session_id,
                "expires_at": expires_at,
                "prompt": {
                    "title": view.title,
                    "note": view.note,
                    "facts": [[key, value] for key, value in view.facts],
                    "details": view.details,
                },
            },
        )
    except Exception as error:
        return api._error(command, "PANEL_CONFIRMATION_UNAVAILABLE", api._safe_message(error))



def confirm_panel_confirmation(
    api: LocalAPI,
    session: Session,
    command: Command,
    *,
    challenge_id: str,
    input_digest: str,
) -> Response:
    """Bind one panel gesture to the exact challenge prepared for this command."""
    if session.entry_kind is not EntryKind.HUMAN_UI or not session.interactive:
        return api._error(
            command, "ENTRY_ACTION_NOT_ALLOWED", "面板确认只接受已核实的宿主连接"
        )
    if command.action not in CHALLENGE_ACTIONS:
        return api._error(command, "ENTRY_ACTION_NOT_ALLOWED", "该动作不属于受控确认动作")
    if (
        not isinstance(challenge_id, str)
        or not challenge_id.strip()
        or not isinstance(input_digest, str)
        or not input_digest.strip()
    ):
        return api._error(command, "INVALID_COMMAND", "面板确认缺少准确 challenge")
    if "approval_challenge_id" in command.parameters:
        return api._error(command, "INVALID_COMMAND", "调用方不能预设确认事实")
    key = (session.session_id, command.request_id)
    with _PANEL_SESSIONS_LOCK:
        entry = _PANEL_SESSIONS.pop(key, None)
    if entry is None:
        return api._error(
            command,
            "PANEL_CONFIRMATION_EXPIRED",
            "面板确认已过期，请重新准备并核对本次操作",
        )
    stored_session, timer, expires_at = entry
    timer.cancel()
    if datetime.fromisoformat(expires_at) <= datetime.now(UTC):
        return api._error(
            command,
            "PANEL_CONFIRMATION_EXPIRED",
            "面板确认已过期，请重新准备并核对本次操作",
        )
    try:
        approved = command.model_copy(
            update={
                "parameters": command.parameters | {"approval_challenge_id": challenge_id},
            }
        )
        return api.dispatch_user_confirmation(
            approved, stored_session, challenge_id=challenge_id, input_digest=input_digest
        )
    except Exception as error:
        return api._error(command, "PANEL_CONFIRMATION_UNAVAILABLE", api._safe_message(error))
    finally:
        api.close_session(session)

