"""Test-only human gesture adapter built on the real panel confirmation path."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from aitest.application.ports import ApprovalRecords
from aitest.contracts.commands import CHALLENGE_ACTIONS, Command
from aitest.contracts.responses import Response
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from aitest.interfaces.local.panel_confirmation import (
    confirm_panel_confirmation,
    prepare_panel_confirmation,
)


def dispatch_native(
    api: LocalAPI,
    command: Command,
    *,
    prompt: Callable[[object], bool] = lambda _prompt: True,
    records: ApprovalRecords | None = None,
) -> Response:
    """Simulate one controlled human gesture through panel prepare/confirm.

    The name is kept for existing tests; production native windows no longer exist.
    """

    session = Session("panel-test-" + uuid4().hex, EntryKind.HUMAN_UI, True)
    try:
        if command.action not in CHALLENGE_ACTIONS:
            return api.dispatch(command, session)
        prepared = prepare_panel_confirmation(api, session, command, records=records)
        if prepared.error is not None:
            return prepared
        assert prepared.result is not None
        if "challenge_id" not in prepared.result:
            # Already committed: prepare returns the saved receipt without a prompt.
            return prepared
        if not prompt(None):
            return api._error(
                command,
                "NATIVE_CONFIRMATION_UNAVAILABLE",
                "user declined the confirmation",
            )
        return confirm_panel_confirmation(
            api,
            session,
            command,
            challenge_id=str(prepared.result["challenge_id"]),
            input_digest=str(prepared.result["input_digest"]),
        )
    finally:
        api.close_session(session)
