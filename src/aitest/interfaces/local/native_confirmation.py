"""Core-owned Windows gesture adapter; callers can request a prompt, never grant it."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast
from uuid import uuid4

from pydantic import JsonValue

from aitest.application.approval_service import _digest
from aitest.application.ports import ApprovalRecords
from aitest.contracts.commands import PHASE_ONE_ACTIONS, READ_ACTIONS, Command
from aitest.contracts.responses import Response
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session

NATIVE_CONFIRMATION_VERSION = "native-confirmation/1"
NATIVE_BATCH_VERSION = "native-batch/1"
NATIVE_CONFIRMATION_TIMEOUT_MS = 120_000
CHALLENGE_ACTIONS = frozenset(
    {
        "save_binding",
        "save_environment",
        "publish_rules",
        "publish_plan",
        "submit_delivery",
        "save_model_outbound_policy",
        "authorize_step",
        "revise_pending_steps",
        "narrow_driver",
        "confirm_basis",
    }
)
NATIVE_ACTIONS = (
    (PHASE_ONE_ACTIONS | {"save_binding"})
    - READ_ACTIONS
    - {
        "prepare_approval",
        "revoke_approval",
    }
)
MAX_PROMPT_CHARS = 128_000

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
    "environment": (("data_reset_policy", "数据复位"),),
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
    import tkinter


def _create_windows_root() -> tkinter.Tk:
    """Locate Tcl/Tk in the running interpreter, including Windows virtualenvs."""
    import tkinter as tk

    # Virtualenvs can lose the base installation's Tcl search path. Never search
    # the project, working directory or caller-supplied command for executable Tcl.
    libraries = {
        "TCL_LIBRARY": Path(sys.base_prefix) / "tcl" / f"tcl{tk.TclVersion}",
        "TK_LIBRARY": Path(sys.base_prefix) / "tcl" / f"tk{tk.TkVersion}",
    }
    if not all(
        (path / name).is_file()
        for path, name in zip(libraries.values(), ("init.tcl", "tk.tcl"), strict=True)
    ):
        raise RuntimeError("Python 安装缺少 Tcl/Tk；请修复解释器的 Tcl/Tk 组件")
    previous = {key: os.environ.get(key) for key in libraries}
    try:
        for key, path in libraries.items():
            os.environ[key] = str(path)
        return tk.Tk()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def windows_prompt(prompt: ConfirmationPrompt) -> bool:
    """A bounded core-owned dialog. Timeout, inaccessible desktop and No all deny."""
    if os.name != "nt":
        return False
    # Lazy import: headless installations keep every noninteractive entry usable.
    from tkinter import scrolledtext, ttk

    accepted = False
    root = _create_windows_root()
    try:
        root.title("AI 测试 · 确认这一步")
        root.minsize(560, 280)
        header = ttk.Frame(root, padding=(18, 16, 18, 4))
        header.pack(fill="x")
        ttk.Label(header, text=prompt.title, font=("Microsoft YaHei UI", 14, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            header, text=prompt.note, wraplength=620, justify="left", foreground="#8a4b00"
        ).pack(anchor="w", pady=(8, 0))
        body = ttk.Frame(root, padding=(18, 8, 18, 4))
        body.pack(fill="both", expand=True)
        table = ttk.Frame(body)
        table.pack(fill="x")
        for row, (label, value) in enumerate(prompt.facts):
            ttk.Label(table, text=label, width=9).grid(row=row, column=0, sticky="nw", pady=3)
            ttk.Label(table, text=value, wraplength=520, justify="left").grid(
                row=row, column=1, sticky="w", pady=3
            )
        preview = scrolledtext.ScrolledText(body, wrap="word", height=10)
        open_state = {"shown": False}

        def fit() -> None:
            """按当前内容定高，避免固定尺寸留下的空白。"""
            root.update_idletasks()
            root.geometry(f"640x{min(720, max(300, root.winfo_reqheight()))}")

        def toggle() -> None:
            open_state["shown"] = not open_state["shown"]
            if open_state["shown"]:
                preview.pack(fill="both", expand=True, pady=(8, 0))
                detailed.configure(text="收起技术细节")
            else:
                preview.pack_forget()
                detailed.configure(text="查看技术细节（完整输入）")
            fit()

        detailed = ttk.Button(body, text="查看技术细节（完整输入）", command=toggle)
        detailed.pack(anchor="w", pady=(10, 0))
        preview.insert("1.0", prompt.details)
        preview.configure(state="disabled")
        footer = ttk.Frame(root, padding=(18, 8, 18, 14))
        footer.pack(fill="x")
        ttk.Label(footer, text="两分钟内未确认将自动取消；取消不代表测试通过。").pack(side="left")

        def accept() -> None:
            nonlocal accepted
            accepted = True
            root.quit()

        # Windows 习惯：确认在左、取消在右；回车仍绑定取消，误按不提交。
        cancel = ttk.Button(footer, text="取消", command=root.quit)
        cancel.pack(side="right")
        confirm = ttk.Button(footer, text="确认本次操作", command=accept)
        confirm.pack(side="right", padx=8)
        root.protocol("WM_DELETE_WINDOW", root.quit)
        root.bind("<Escape>", lambda event: root.quit())
        root.bind("<Return>", lambda event: root.quit())
        root.after(NATIVE_CONFIRMATION_TIMEOUT_MS, root.quit)
        cancel.focus_set()
        fit()
        root.mainloop()
        return accepted
    finally:
        root.destroy()


@dataclass
class BatchPromptItem:
    """One prepared action in the core-owned batch authorization window."""

    command: Command
    challenge_id: str
    input_digest: str
    prompt: ConfirmationPrompt
    state: str = "pending"
    message: str = "待确认"
    response: Response | None = None


def windows_batch_prompt(
    items: Sequence[BatchPromptItem],
    confirm: Callable[[int], tuple[bool, str]],
) -> None:
    """One window, one confirm button per action; no "confirm all" gesture."""

    if os.name != "nt":
        raise RuntimeError("当前平台不支持核心原生授权窗口")
    import tkinter as tk
    from tkinter import scrolledtext, ttk

    root = _create_windows_root()
    try:
        root.title("AI 测试 · 授权本次执行")
        root.minsize(760, 460)
        header = ttk.Frame(root, padding=(18, 14, 18, 6))
        header.pack(fill="x")
        ttk.Label(
            header, text="逐项确认本次执行", font=("Microsoft YaHei UI", 14, "bold")
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "每一项都需要单独点击「确认此项」；未确认的项不会获得授权。"
                "关闭窗口后未确认项失效，不会自动通过。"
            ),
            wraplength=700,
            justify="left",
            foreground="#8a4b00",
        ).pack(anchor="w", pady=(6, 0))

        container = ttk.Frame(root)
        container.pack(fill="both", expand=True, padx=(12, 12), pady=(4, 4))
        canvas = tk.Canvas(container, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for index, item in enumerate(items):
            card = ttk.LabelFrame(
                inner,
                text=f"{index + 1}. {item.prompt.title}",
                padding=(10, 8),
            )
            card.pack(fill="x", padx=8, pady=6)
            ttk.Label(
                card, text=item.prompt.note, wraplength=640, justify="left",
                foreground="#8a4b00",
            ).pack(anchor="w")
            for label, value in item.prompt.facts:
                ttk.Label(
                    card, text=f"{label}：{value}", wraplength=640, justify="left"
                ).pack(anchor="w", pady=(2, 0))
            status = ttk.Label(card, text=item.message, foreground="#666")
            status.pack(anchor="w", pady=(4, 0))
            buttons = ttk.Frame(card)
            buttons.pack(anchor="w", pady=(6, 0))

            def show_details(data: ConfirmationPrompt = item.prompt) -> None:
                top = tk.Toplevel(root)
                top.title("技术细节（完整输入）")
                top.geometry("760x520")
                text = scrolledtext.ScrolledText(top, wrap="word")
                text.pack(fill="both", expand=True)
                text.insert("1.0", data.details)
                text.configure(state="disabled")

            ttk.Button(buttons, text="查看技术细节", command=show_details).pack(side="left")

            button = ttk.Button(buttons, text="确认此项")

            def accept(
                i: int = index,
                control: object = button,
                label: object = status,
            ) -> None:
                ok, message = confirm(i)
                control.configure(  # type: ignore[attr-defined]
                    state="disabled", text="已确认" if ok else "未确认"
                )
                label.configure(text=message)  # type: ignore[attr-defined]

            button.configure(command=accept)
            button.pack(side="left", padx=8)
            if item.state == "confirmed":
                button.configure(state="disabled", text="已确认")
            elif item.state == "failed":
                button.configure(state="disabled", text="未确认")

        footer = ttk.Frame(root, padding=(18, 6, 18, 14))
        footer.pack(fill="x")
        ttk.Label(
            footer,
            text="本窗口不会自动通过；关闭后未确认项需重新发起授权。",
        ).pack(side="left")
        ttk.Button(footer, text="完成", command=root.quit).pack(side="right")
        root.protocol("WM_DELETE_WINDOW", root.quit)
        root.bind("<Escape>", lambda _event: root.quit())
        # A batch may take longer than one action; the bound is explicit and
        # unconfirmed challenges are revoked when the session closes.
        root.after(10 * 60 * 1000, root.quit)
        root.mainloop()
    finally:
        root.destroy()


def _batch_item_from_command(
    api: LocalAPI,
    command: Command,
    session: Session,
    records: ApprovalRecords | None,
) -> BatchPromptItem:
    """Prepare one challenge and its exact prompt; no confirmation is created."""

    original = api.dispatch(command.model_copy(update={"request_id": uuid4().hex}), session)
    if original.error is None:
        return BatchPromptItem(
            command=command,
            challenge_id="",
            input_digest="",
            prompt=ConfirmationPrompt(
                title=_ACTION_LABELS.get(command.action, "核对本次操作"),
                note="此前已保存同一业务意图的有效授权。",
                facts=(),
                details="",
            ),
            state="confirmed",
            message="此前已授权",
            response=original.model_copy(update={"request_id": command.request_id}),
        )
    if original.error.code == "INTENT_CONFLICT":
        raise ValueError(original.error.message)
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
        raise ValueError(prepared.error.message)
    if prepared.result is None:
        raise ValueError("核心未返回确认依据")
    basis = prepared.result["basis"]
    raw_challenge = prepared.result.get("challenge_id")
    if not isinstance(raw_challenge, str) or not raw_challenge:
        raise ValueError("核心确认挑战无效")
    challenge_id = raw_challenge
    if not isinstance(basis, dict):
        raise ValueError("核心确认依据无效")
    raw_digest = basis.get("input_digest")
    if not isinstance(raw_digest, str):
        raise ValueError("核心确认摘要无效")
    input_digest = raw_digest
    materials = basis.get("materials")
    if records is None or not isinstance(materials, list):
        raise ValueError("无法读取核心保存的准确确认材料")
    saved_materials: list[object] = []
    for ref in materials:
        if not isinstance(ref, dict):
            raise ValueError("确认材料引用无效")
        aggregate_kind = ref.get("aggregate_kind")
        record_id = ref.get("record_id")
        revision = ref.get("record_revision")
        digest = ref.get("digest")
        if (
            not isinstance(aggregate_kind, str)
            or not isinstance(record_id, str)
            or type(revision) is not int
            or not isinstance(digest, str)
        ):
            raise ValueError("确认材料引用无效")
        saved = records.read(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            revision=revision,
        )
        payload = getattr(saved, "payload", None)
        if not isinstance(payload, Mapping) or _digest(payload) != digest:
            raise ValueError("确认材料摘要不一致")
        saved_materials.append({"reference": ref, "payload": dict(payload)})
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
        raise ValueError("操作材料过大，无法完整展示确认")
    return BatchPromptItem(
        command=command,
        challenge_id=challenge_id,
        input_digest=input_digest,
        prompt=view,
    )


def dispatch_native_batch(
    api: LocalAPI,
    commands: Sequence[Command],
    *,
    prompt: Callable[
        [Sequence[BatchPromptItem], Callable[[int], tuple[bool, str]]], None
    ] = windows_batch_prompt,
    records: ApprovalRecords | None = None,
) -> Response:
    """One window, per-action gestures, independent confirmation records.

    The caller cannot submit challenges, interactions or digest values.  Each
    row is prepared and later confirmed as the *same* action intent; a single
    click never confirms two challenges.
    """

    if not commands:
        raise ValueError("批量授权至少需要一个动作")
    project = commands[0].project_id
    if not project or any(
        command.action != "authorize_step" or command.project_id != project
        for command in commands
    ):
        raise ValueError("批量授权只接受同一项目的 authorize_step 动作")
    for command in commands:
        if (
            not command.intent_id
            or type(command.expected_revision) is not int
            or command.expected_revision < 0
            or command.request_id == command.intent_id
            or not command.target
        ):
            raise ValueError("批量授权需要项目、稳定意图、准确目标和预期修订")
        if any(
            key in command.parameters
            for key in ("approval_challenge_id", "user_confirmed", "interaction", "input_digest")
        ):
            raise ValueError("入口不接受调用方提交的确认事实")
    session = Session("native-batch-" + uuid4().hex, EntryKind.HUMAN_UI, True)
    request_id = uuid4().hex
    try:
        items: list[BatchPromptItem] = []
        for command in commands:
            try:
                items.append(_batch_item_from_command(api, command, session, records))
            except Exception as error:
                items.append(
                    BatchPromptItem(
                        command=command,
                        challenge_id="",
                        input_digest="",
                        prompt=ConfirmationPrompt(
                            title=_ACTION_LABELS.get(command.action, "核对本次操作"),
                            note=api._safe_message(error),
                            facts=(),
                            details="",
                        ),
                        state="failed",
                        message=api._safe_message(error),
                    )
                )

        def confirm(index: int) -> tuple[bool, str]:
            if index < 0 or index >= len(items):
                return False, "无效的授权项"
            item = items[index]
            if item.state == "confirmed":
                return True, item.message
            if item.state == "failed" or not item.challenge_id:
                return False, item.message
            try:
                approved = item.command.model_copy(
                    update={
                        "parameters": item.command.parameters
                        | {"approval_challenge_id": item.challenge_id}
                    }
                )
                response = api.dispatch_user_confirmation(
                    approved,
                    session,
                    challenge_id=item.challenge_id,
                    input_digest=item.input_digest,
                )
                if response.error is not None:
                    item.state = "failed"
                    item.message = response.error.message
                    return False, item.message
                item.state = "confirmed"
                item.message = "已确认"
                item.response = response
                return True, item.message
            except Exception as error:
                item.state = "failed"
                item.message = api._safe_message(error)
                return False, item.message

        prompt(items, confirm)
        confirmed = sum(1 for item in items if item.state == "confirmed")
        pending = sum(1 for item in items if item.state == "pending")
        failed = sum(1 for item in items if item.state == "failed")
        result: dict[str, object] = {
            "items": [
                {
                    "index": index,
                    "step_id": item.command.target,
                    "action": item.command.action,
                    "state": item.state,
                    "message": item.message,
                    "response": (
                        item.response.model_dump(mode="json")
                        if item.response is not None
                        else None
                    ),
                }
                for index, item in enumerate(items)
            ],
            "confirmed_count": confirmed,
            "pending_count": pending,
            "failed_count": failed,
        }
        return Response(
            request_id=request_id,
            instance_id=api.instance_id,
            workspace_id=api.workspace_id,
            project_id=project,
            result=cast(dict[str, JsonValue], result),
        )
    finally:
        api.close_session(session)


def dispatch_native(
    api: LocalAPI,
    command: Command,
    *,
    prompt: Callable[[ConfirmationPrompt], bool] = windows_prompt,
    records: ApprovalRecords | None = None,
) -> Response:
    """Allocate a private UI session, freeze a challenge, then await an actual gesture.

    No caller-supplied approval/challenge/interaction is accepted. The relay session
    stays a relay; this adapter owns both the dialog and its short-lived UI session.
    """
    if command.action not in NATIVE_ACTIONS:
        return api._error(command, "ENTRY_ACTION_NOT_ALLOWED", "该动作不属于原生受控写入口")
    if (
        not command.project_id
        or not command.intent_id
        or type(command.expected_revision) is not int
        or command.expected_revision < 0
        or command.request_id == command.intent_id
    ):
        return api._error(command, "INVALID_COMMAND", "原生写入须有项目、稳定意图和准确预期修订")
    if any(
        key in command.parameters
        for key in (
            "approval_challenge_id",
            "user_confirmed",
            "interaction",
            "input_digest",
        )
    ):
        return api._error(command, "INVALID_COMMAND", "入口不接受调用方提交的确认事实")
    session = Session("native-ui-" + uuid4().hex, EntryKind.HUMAN_UI, True)
    try:
        basis: object = None
        challenge_id: str | None = None
        input_digest: str | None = None
        saved_materials: list[object] = []
        if command.action in CHALLENGE_ACTIONS:
            if not command.target:
                return api._error(command, "INVALID_COMMAND", "受控动作须指定准确目标 target")
            # These adapters require an actual UserInteraction for a new write.
            # With no interaction this can only recall an already committed intent
            # or fail. Recall must precede preparing against a now advanced revision.
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
            if records is None or not isinstance(basis.get("materials"), list):
                raise ValueError("无法读取核心保存的准确确认材料")
            for ref in basis["materials"]:
                if not isinstance(ref, dict):
                    raise ValueError("确认材料引用无效")
                saved = records.read(
                    aggregate_kind=ref["aggregate_kind"],
                    record_id=ref["record_id"],
                    revision=ref["record_revision"],
                )
                payload = getattr(saved, "payload", None)
                if not isinstance(payload, Mapping) or _digest(payload) != ref["digest"]:
                    raise ValueError("确认材料摘要不一致")
                saved_materials.append({"reference": ref, "payload": dict(payload)})
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
        if prompt(view) is not True:
            return api._error(command, "USER_DECLINED", "用户未在核心原生窗口确认，操作未提交")
        if challenge_id is not None and input_digest is not None:
            approved = command.model_copy(
                update={
                    "parameters": command.parameters | {"approval_challenge_id": challenge_id},
                }
            )
            return api.dispatch_user_confirmation(
                approved,
                session,
                challenge_id=challenge_id,
                input_digest=input_digest,
            )
        return api.dispatch(command, session)
    except Exception as error:
        return api._error(command, "NATIVE_CONFIRMATION_UNAVAILABLE", api._safe_message(error))
    finally:
        api.close_session(session)
