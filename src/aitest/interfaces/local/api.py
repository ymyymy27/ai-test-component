"""Read-only skeleton API. No write action is registered until its full guards exist."""

import json
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from aitest.contracts.commands import HUMAN_ACTIONS, Command
from aitest.contracts.views import ErrorDTO, Response


class EntryKind(StrEnum):
    HUMAN_UI = "human_ui"
    INTERACTIVE_CLI = "interactive_cli"
    AGENT_RELAY = "agent_relay"


@dataclass(frozen=True, slots=True)
class Session:
    """Allocated by a trusted host, never constructed from command parameters."""

    session_id: str
    entry_kind: EntryKind


class LocalAPI:
    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self._requests: OrderedDict[tuple[str, str], tuple[str, Response]] = OrderedDict()

    def dispatch(self, command: Command, session: Session) -> Response:
        fingerprint = sha256(json.dumps(command.model_dump(), sort_keys=True).encode()).hexdigest()
        key = (session.session_id, command.request_id)
        cached = self._requests.get(key)
        if cached:
            if cached[0] != fingerprint:
                return self._error(command, "REQUEST_CONFLICT", "request_id has different inputs")
            return cached[1].model_copy(deep=True)
        if session.entry_kind == EntryKind.AGENT_RELAY and command.action in HUMAN_ACTIONS:
            response = self._error(
                command,
                "AWAITING_USER_CONFIRMATION",
                "human confirmation requires a controlled user entry",
            )
        elif command.action == "doctor":
            response = Response(
                request_id=command.request_id,
                instance_id=self.instance_id,
                project_id=command.project_id,
                binding_revision=command.binding_revision,
                result={
                    "status": "NOT_READY",
                    "implementation_stage": "skeleton",
                    "reason": "workspace transactions and local IPC are not implemented",
                    "supported_actions": ["doctor"],
                    "phase": 1,
                },
            )
        else:
            response = self._error(
                command, "CAPABILITY_UNAVAILABLE", "action is not implemented in this skeleton"
            )
        self._requests[key] = (fingerprint, response.model_copy(deep=True))
        if len(self._requests) > 256:
            self._requests.popitem(last=False)
        return response

    def _error(self, command: Command, code: str, message: str) -> Response:
        return Response(
            request_id=command.request_id,
            instance_id=self.instance_id,
            project_id=command.project_id,
            binding_revision=command.binding_revision,
            error=ErrorDTO(code=code, message=message, next_step="See docs/一期/工程状态.md"),
        )
