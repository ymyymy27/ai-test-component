"""CLI/relay facade: one verified endpoint, exact receipts, no automatic retransmission."""

from contextlib import suppress
from typing import cast
from uuid import uuid4

from aitest.application.ports import CoreMessagePort
from aitest.contracts.commands import Command
from aitest.contracts.responses import Response
from aitest.domain.json_material import decode_json
from aitest.interfaces.local.editor_host import CoreEndpoint
from aitest.interfaces.local.native_confirmation import NATIVE_CONFIRMATION_VERSION
from aitest.interfaces.local.pipe import MAX_MESSAGE_BYTES

MAX_COMMAND_BYTES = MAX_MESSAGE_BYTES


class CoreResultUnverified(RuntimeError):
    code = "CORE_RESULT_UNVERIFIED"


class CoreClient:
    def __init__(self, endpoint: CoreEndpoint) -> None:
        self.endpoint = endpoint
        self.channel = cast(CoreMessagePort, endpoint.connection)
        self.closed = False

    def send(self, command: Command) -> Response:
        return self._exchange(command, command.model_dump_json().encode("utf-8"))

    def send_native(self, command: Command) -> Response:
        """Ask the verified core to show its own UI; no user-event claim crosses the pipe."""
        import json

        discovery = self.send(Command(request_id=uuid4().hex, action="doctor"))
        capabilities = (discovery.result or {}).get("transport_capabilities")
        if not isinstance(capabilities, list) or NATIVE_CONFIRMATION_VERSION not in capabilities:
            raise CoreResultUnverified("核心不支持原生确认入口，请核对安装版本并受控重启核心")
        return self._exchange(
            command,
            json.dumps(
                {
                    "__aitest_control__": NATIVE_CONFIRMATION_VERSION,
                    "request_id": command.request_id,
                    "command": command.model_dump(mode="json"),
                },
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8"),
        )

    def _exchange(self, command: Command, payload: bytes) -> Response:
        if self.closed:
            raise CoreResultUnverified("核心连接已关闭")
        if len(payload) > MAX_COMMAND_BYTES:
            raise CoreResultUnverified("核心入口消息超过传输预算")
        try:
            self.channel.write_message(payload)
            response = Response.model_validate(
                decode_json(self.channel.read_message()), strict=True
            )
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
