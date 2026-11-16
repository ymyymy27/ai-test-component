"""CLI behaviour for ``delivery.draft`` against a fake HTTP model."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from aitest.cli import main

Capsys = pytest.CaptureFixture[str]

_API_KEY = "sk-draft-test-key"


class _Server(ThreadingHTTPServer):
    daemon_threads = True


@contextmanager
def _serve(content: str) -> Iterator[tuple[str, list[str]]]:
    prompts: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            prompts.append(body.decode("utf-8", errors="replace"))
            payload = json.dumps(
                {"choices": [{"message": {"content": content}}]},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = _Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", prompts
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)


def call(
    tmp_path: Path, command: str, payload: dict[str, Any], capsys: Capsys
) -> dict[str, Any]:
    input_file = tmp_path / "req.json"
    request_id = f"{command}-{uuid.uuid4().hex[:8]}"
    envelope = {"command": command, "request_id": request_id, "payload": payload}
    input_file.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    main([command, "--workspace", str(tmp_path / "ws"), "--input", str(input_file)])
    return json.loads(capsys.readouterr().out.strip())


def _setup(tmp_path: Path, capsys: Capsys, base_url: str | None = None) -> dict[str, Any]:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("下单服务说明：运行 pytest。\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='order'\n", encoding="utf-8")
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    task = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "下单验证",
            "goal": "验证下单只生成一条订单",
            "acceptance_items": [{"text": "订单列表只出现一条", "observable": True}],
        },
        capsys,
    )["data"]
    config_dir = tmp_path / "ws" / "projects" / project["project_id"]
    if base_url is not None:
        (config_dir / "project.config.json").write_text(
            json.dumps(
                {"schema_version": 1, "model": {"base_url": base_url}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return {"project": project, "task": task}


def test_delivery_draft_returns_all_sections_without_writing(
    tmp_path: Path, capsys: Capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AITEST_MODEL_KEY", _API_KEY)
    content = json.dumps(
        {
            "done": ["下单接口"],
            "run_commands": ["pytest tests/test_order.py"],
            "test_data": ["测试库"],
        },
        ensure_ascii=False,
    )
    with _serve(content) as (base_url, prompts):
        setup = _setup(tmp_path, capsys, base_url)
        result = call(
            tmp_path,
            "delivery.draft",
            {
                "project_id": setup["project"]["project_id"],
                "task_id": setup["task"]["task_id"],
                "commit": "abc123",
            },
            capsys,
        )

    assert result["ok"] is True
    draft = result["data"]["draft"]
    assert draft["done"] == ["下单接口"]
    assert draft["run_commands"] == ["pytest tests/test_order.py"]
    assert draft["test_data"] == ["测试库"]
    for field in (
        "not_done",
        "changed_modules",
        "interface_changes",
        "dependencies",
        "mocks",
        "self_test_evidence",
    ):
        assert draft[field] == []
    assert result["data"]["sources"]["readme"] == "README.md"
    assert result["data"]["sources"]["manifest"] == "pyproject.toml"

    assert prompts
    assert "下单服务说明" in prompts[0]
    assert "验证下单只生成一条订单" in prompts[0]
    assert _API_KEY not in prompts[0]

    delivery_path = (
        tmp_path / "ws" / "projects" / setup["project"]["project_id"] / "deliveries"
    )
    assert not delivery_path.exists()


def test_delivery_draft_without_model_key_is_unavailable(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    result = call(
        tmp_path,
        "delivery.draft",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
        },
        capsys,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "MODEL_UNAVAILABLE"
