"""Core-owned Windows gesture adapter; callers can request a prompt, never grant it."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from aitest.application.approval_service import _digest
from aitest.application.ports import ApprovalRecords
from aitest.contracts.commands import PHASE_ONE_ACTIONS, READ_ACTIONS, Command
from aitest.contracts.responses import Response
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session

NATIVE_CONFIRMATION_VERSION = "native-confirmation/1"
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


def windows_prompt(text: str) -> bool:
    """A bounded core-owned dialog. Timeout, inaccessible desktop and No all deny."""
    if os.name != "nt" or len(text) > MAX_PROMPT_CHARS:
        return False
    # Lazy import: headless installations keep every noninteractive entry usable.
    from tkinter import scrolledtext, ttk

    accepted = False
    root = _create_windows_root()
    try:
        root.title("AI Test · 核心操作确认")
        root.geometry("900x650")
        root.minsize(600, 400)
        ttk.Label(root, text="核对完整输入与已保存依据；两分钟未确认将取消。", padding=12).pack()
        preview = scrolledtext.ScrolledText(root, wrap="word", width=100, height=30)
        preview.pack(fill="both", expand=True, padx=12)
        preview.insert("1.0", text)
        preview.configure(state="disabled")
        buttons = ttk.Frame(root, padding=12)
        buttons.pack(fill="x")

        def accept() -> None:
            nonlocal accepted
            accepted = True
            root.quit()

        cancel = ttk.Button(buttons, text="取消", command=root.quit)
        cancel.pack(side="right", padx=8)
        ttk.Button(buttons, text="确认本次操作", command=accept).pack(side="right")
        root.protocol("WM_DELETE_WINDOW", root.quit)
        root.bind("<Escape>", lambda event: root.quit())
        root.bind("<Return>", lambda event: root.quit())
        root.after(NATIVE_CONFIRMATION_TIMEOUT_MS, root.quit)
        cancel.focus_set()
        root.mainloop()
        return accepted
    finally:
        root.destroy()


def dispatch_native(
    api: LocalAPI,
    command: Command,
    *,
    prompt: Callable[[str], bool] = windows_prompt,
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
        text = (
            "请核对以下操作、项目、输入和已保存依据。点击“确认本次操作”才会提交。\n"
            "测试、网络或外部命令可能产生副作用；仅在理解准确输入后确认。\n"
            "取消或两分钟未响应将取消；不代表任何业务验证通过。\n\n"
            + json.dumps(projected, ensure_ascii=False, indent=2, allow_nan=False).translate(
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
        )
        if len(text) > MAX_PROMPT_CHARS:
            return api._error(
                command, "CONFIRMATION_PREVIEW_LIMIT", "操作材料过大，无法完整展示确认"
            )
        if prompt(text) is not True:
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
