"""CLI/relay facade: one verified endpoint, exact receipts, no automatic retransmission."""

from contextlib import suppress
from typing import cast

from aitest.application.ports import CoreMessagePort
from aitest.contracts.commands import Command
from aitest.contracts.responses import Response
from aitest.domain.json_material import decode_json
from aitest.interfaces.local.editor_host import CoreEndpoint
from aitest.interfaces.local.pipe import MAX_MESSAGE_BYTES

MAX_COMMAND_BYTES = MAX_MESSAGE_BYTES
BUSINESS_RESPONSE_TIMEOUT_MS = 130_000
LONG_RUNNING_ACTIONS = frozenset({
    "analyze_project", "discover_tests", "generate_draft", "create_report", "export_report",
    "prepare_run", "register_run", "prepare_execution", "execute_step", "retry_step",
    "return_stage", "start_run", "verify_pending", "request_model_draft",
})


class CoreResultUnverified(RuntimeError):
    code = "CORE_RESULT_UNVERIFIED"


class CoreClient:
    def __init__(self, endpoint: CoreEndpoint) -> None:
        self.endpoint = endpoint
        self.channel = cast(CoreMessagePort, endpoint.connection)
        self.closed = False

    def send(self, command: Command) -> Response:
        return self._exchange(
            command, command.model_dump_json().encode("utf-8"),
            response_timeout_ms=(
                BUSINESS_RESPONSE_TIMEOUT_MS if command.action in LONG_RUNNING_ACTIONS else None
            ),
        )

    def _exchange(
        self, command: Command, payload: bytes, *, response_timeout_ms: int | None = None,
    ) -> Response:
        if self.closed:
            raise CoreResultUnverified("核心连接已关闭")
        if len(payload) > MAX_COMMAND_BYTES:
            raise CoreResultUnverified("核心入口消息超过传输预算")
        try:
            self.channel.write_message(payload)
            raw = (
                self.channel.read_message()
                if response_timeout_ms is None
                else self.channel.read_message(timeout_ms=response_timeout_ms)
            )
            response = Response.model_validate(decode_json(raw), strict=True)
            if (
                response.protocol_version != command.protocol_version
                or response.request_id != command.request_id
                or response.instance_id != self.endpoint.instance_id
                or response.workspace_id != self.endpoint.workspace_id
                or response.project_id != command.project_id
                or response.binding_revision != command.binding_revision
                or response.intent_id != command.intent_id
                or (response.result is None) == (response.error is None)
            ):
                raise ValueError("core receipt identity mismatch")
        except Exception as error:
            self.close()
            raise CoreResultUnverified("核心回执无法核实，原业务结果待核实") from error
        return response

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            with suppress(Exception):
                self.channel.close()


def read_command(raw: bytes) -> Command:
    if len(raw) > MAX_COMMAND_BYTES:
        raise ValueError("command exceeds the local frame budget")
    return Command.model_validate(decode_json(raw))
