"""Lightweight CLI entry: one JSON request in, one JSON response out."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from aitest.commands import MUTATING, REGISTRY, cached_result, store_result
from aitest.errors import AiTestError, invalid_request
from aitest.storage.workspace import Workspace


def _parse(argv: list[str]) -> tuple[str, Path, str | None, bool]:
    if not argv:
        raise invalid_request("缺少命令", "用法：aitest <command> --workspace <dir>")
    command = argv[0]
    workspace: str | None = None
    input_file: str | None = None
    confirm = False
    index = 1
    while index < len(argv):
        item = argv[index]
        if item in {"--workspace", "-w"}:
            index += 1
            workspace = argv[index] if index < len(argv) else None
        elif item in {"--input", "-i"}:
            index += 1
            input_file = argv[index] if index < len(argv) else None
        elif item == "--confirm":
            confirm = True
        else:
            raise invalid_request(f"未知参数：{item}")
        index += 1
    if not workspace:
        raise invalid_request("缺少 --workspace", "用法：aitest <command> --workspace <dir>")
    return command, Path(workspace).expanduser().resolve(), input_file, confirm


def _read_payload(input_file: str | None) -> dict[str, Any]:
    raw: str
    if input_file:
        raw = Path(input_file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        raw = ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise invalid_request(f"输入不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise invalid_request("输入必须是 JSON 对象")
    return data


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        command, workspace_root, input_file, confirm = _parse(args)
        payload = _read_payload(input_file)
        request_id = payload.get("request_id", "")
        if not isinstance(request_id, str):
            raise invalid_request("request_id 必须是字符串")
        envelope_command = payload.get("command", command)
        if envelope_command != command:
            raise invalid_request("信封 command 与参数不一致")
        inner = payload.get("payload", {})
        if not isinstance(inner, dict):
            raise invalid_request("payload 必须是对象")
        handler = REGISTRY.get(command)
        if handler is None:
            raise invalid_request(f"未知命令：{command}", "检查命令拼写")
        workspace = Workspace(workspace_root)
        workspace.init()
        if request_id and command in MUTATING:
            cached = cached_result(workspace, request_id)
            if cached is not None:
                print(json.dumps(cached, ensure_ascii=False))
                return 0
        result = handler(workspace, inner, confirm)
        response = {"ok": True, "request_id": request_id, "data": result}
        if request_id and command in MUTATING:
            store_result(workspace, request_id, response)
        print(json.dumps(response, ensure_ascii=False))
        return 0
    except AiTestError as exc:
        response = {"ok": False, "error": exc.info.to_dict()}
        print(json.dumps(response, ensure_ascii=False))
        return 1
    except (OSError, ValueError) as exc:
        response = {
            "ok": False,
            "error": {
                "code": "INVALID_REQUEST",
                "message": str(exc),
                "next_step": "检查参数与环境后重试",
            },
        }
        print(json.dumps(response, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
