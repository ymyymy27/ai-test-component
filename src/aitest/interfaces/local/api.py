"""Local protocol adapter: routing, lifecycle guards and safe projections."""

import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import cast
from uuid import uuid4

from pydantic import JsonValue

from aitest.application.connectivity import retry_delay
from aitest.contracts.commands import HUMAN_ACTIONS, Command
from aitest.contracts.errors import ErrorDTO
from aitest.contracts.redaction import (
    redact_structure,
    safeguard_projector,
    scrub_secret_text,
)
from aitest.contracts.views import Response
from aitest.domain.approvals import ApprovalRequired, TrustedActor, UserInteraction
from aitest.domain.approvals import EntryKind as EntryKind
from aitest.interfaces.local.actor_context import CoreActorContext

Handler = Callable[[Command], Mapping[str, object] | dict[str, object]]


@dataclass(frozen=True, slots=True)
class Session:
    """Allocated by a trusted host, never constructed from command parameters."""

    session_id: str
    entry_kind: EntryKind
    interactive: bool = False
    interaction: UserInteraction | None = None

    @property
    def origin(self) -> tuple[str, EntryKind, bool]:
        """A channel's allocated identity; one gesture does not create a new channel."""
        return self.session_id, self.entry_kind, self.interactive


@dataclass(frozen=True, slots=True)
class _TransactionOwnership:
    """会话内活动事务的归属事实（A-14）。

    传输请求幂等（command.request_id）与事务归属（begin 建立的句柄）是
    两个不同事实：commit/rollback 必须显式引用 begin 句柄，异号且不持有
    句柄的结束请求必须拒绝，不能路由到他人事务。
    """

    begin_request_id: str
    project_id: str | None


#: commit/rollback 在 parameters 中携带的 begin 句柄字段。
BEGIN_REQUEST_ID_PARAM = "begin_request_id"


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
        connection_persistence: object | None = None,
        capability_gate: object | None = None,
        actors: CoreActorContext | None = None,
        session_finalizer: Callable[[Session], None] | None = None,
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
        # 自定义投影器只能再组织输出；其结果仍必须过统一脱敏底线（A-09），
        # 装配时即包装，调用方无法通过自定义投影器绕过凭据过滤。
        self.projector = safeguard_projector(projector) if projector is not None else None
        # This callback is the configured known-credential boundary. Failure
        # must block the output, rather than fall back to pattern-only filtering.
        self.credential_projector = credential_projector
        self._sleeper = sleeper or time.sleep
        #: 持久统一连接状态：多次 test_connection 共享同一份结论；A-10 起
        #: 装配连接持久化后，初始结论从工作空间事实台账水合（跨重启可恢复）。
        self.connection_state: dict[str, object] = {
            "connected": False,
            "attempts": 0,
            "last_error": None,
        }
        self._connection_persistence = connection_persistence
        #: 动作级能力门（A-10）；接口层只按结构调用，不导入基础设施。
        self.capability_gate = capability_gate
        self.actors = actors
        self._session_finalizer = session_finalizer
        if connection_persistence is not None:
            try:
                recovered = connection_persistence.load()  # type: ignore[attr-defined]
            except Exception as exc:
                reason = self._safe_message(exc)
                self.connection_state.update(last_error=reason, last_error_kind="storage")
                self._report_gate_connection(False, reason=reason, error_kind="storage")
            else:
                if isinstance(recovered, dict):
                    self.connection_state.update(recovered)
        self._requests: OrderedDict[tuple[str, EntryKind, bool, str], tuple[str, Response]] = (
            OrderedDict()
        )
        #: 受控通道来源 → 活动事务归属（A-14）。
        self._active_transactions: dict[tuple[str, EntryKind, bool], _TransactionOwnership] = {}

    def dispatch(self, command: Command, session: Session) -> Response:
        fingerprint = sha256(json.dumps(command.model_dump(), sort_keys=True).encode()).hexdigest()
        key = (*session.origin, command.request_id)
        cached = self._requests.get(key)
        if cached:
            if cached[0] != fingerprint:
                return self._error(
                    command,
                    "REQUEST_CONFLICT",
                    "request_id has different inputs",
                )
            return cached[1].model_copy(deep=True)
        if session.entry_kind == EntryKind.AGENT_RELAY and command.action in HUMAN_ACTIONS:
            response = self._error(
                command,
                "AWAITING_USER_CONFIRMATION",
                "human confirmation requires a controlled user entry",
            )
        elif (
            command.action in {"begin", "commit", "rollback", "recover"}
            and self.transaction_port is not None
        ):
            response = self._transaction(command, session)
        elif command.action == "test_connection" and self.connector is not None:
            response = self._connect(command)
        elif (
            command.action in self.handlers
            and (denied_reason := self._gate_denial(command.action, command.parameters)) is not None
        ):
            # A-10：只拦截实际依赖故障能力的动作，不依赖该能力的动作与
            # doctor/test_connection 等恢复性动作不在此分支，继续可用。
            response = self._error(command, "CAPABILITY_DEGRADED", denied_reason)
        elif command.action in self.handlers:
            try:
                result = self._invoke(command, session)
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
                "supported_actions": cast(list[JsonValue], sorted(set(self.handlers) | {"doctor"})),
                "phase": 1,
            }
            if self.capability_gate is not None:
                # A-10：显式报告各项外部能力条件与动作依赖，调用方据此做
                # 动作级降级判断，而不是把单个适配器故障当成整体不可用。
                doctor_result["dependencies"] = cast(
                    JsonValue,
                    self.capability_gate.snapshot(),  # type: ignore[attr-defined]
                )
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

    def _invoke(self, command: Command, session: Session) -> Mapping[str, object]:
        if self.actors is None or command.project_id is None or self.workspace_id is None:
            return self.handlers[command.action](command)
        actor = TrustedActor(
            self.workspace_id,
            command.project_id,
            session.session_id,
            session.entry_kind,
            session.interactive,
            session.interaction,
        )
        with self.actors.bind(actor):
            return self.handlers[command.action](command)

    def close_session(self, session: Session) -> None:
        """End only this channel's transaction and unused challenges, preserving history."""
        active = self._active_transactions.get(session.origin)
        if active is not None:
            command = Command(
                action="rollback", request_id="close-" + uuid4().hex, project_id=active.project_id
            )
            result = self._call_transaction_port(
                command, action="rollback", owner_request_id=active.begin_request_id
            )
            if isinstance(result, Response) and result.error is not None:
                raise RuntimeError("the closing session's transaction could not be rolled back")
            self._active_transactions.pop(session.origin, None)
        if self._session_finalizer is not None:
            self._session_finalizer(session)
        for key in tuple(self._requests):
            if key[:3] == session.origin:
                self._requests.pop(key)

    def dispatch_user_confirmation(
        self, command: Command, session: Session, *, challenge_id: str, input_digest: str
    ) -> Response:
        """Called by the controlled host's actual user-event adapter, not a Command.

        The pipe/CLI adapter must establish that event in its controlled channel.
        Relay cannot reach this path through ordinary business parameters.
        """
        if session.entry_kind is EntryKind.AGENT_RELAY or not session.interactive:
            return self._error(
                command,
                ApprovalRequired.code,
                "confirmation requires an actual controlled user event",
            )
        if command.parameters.get("approval_challenge_id") != challenge_id:
            return self._error(
                command, ApprovalRequired.code, "the actual user event differs from this challenge"
            )
        interaction = UserInteraction(uuid4().hex, session.session_id, challenge_id, input_digest)
        return self.dispatch(
            command, Session(session.session_id, session.entry_kind, True, interaction)
        )

    def _error(self, command: Command, code: str, message: str) -> Response:
        return Response(
            request_id=command.request_id,
            instance_id=self.instance_id,
            project_id=command.project_id,
            binding_revision=command.binding_revision,
            intent_id=command.intent_id,
            error=ErrorDTO(
                code=code,
                message=self._safe_error_text(message),
                retryable=code == "WORKSPACE_IN_USE",
                next_step=(
                    "写入入口繁忙，请稍后保留原业务意图重试。"
                    if code == "WORKSPACE_IN_USE"
                    else "核对当前状态和诊断后继续；结果未知的动作先核实原意图。"
                ),
                request_id=command.request_id,
                intent_id=command.intent_id,
            ),
        )

    def _transaction(self, command: Command, session: Session) -> Response:
        try:
            if command.action == "recover":
                # 恢复是只读核实，不需要活动事务归属。
                result = self._call_transaction_port(
                    command,
                    action="recover",
                    owner_request_id=command.request_id,
                )
                return self._transaction_response(command, result)

            if command.action == "begin":
                active = self._active_transactions.get(session.origin)
                if active is not None:
                    return self._error(
                        command,
                        "TRANSACTION_ALREADY_ACTIVE",
                        "another transaction is already open in this session",
                    )
                result = self._call_transaction_port(
                    command,
                    action="begin",
                    owner_request_id=command.request_id,
                )
                if not isinstance(result, Response) or result.error is None:
                    # Keep ownership even if projecting a successful begin raises:
                    # the acquired transaction still needs an exact rollback.
                    self._active_transactions[session.origin] = _TransactionOwnership(
                        begin_request_id=command.request_id,
                        project_id=command.project_id,
                    )
                return self._transaction_response(command, result)

            # commit / rollback：传输请求幂等（command.request_id）与事务
            # 归属（begin 句柄）分离。必须持有活动事务，且 parameters 中
            # 携带的 begin_request_id 必须与归属一致（A-14）。
            active = self._active_transactions.get(session.origin)
            if active is None:
                return self._error(
                    command,
                    "NO_ACTIVE_TRANSACTION",
                    "commit/rollback requires an open transaction owned by this session",
                )
            referenced = command.parameters.get(BEGIN_REQUEST_ID_PARAM)
            if referenced != active.begin_request_id:
                return self._error(
                    command,
                    "TRANSACTION_NOT_OWNED",
                    "begin_request_id does not match the open transaction",
                )
            if command.project_id != active.project_id:
                return self._error(
                    command,
                    "TRANSACTION_PROJECT_MISMATCH",
                    "commit/rollback project differs from begin project",
                )
            result = self._call_transaction_port(
                command,
                action=command.action,
                owner_request_id=active.begin_request_id,
            )
            response = self._transaction_response(command, result)
            if response.error is None:
                # 事务结束（提交或回滚）后释放归属；后续异号 commit/rollback
                # 不再路由到已关闭事务。
                self._active_transactions.pop(session.origin, None)
            return response
        except Exception as exc:
            return self._error(
                command,
                getattr(exc, "code", "INTERNAL_ERROR"),
                self._safe_message(exc),
            )

    def _call_transaction_port(
        self,
        command: Command,
        *,
        action: str,
        owner_request_id: str,
    ) -> object:
        method = getattr(self.transaction_port, action)
        kwargs: dict[str, object] = {
            # 事务归属以 begin 的 request_id 为准，而不是结束命令自己的
            # 传输 request_id；同句柄重传幂等，异号请求不得接管。
            "request_id": owner_request_id,
            "workspace_id": self.workspace_id,
        }
        if action == "begin":
            kwargs.update(
                project_id=command.project_id,
                intent_id=command.intent_id,
            )
        return method(**kwargs)

    def _transaction_response(self, command: Command, result: object) -> Response:
        if isinstance(result, Response):
            # A port DTO is still untrusted material. Only its safe facts cross
            # the boundary; request/instance/owner identity belongs to this core.
            facts = self.safe_projection(
                {
                    "result": result.result,
                    "page": result.page.model_dump() if result.page is not None else None,
                    "error": result.error.model_dump() if result.error is not None else None,
                }
            )
            response = Response.model_validate(
                dict(facts)
                | {
                    "request_id": command.request_id,
                    "instance_id": self.instance_id,
                }
            )
            payload = response.result
            error = response.error
            page = response.page
        else:
            material = result if isinstance(result, Mapping) else {"result": result}
            payload = cast(dict[str, JsonValue], dict(self.safe_projection(material)))
            error, page = None, None
        if command.action == "begin" and error is None:
            payload = dict(payload or {})
            payload[BEGIN_REQUEST_ID_PARAM] = command.request_id
        if error is not None:
            error = error.model_copy(
                update={"request_id": command.request_id, "intent_id": command.intent_id}
            )
        return Response(
            request_id=command.request_id,
            instance_id=self.instance_id,
            workspace_id=self.workspace_id,
            project_id=command.project_id,
            intent_id=command.intent_id,
            binding_revision=command.binding_revision,
            result=payload,
            page=page,
            error=error,
        )

    def safe_projection(self, value: Mapping[str, object]) -> Mapping[str, object]:
        """Return a redacted projection; credentials never leave this adapter."""
        if self.credential_projector is not None:
            projected = self.credential_projector(value)
            if not isinstance(projected, Mapping):
                raise ValueError("credential projection did not return a safe mapping")
            redacted, _changed = redact_structure(projected)
            return cast(Mapping[str, object], redacted)
        if self.projector is not None:
            return self.projector(value)
        redacted, _changed = redact_structure(value)
        return cast(Mapping[str, object], redacted)

    def _connect(self, command: Command) -> Response:
        assert self.connector is not None
        retries_completed = 0
        while True:
            error_message: str | None = None
            result: object = None
            error_kind: str | None = None
            elapsed_ms = 0
            try:
                result = self.connector()
            except Exception as exc:  # 连接探测允许归一失败，不允许抛出协议外
                error_message = self._safe_message(exc)
            # 管道/端点未就绪时连接器返回 None/falsy，同样属于未连接，
            # 必须按重试策略继续，而不是直接报告 connected=False 成功返回。
            # 富事实（TransportFact）以 reachable 为准——dataclass 实例本身
            # 恒为真值，直接 bool() 会把不可达事实误判为可达（A-10）。
            reachability = getattr(result, "reachable", result) if result is not None else False
            connected = reachability is True
            if type(reachability) is not bool:
                error_message = "connection probe returned an invalid reachability fact"
            reported_kind = getattr(result, "error_kind", None)
            if reported_kind is not None and (connected or not isinstance(reported_kind, str)):
                connected = False
                error_message = "connection probe returned contradictory error metadata"
            if connected:
                # 连接器可返回富事实（TransportFact）；普通布尔/falsy 结果兼容。
                duration = getattr(result, "elapsed_ms", 0)
                if type(duration) is not int or duration < 0:
                    connected = False
                    error_message = "connection probe returned an invalid duration fact"
                else:
                    elapsed_ms = duration
            else:
                error_kind = getattr(result, "error_kind", None)
            self.connection_state = {
                "connected": connected,
                "attempts": retries_completed + 1,
                "last_error": None if connected else (error_message or "目标未就绪"),
                "last_error_kind": error_kind,
                "elapsed_ms": elapsed_ms,
            }
            if self._connection_persistence is not None:
                # 每次探测事实即时落盘，核心崩溃/换实例后结论不丢（A-10）。
                try:
                    self._connection_persistence.save(  # type: ignore[attr-defined]
                        self.connection_state
                    )
                except Exception as exc:
                    reason = self._safe_message(exc)
                    self._report_gate_connection(False, reason=reason, error_kind="storage")
                    return self._error(
                        command, "INTERNAL_ERROR", "connection fact could not be saved: " + reason
                    )
            self._report_gate_connection(
                connected,
                reason=error_message or "目标未就绪",
                error_kind=error_kind,
            )
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
            delay = retry_delay(retries_completed, read_only=True, idempotency_proven=True)
            if delay is None:
                return self._error(
                    command,
                    "CONNECTIVITY_FAILED",
                    error_message or "连接目标持续未就绪",
                )
            # 按应用层统一重试节奏退避；原实现拿到 delay 却空转，是忙等缺陷。
            self._sleeper(delay)
            retries_completed += 1

    def _safe_message(self, exc: Exception) -> str:
        text = str(exc).replace("\r", " ").replace("\n", " ")
        text = self._safe_error_text(text)
        return text[:500] or exc.__class__.__name__

    def _safe_error_text(self, message: str) -> str:
        """Error replies use the same configured credential boundary as success replies."""
        try:
            projected = self.safe_projection({"message": message})
            text = projected.get("message")
            if not isinstance(text, str):
                return "error detail unavailable after safe projection"
            text, _changed = scrub_secret_text(text)
            return text
        except Exception:
            return "error detail unavailable after safe projection"

    def _gate_denial(
        self, action: str, parameters: Mapping[str, object] | None = None
    ) -> str | None:
        """动作级门禁：返回拒绝原因；未装门或允许时返回 None。"""
        gate = self.capability_gate
        if gate is None:
            return None
        try:
            command_check = getattr(gate, "check_command", None)
            if callable(command_check):
                decision = command_check(action, parameters or {})
            else:
                decision = gate.check(action)  # type: ignore[attr-defined]
        except Exception:
            return f"动作 {action} 的能力门禁无法核实，请先恢复该门禁"
        if getattr(decision, "allowed", False) is True:
            return None
        reason = getattr(decision, "reason", None)
        return str(reason) if reason else f"动作 {action} 依赖的外部能力当前不可用"

    def _report_gate_connection(
        self, connected: bool, *, reason: str, error_kind: str | None
    ) -> None:
        """把一次真实探测事实写入能力门；门故障不影响探测协议本身。"""
        gate = self.capability_gate
        if gate is None:
            return
        try:
            if connected:
                gate.report("connection", healthy=True)  # type: ignore[attr-defined]
            else:
                gate.report(  # type: ignore[attr-defined]
                    "connection",
                    healthy=False,
                    reason=reason,
                    classification=error_kind or "transport",
                )
        except Exception:
            return
