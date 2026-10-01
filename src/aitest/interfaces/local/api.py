"""Local protocol adapter: routing, lifecycle guards and safe projections."""

import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import cast

from pydantic import JsonValue

from aitest.application.connectivity import retry_delay
from aitest.contracts.commands import HUMAN_ACTIONS, Command
from aitest.contracts.errors import ErrorDTO
from aitest.contracts.redaction import redact_structure, scrub_secret_text
from aitest.contracts.views import Response

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
    def __init__(
        self,
        instance_id: str,
        workspace_id: str | None = None,
        handlers: dict[str, Handler] | None = None,
        *,
        transaction_port: object | None = None,
        connector: Callable[[], object] | None = None,
        projector: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None,
        credential_projector: (
            Callable[[Mapping[str, object]], Mapping[str, object]] | None
        ) = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
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
        self._sleeper = sleeper or time.sleep
        #: 持久统一连接状态：多次 test_connection 共享同一份结论。
        self.connection_state: dict[str, object] = {
            "connected": False,
            "attempts": 0,
            "last_error": None,
        }
        self._requests: OrderedDict[tuple[str, str], tuple[str, Response]] = (
            OrderedDict()
        )

    def dispatch(self, command: Command, session: Session) -> Response:
        fingerprint = sha256(
            json.dumps(command.model_dump(), sort_keys=True).encode()
        ).hexdigest()
        key = (session.session_id, command.request_id)
        cached = self._requests.get(key)
        if cached:
            if cached[0] != fingerprint:
                return self._error(
                    command,
                    "REQUEST_CONFLICT",
                    "request_id has different inputs",
                )
            return cached[1].model_copy(deep=True)
        if (
            session.entry_kind == EntryKind.AGENT_RELAY
            and command.action in HUMAN_ACTIONS
        ):
            response = self._error(
                command,
                "AWAITING_USER_CONFIRMATION",
                "human confirmation requires a controlled user entry",
            )
        elif (
            command.action in {"begin", "commit", "rollback", "recover"}
            and self.transaction_port is not None
        ):
            response = self._transaction(command)
        elif command.action == "test_connection" and self.connector is not None:
            response = self._connect(command)
        elif command.action in self.handlers:
            try:
                result = self.handlers[command.action](command)
                # 统一出口：无论是否注入投影器，handler 正常结果都必须经过
                # 凭据脱敏，不能依赖上层逐个 handler 自觉过滤。
                result = self.safe_projection(result)
                response = Response(
                    request_id=command.request_id,
                    instance_id=self.instance_id,
                    workspace_id=self.workspace_id,
                    project_id=command.project_id,
                    binding_revision=command.binding_revision,
                    result=cast(dict[str, JsonValue], result),
                )
            except Exception as exc:
                response = self._error(
                    command,
                    getattr(exc, "code", "INTERNAL_ERROR"),
                    self._safe_message(exc),
                )
        elif command.action == "doctor":
            ready = "READY" if self.workspace_id else "NOT_READY"
            doctor_result: dict[str, JsonValue] = {
                "status": ready,
                "protocol": "aitest.local/2.0",
                "supported_actions": cast(
                    list[JsonValue], sorted(set(self.handlers) | {"doctor"})
                ),
                "phase": 1,
            }
            response = Response(
                request_id=command.request_id,
                instance_id=self.instance_id,
                project_id=command.project_id,
                binding_revision=command.binding_revision,
                workspace_id=self.workspace_id,
                result=doctor_result,
            )
        else:
            response = self._error(
                command,
                "CAPABILITY_UNAVAILABLE",
                "action is not implemented in this skeleton",
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
            error=ErrorDTO(
                code=code,
                message=message,
                next_step="See docs/一期/工程状态.md",
            ),
        )

    def _transaction(self, command: Command) -> Response:
        try:
            method = getattr(self.transaction_port, command.action)
            kwargs: dict[str, object] = {
                "request_id": command.request_id,
                "workspace_id": self.workspace_id,
            }
            if command.action == "begin":
                kwargs.update(
                    project_id=command.project_id,
                    intent_id=command.intent_id,
                )
            result = method(**kwargs)
            if isinstance(result, Response):
                return result
            return Response(
                request_id=command.request_id,
                instance_id=self.instance_id,
                workspace_id=self.workspace_id,
                result=dict(result),
            )
        except Exception as exc:
            return self._error(
                command,
                getattr(exc, "code", "INTERNAL_ERROR"),
                self._safe_message(exc),
            )

    def safe_projection(
        self, value: Mapping[str, object]
    ) -> Mapping[str, object]:
        """Return a redacted projection; credentials never leave this adapter."""
        projector = self.credential_projector or self.projector
        if projector is not None:
            return projector(value)
        redacted, _changed = redact_structure(value)
        return cast(Mapping[str, object], redacted)

    def _connect(self, command: Command) -> Response:
        assert self.connector is not None
        retries_completed = 0
        while True:
            error_message: str | None = None
            result: object = None
            try:
                result = self.connector()
            except Exception as exc:  # 连接探测允许归一失败，不允许抛出协议外
                error_message = self._safe_message(exc)
            # 管道/端点未就绪时连接器返回 None/falsy，同样属于未连接，
            # 必须按重试策略继续，而不是直接报告 connected=False 成功返回。
            connected = bool(result)
            self.connection_state = {
                "connected": connected,
                "attempts": retries_completed + 1,
                "last_error": None if connected else (error_message or "目标未就绪"),
            }
            if connected:
                return Response(
                    request_id=command.request_id,
                    instance_id=self.instance_id,
                    workspace_id=self.workspace_id,
                    result={
                        "connected": True,
                        "attempts": retries_completed + 1,
                    },
                )
            delay = retry_delay(
                retries_completed, read_only=True, idempotency_proven=True
            )
            if delay is None:
                return self._error(
                    command,
                    "CONNECTIVITY_FAILED",
                    error_message or "连接目标持续未就绪",
                )
            # 按应用层统一重试节奏退避；原实现拿到 delay 却空转，是忙等缺陷。
            self._sleeper(delay)
            retries_completed += 1

    @staticmethod
    def _safe_message(exc: Exception) -> str:
        text = str(exc).replace("\r", " ").replace("\n", " ")
        text, _changed = scrub_secret_text(text)
        return text[:500] or exc.__class__.__name__
