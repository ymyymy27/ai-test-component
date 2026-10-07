"""Bounded MCP commands forward original business intents to the same verified core."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from aitest import __version__
from aitest.application.errors import CapabilityUnavailable
from aitest.bootstrap import acquire_existing_endpoint
from aitest.contracts.commands import HUMAN_ACTIONS, Command
from aitest.contracts.queries import QuerySpec
from aitest.contracts.responses import Response
from aitest.domain.json_material import decode_json
from aitest.interfaces.local.core_client import MAX_COMMAND_BYTES, CoreClient

_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
_QUERY_FIELDS = {"aggregate_kind", "record_id", "limit", "cursor"}
_MAX_REQUESTS = 4096
_WRITE_FIELDS = {"action", "intent_id", "expected_revision", "target", "parameters"}
_WRITE_ACTIONS = frozenset({
    "save_context", "save_environment", "save_dependency_graph", "save_case",
    "save_acceptance", "save_task", "save_delivery",
}) - HUMAN_ACTIONS
_WRITE_CONTRACT = "aitest.record-write-intent/1.0"


class _RpcError(ValueError):
    def __init__(self, code: int, message: str) -> None:
        self.code, self.message = code, message


class McpRelay:
    def __init__(
        self,
        client: CoreClient,
        project_id: str,
        binding_id: str,
        *,
        client_factory: Callable[[], CoreClient] | None = None,
    ) -> None:
        for identity in (project_id, binding_id):
            if (
                not identity.strip()
                or len(identity) > 128
                or any(ord(char) < 32 for char in identity)
            ):
                raise CapabilityUnavailable("MCP requires explicit project and binding identities")
        self.client, self.project_id, self.binding_id = client, project_id, binding_id
        self._client_factory: Callable[[], CoreClient] | None = None
        self.initialized = False
        self.ready = False
        self.version = _VERSIONS[-1]
        self._seen: set[str | int] = set()
        health = self._send("doctor", {})
        actions = (health.result or {}).get("supported_actions")
        if health.error is not None or not isinstance(actions, list) or "query" not in actions:
            raise CapabilityUnavailable("Core diagnostics or finite query capability unavailable")
        if (len(actions) > 256 or any(not isinstance(action, str) or not action.strip()
                                     or len(action) > 128 or any(ord(c) < 32 for c in action)
                                     for action in actions)
                or len(set(actions)) != len(actions)):
            raise CapabilityUnavailable("Core action catalog cannot be verified")
        contracts = (health.result or {}).get("intent_contracts", {})
        if not isinstance(contracts, dict):
            # A missing/unknown write contract cannot disable the independent read tools.
            contracts = {}
        self._write_actions = tuple(sorted(
            action for action in _WRITE_ACTIONS & set(actions)
            if contracts.get(action) == _WRITE_CONTRACT
        ))
        self.binding_revision = self._binding_revision()
        self._client_factory = client_factory
        if client_factory is not None:
            client.close()

    def _send(
        self, action: str, parameters: dict[str, object], *, binding: bool = False
    ) -> Response:
        command = Command.model_validate(
            {
                "request_id": "mcp-" + uuid4().hex,
                "action": action,
                "project_id": self.project_id,
                "binding_revision": self.binding_revision if binding else None,
                "parameters": parameters,
            }
        )
        return self._send_command(command)

    def _send_command(self, command: Command) -> Response:
        client = self.client if self._client_factory is None else self._client_factory()
        try:
            if client.endpoint.workspace_id != self.client.endpoint.workspace_id:
                raise CapabilityUnavailable("MCP workspace identity changed")
            return client.send(command)
        finally:
            if self._client_factory is not None:
                client.close()

    def _binding_revision(self) -> int:
        response = self._send(
            "query",
            {
                "aggregate_kind": "binding",
                "record_id": self.binding_id,
                "limit": 1,
            },
        )
        items = (response.result or {}).get("items")
        if response.error is not None or not isinstance(items, list) or len(items) != 1:
            raise CapabilityUnavailable("Selected binding cannot be verified in this project")
        row = items[0]
        revision = row.get("revision") if isinstance(row, dict) else None
        if (
            not isinstance(row, dict)
            or row.get("aggregate_kind") != "binding"
            or row.get("record_id") != self.binding_id
            or type(revision) is not int
            or revision < 1
        ):
            raise CapabilityUnavailable("Selected binding receipt cannot be verified")
        return revision

    def _tools(self) -> list[dict[str, object]]:
        query = QuerySpec.model_json_schema()
        query["properties"] = {
            name: value for name, value in query["properties"].items() if name in _QUERY_FIELDS
        }
        query.pop("required", None)
        query.pop("$defs", None)
        tools: list[dict[str, object]] = [
            {
                "name": "aitest_doctor",
                "description": "读取同一核心诊断；READY不表示业务通过",
                "inputSchema": {"type": "object", "additionalProperties": False},
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "aitest_query",
                "description": "读取所选项目的有限记录摘要页",
                "inputSchema": query,
                "annotations": {"readOnlyHint": True},
            },
        ]
        if self._write_actions:
            command = Command.model_json_schema()
            command["properties"] = {
                name: value for name, value in command["properties"].items()
                if name in _WRITE_FIELDS
            }
            command["properties"]["action"]["enum"] = list(self._write_actions)
            for name in ("intent_id", "expected_revision"):
                field = command["properties"][name]
                field.pop("default", None)
                field["anyOf"] = [choice for choice in field["anyOf"]
                                   if choice.get("type") != "null"]
            command["required"] = ["action", "intent_id", "expected_revision"]
            tools.append({
                "name": "aitest_dispatch",
                "description": "保存所选项目业务材料；保留原意图重传，回执未知先核实原意图",
                "inputSchema": command,
                "annotations": {"readOnlyHint": False, "destructiveHint": True,
                                "idempotentHint": True},
            })
        return tools

    @staticmethod
    def _valid_id(value: object) -> bool:
        return type(value) is int or (isinstance(value, str) and 0 < len(value) <= 128)

    def handle(self, raw: bytes) -> dict[str, object] | None:
        request_id: str | int | None = None
        notification = False
        try:
            try:
                message = decode_json(raw)
            except ValueError as error:
                raise _RpcError(-32700, "Invalid UTF-8 JSON") from error
            if not isinstance(message, dict):
                raise _RpcError(-32600, "JSON-RPC message requires an object")
            value = message.get("id")
            if self._valid_id(value):
                request_id = value
            if (
                message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str)
                or set(message) - {"jsonrpc", "id", "method", "params"}
                or ("id" in message and not self._valid_id(value))
            ):
                raise _RpcError(-32600, "Invalid JSON-RPC request")
            notification = "id" not in message
            method = message["method"]
            params = message.get("params", {})
            if not isinstance(params, dict):
                raise _RpcError(-32602, "Parameters require an object")
            if notification:
                if method == "notifications/initialized" and self.initialized and not params:
                    self.ready = True
                # Cancellation of a transport request cannot authorize run cancellation.
                return None
            assert request_id is not None
            if request_id in self._seen or len(self._seen) >= _MAX_REQUESTS:
                raise _RpcError(-32600, "Duplicate ID or request capacity exceeded")
            self._seen.add(request_id)
            if method == "ping":
                result: dict[str, object] = {}
            elif method == "initialize":
                if self.initialized:
                    raise _RpcError(-32600, "Session already initialized")
                version, capabilities, info = (
                    params.get("protocolVersion"),
                    params.get("capabilities"),
                    params.get("clientInfo"),
                )
                if (
                    not isinstance(version, str)
                    or not version
                    or not isinstance(capabilities, dict)
                    or not isinstance(info, dict)
                    or not isinstance(info.get("name"), str)
                    or not info["name"]
                    or not isinstance(info.get("version"), str)
                    or not info["version"]
                ):
                    raise _RpcError(-32602, "Invalid initialization parameters")
                self.version = version if version in _VERSIONS else _VERSIONS[-1]
                self.initialized = True
                result = {
                    "protocolVersion": self.version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "aitest-local", "version": __version__},
                }
            else:
                if not self.ready:
                    raise _RpcError(-32000, "Initialize and notify initialized first")
                if method == "tools/list":
                    if params:
                        raise _RpcError(-32602, "No cursor is available for this finite tool list")
                    result = {"tools": self._tools()}
                elif method == "tools/call":
                    result = self._call(params)
                else:
                    raise _RpcError(-32601, "Method unavailable")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except _RpcError as error:
            if notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": error.code, "message": error.message},
            }

    def _call(self, params: dict[str, object]) -> dict[str, object]:
        if set(params) - {"name", "arguments", "_meta"}:
            raise _RpcError(-32602, "Unknown tool parameters")
        name, arguments = params.get("name"), params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise _RpcError(-32602, "Tool arguments require an object")
        if name == "aitest_dispatch":
            return self._dispatch(arguments)
        if not isinstance(name, str) or name not in {"aitest_doctor", "aitest_query"}:
            raise _RpcError(-32602, "Tool unavailable")
        if (
            name == "aitest_doctor"
            and arguments
            or name == "aitest_query"
            and set(arguments) - _QUERY_FIELDS
        ):
            raise _RpcError(-32602, "Unknown or overridden context arguments")
        if name == "aitest_query":
            try:
                QuerySpec.model_validate({"project_id": self.project_id, **arguments})
            except ValueError as error:
                raise _RpcError(-32602, "Invalid finite query arguments") from error
        try:
            if self._binding_revision() != self.binding_revision:
                return self._tool_error(
                    "B_REPREPARE_REQUIRED", "绑定修订已变化，请重新选择MCP上下文"
                )
            response = self._send(
                "doctor" if name == "aitest_doctor" else "query", arguments, binding=True
            )
            if self._binding_revision() != self.binding_revision:
                return self._tool_error(
                    "B_REPREPARE_REQUIRED", "读取期间绑定变化，重新选择MCP上下文"
                )
        except Exception:
            return self._tool_error("CORE_RESULT_UNVERIFIED", "核心结果无法核实，不自动重传")
        return self._response_result(response)

    def _response_result(self, response: Response) -> dict[str, object]:
        content = [{"type": "text", "text": response.model_dump_json()}]
        result: dict[str, object] = {"content": content, "isError": response.error is not None}
        if self.version != "2024-11-05":
            result["structuredContent"] = response.model_dump(mode="json")
        return result

    def _dispatch(self, arguments: dict[str, object]) -> dict[str, object]:
        if (set(arguments) - _WRITE_FIELDS or arguments.get("action") not in self._write_actions
                or arguments.get("intent_id") is None
                or type(arguments.get("expected_revision")) is not int):
            raise _RpcError(-32602, "Unsupported write or overridden/missing business identity")
        try:
            command = Command.model_validate({
                **arguments, "request_id": "mcp-" + uuid4().hex,
                "project_id": self.project_id, "binding_revision": self.binding_revision,
            })
        except ValueError as error:
            raise _RpcError(-32602, "Invalid business command arguments") from error
        response: Response | None = None
        try:
            if self._binding_revision() != self.binding_revision:
                return self._tool_error("B_REPREPARE_REQUIRED", "绑定修订已变化，请重选MCP上下文")
            response = self._send_command(command)
            if self._binding_revision() == self.binding_revision:
                return self._response_result(response)
        except Exception:
            if response is None:
                return self._tool_error("CORE_RESULT_UNVERIFIED", "原意图结果无法核实，不自动重传")
        # The send returned an exactly verified Response. Later context checks cannot
        # erase a saved business fact or turn it into a claim that nothing happened.
        result = self._response_result(response)
        result["_meta"] = {"aitest/context": {"state": "reselect_required"}}
        result["content"] = [
            {"type": "text", "text": response.model_dump_json()},
            {"type": "text", "text": (
                "原核心回执已核实；绑定已变化或无法核实，"
                "请重选上下文并核对原意图。"
            )},
        ]
        return result

    @staticmethod
    def _tool_error(code: str, message: str) -> dict[str, object]:
        return {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"code": code, "message": message}, ensure_ascii=False),
                }
            ],
        }

    def serve(self, source: BinaryIO, sink: BinaryIO) -> None:
        while True:
            raw = source.readline(MAX_COMMAND_BYTES + 1)
            if not raw:
                return
            if len(raw) > MAX_COMMAND_BYTES or not raw.endswith(b"\n"):
                result = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Oversized or unterminated frame"},
                }
                sink.write(json.dumps(result).encode("utf-8") + b"\n")
                sink.flush()
                return
            response = self.handle(raw)
            if response is not None:
                encoded = json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8")
                if len(encoded) > MAX_COMMAND_BYTES:
                    encoded = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": response.get("id"),
                            "error": {"code": -32000, "message": "Response exceeds frame budget"},
                        }
                    ).encode("utf-8")
                sink.write(encoded + b"\n")
                sink.flush()


def run(
    binding_id: str, *, workspace_root: Path | None = None, project_id: str | None = None
) -> None:
    if workspace_root is None or project_id is None:
        raise CapabilityUnavailable(
            "MCP requires --workspace and --project for the selected binding"
        )
    client = CoreClient(acquire_existing_endpoint(workspace_root))
    try:
        relay = McpRelay(
            client,
            project_id,
            binding_id,
            client_factory=lambda: CoreClient(acquire_existing_endpoint(workspace_root)),
        )
        relay.serve(sys.stdin.buffer, sys.stdout.buffer)
    finally:
        client.close()
