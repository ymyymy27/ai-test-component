"""Local protocol adapter: routing, lifecycle guards and safe projections."""

import json
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from collections.abc import Callable, Mapping

from aitest.contracts.commands import HUMAN_ACTIONS, Command
from aitest.contracts.views import ErrorDTO, Response
from aitest.application.connectivity import retry_delay

Handler = Callable[[Command], Mapping[str, object] | dict[str, object]]


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
    def __init__(self, instance_id: str, workspace_id: str | None = None, handlers: dict[str, Handler] | None = None, *, transaction_port: object | None = None, connector: Callable[[], object] | None = None, projector: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None, credential_projector: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None) -> None:
        """本地协议适配器。

        .. note::
            ``handlers`` 参数仅供 A 包内部装配（来自 :class:`UseCaseRegistry` 快照）。
            B/C/D 上层包**禁止直接构造 LocalAPI 并传入 handlers**，必须通过
            ``register_use_cases`` 注册后由 ``create_api`` 装配。直接传入 handlers
            会绕过统一注册窗口与所有者校验。
        """
        self.instance_id = instance_id
        self.workspace_id = workspace_id
        self.handlers = handlers or {}
        self.transaction_port = transaction_port
        self.connector = connector
        self.projector = projector
        self.credential_projector = credential_projector
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
        elif command.action in {"begin", "commit", "rollback", "recover"} and self.transaction_port is not None:
            response = self._transaction(command)
        elif command.action == "test_connection" and self.connector is not None:
            response = self._connect(command)
        elif command.action in self.handlers:
            try:
                result = self.handlers[command.action](command)
                projector = self.credential_projector or self.projector
                if projector is not None:
                    result = projector(result)
                response = Response(request_id=command.request_id, instance_id=self.instance_id, workspace_id=self.workspace_id, project_id=command.project_id, binding_revision=command.binding_revision, result=result)
            except Exception as exc:
                response = self._error(command, getattr(exc, "code", "INTERNAL_ERROR"), str(exc))
        elif command.action == "doctor":
            response = Response(
                request_id=command.request_id,
                instance_id=self.instance_id,
                project_id=command.project_id,
                binding_revision=command.binding_revision,
                workspace_id=self.workspace_id, result={"status": "READY" if self.workspace_id else "NOT_READY", "protocol": "aitest.local/2.0", "supported_actions": sorted(set(self.handlers)|{"doctor"}), "phase": 1},
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

    def _transaction(self, command: Command) -> Response:
        try:
            method = getattr(self.transaction_port, command.action)
            kwargs = {"request_id": command.request_id, "workspace_id": self.workspace_id}
            if command.action == "begin":
                kwargs.update(project_id=command.project_id, intent_id=command.intent_id)
            result = method(**kwargs)
            return result if isinstance(result, Response) else Response(request_id=command.request_id, instance_id=self.instance_id, workspace_id=self.workspace_id, result=dict(result))
        except Exception as exc:
            return self._error(command, getattr(exc, "code", "INTERNAL_ERROR"), self._safe_message(exc))

    def safe_projection(self, value: Mapping[str, object]) -> Mapping[str, object]:
        """Return a redacted projection; credentials are never returned by this adapter."""
        projector = self.credential_projector or self.projector
        return projector(value) if projector is not None else {key: value[key] for key in value if key.lower() not in {"password", "token", "secret", "api_key", "access_token"}}

    def _connect(self, command: Command) -> Response:
        attempts = 0
        while True:
            try:
                result = self.connector()
                return Response(request_id=command.request_id, instance_id=self.instance_id, workspace_id=self.workspace_id, result={"connected": bool(result), "attempts": attempts + 1})
            except Exception as exc:
                delay = retry_delay(attempts, read_only=True, idempotency_proven=True)
                if delay is None:
                    return self._error(command, "CONNECTIVITY_FAILED", self._safe_message(exc))
                attempts += 1

    @staticmethod
    def _safe_message(exc: Exception) -> str:
        text = str(exc).replace("\r", " ").replace("\n", " ")
        return text[:500] or exc.__class__.__name__
