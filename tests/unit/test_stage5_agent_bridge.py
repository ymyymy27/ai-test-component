"""步骤 5.1 跨智能体 stdio 薄适配器端到端测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SERVER = ROOT / "integrations" / "agent-bridge" / "server.py"
SKILL_MD = ROOT / "integrations" / "agent-bridge" / "SKILL.md"


def _send_ndjson(proc: subprocess.Popen[bytes], payload: dict[str, Any]) -> dict[str, Any]:
    assert proc.stdin is not None and proc.stdout is not None
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    proc.stdin.write(raw)
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, "bridge process exited without output"
    return json.loads(line.decode("utf-8"))


def _send_content_length(proc: subprocess.Popen[bytes], payload: dict[str, Any]) -> dict[str, Any]:
    assert proc.stdin is not None and proc.stdout is not None
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    proc.stdin.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii") + encoded)
    proc.stdin.flush()
    headers: dict[str, str] = {}
    while True:
        line = proc.stdout.readline()
        assert line, "bridge EOF while reading Content-Length header"
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("ascii").strip()
        if ":" in text:
            k, v = text.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers["content-length"])
    body = proc.stdout.read(length)
    return json.loads(body.decode("utf-8"))


def test_agent_bridge_ndjson_and_human_confirmation_guard(tmp_path: Path) -> None:
    ws_dir = tmp_path / "ws"
    proc = subprocess.Popen(
        [sys.executable, str(BRIDGE_SERVER), "--workspace", str(ws_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        init = _send_ndjson(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
        )
        assert init["result"]["serverInfo"]["name"] == "aitest-agent-bridge"

        # 通知不回包，紧接着发送 ping 验证不阻塞
        assert proc.stdin is not None
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        proc.stdin.flush()
        ping = _send_ndjson(proc, {"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert ping["result"] == {}

        tools = _send_ndjson(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        tool_names = [t["name"] for t in tools["result"]["tools"]]
        assert "aitest_doctor" in tool_names
        assert "aitest_project_map" in tool_names
        assert "aitest_run_start" in tool_names
        assert len(tool_names) == 10

        doctor = _send_ndjson(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "aitest_doctor", "arguments": {}},
            },
        )
        assert doctor["result"]["isError"] is False
        doc_payload = json.loads(doctor["result"]["content"][0]["text"])
        assert doc_payload["ok"] is True

        projects = _send_ndjson(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "aitest_project_list", "arguments": {}},
            },
        )
        assert projects["result"]["isError"] is False
        assert json.loads(projects["result"]["content"][0]["text"])["data"]["projects"] == []

        # 安全边界 1：缺少 human_confirmed=true 直接拒绝
        denied_unconfirmed = _send_ndjson(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "aitest_run_start",
                    "arguments": {
                        "project_id": "proj_1",
                        "plan_id": "plan_1",
                        "human_confirmed": False,
                        "confirmed_by": "张三",
                    },
                },
            },
        )
        assert denied_unconfirmed["result"]["isError"] is True
        assert "HUMAN_CONFIRMATION_REQUIRED" in denied_unconfirmed["result"]["content"][0]["text"]

        # 安全边界 2：confirmed_by 为 AI/agent 代称直接拒绝
        denied_ai_name = _send_ndjson(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "aitest_run_start",
                    "arguments": {
                        "project_id": "proj_1",
                        "plan_id": "plan_1",
                        "human_confirmed": True,
                        "confirmed_by": "ai",
                    },
                },
            },
        )
        assert denied_ai_name["result"]["isError"] is True
        assert "INVALID_CONFIRMED_BY" in denied_ai_name["result"]["content"][0]["text"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_agent_bridge_content_length_framing_and_skill_file(tmp_path: Path) -> None:
    assert SKILL_MD.is_file()
    skill_text = SKILL_MD.read_text(encoding="utf-8")
    assert "name: aitest-local-qa" in skill_text
    assert "aitest_run_start" in skill_text

    ws_dir = tmp_path / "ws_cl"
    proc = subprocess.Popen(
        [sys.executable, str(BRIDGE_SERVER), "--workspace", str(ws_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        init = _send_content_length(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
        )
        assert init["id"] == 10
        assert init["result"]["capabilities"]["tools"] == {"listChanged": False}

        unknown = _send_content_length(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "nonexistent_tool", "arguments": {}},
            },
        )
        assert unknown["result"]["isError"] is True
        assert "UNKNOWN_TOOL" in unknown["result"]["content"][0]["text"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)
