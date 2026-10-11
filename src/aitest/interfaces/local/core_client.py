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


class CoreResultUnverified(RuntimeError):
    code = "CORE_RESULT_UNVERIFIED"


class CoreClient:
    def __init__(self, endpoint: CoreEndpoint) -> None:
        self.endpoint = endpoint
        self.channel = cast(CoreMessagePort, endpoint.connection)
        self.closed = False

    def send(self, command: Command) -> Response:
        if self.closed:
            raise CoreResultUnverified("核心连接已关闭")
        try:
            self.channel.write_message(command.model_dump_json().encode("utf-8"))
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
