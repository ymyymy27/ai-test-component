"""Behaviour tests for checklist items C1-C4 and D4-D5 (2026-11-16)."""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import uuid
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from aitest.cli import main
from aitest.core.evidence import DEFAULT_FILE_CAP_BYTES, save_evidence
from aitest.models import Run, Scope
from aitest.runners.http_runner import run_http_sequence
from aitest.storage.files import read_json, write_model
from aitest.storage.workspace import Workspace

Capsys = pytest.CaptureFixture[str]

_JSONL_FIELDS = {
    "request_id": "trace_id",
    "module": "logger",
    "status": "level",
    "message": "msg",
}


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)
    monkeypatch.delenv("AITEST_GITHUB_TOKEN", raising=False)


def call(
    tmp_path: Path,
    command: str,
    payload: dict[str, Any],
    capsys: Capsys,
    confirm: bool = False,
) -> dict[str, Any]:
    request_id = payload.pop("request_id", f"{command}-{uuid.uuid4().hex[:8]}")
    return call_with_payload(tmp_path, command, payload, capsys, confirm, request_id)


def call_with_payload(
    tmp_path: Path,
    command: str,
    payload: dict[str, Any],
    capsys: Capsys,
    confirm: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Like ``call`` but keeps a payload field literally named request_id."""
    workspace = tmp_path / "ws"
    envelope = {
        "command": command,
        "request_id": request_id or f"{command}-{uuid.uuid4().hex[:8]}",
        "payload": payload,
    }
    input_file = tmp_path / "req.json"
    input_file.write_text(json.dumps(envelope), encoding="utf-8")
    argv = [command, "--workspace", str(workspace), "--input", str(input_file)]
    if confirm:
        argv.append("--confirm")
    main(argv)
    return json.loads(capsys.readouterr().out.strip())


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "ws")


def _setup(
    tmp_path: Path, capsys: Capsys, items: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    task = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "下单验证",
            "acceptance_items": items
            or [{"text": "下单后列表出现订单", "observable": True}],
        },
        capsys,
    )["data"]
    return {"project": project, "task": task}


def _plan(
    tmp_path: Path, capsys: Capsys, setup: dict[str, Any], cases: list[dict[str, Any]]
) -> dict[str, Any]:
    item_ids = [item["item_id"] for item in setup["task"]["acceptance_items"]]
    for index, case in enumerate(cases):
        case.setdefault("acceptance_item_id", item_ids[min(index, len(item_ids) - 1)])
    return call(
        tmp_path,
        "plan.generate",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": item_ids},
            "cases": cases,
        },
        capsys,
    )["data"]


def _case(
    item_id: str,
    method: str,
    *,
    layer: str = "L2",
    script_name: str | None = None,
    script_content: str | None = None,
    depends_on: str | None = None,
) -> dict[str, Any]:
    case: dict[str, Any] = {
        "acceptance_item_id": item_id,
        "layer": layer,
        "expected": "用例成功",
        "expectation_source": "下单后列表出现订单",
        "preconditions": ["被测项目目录可访问"],
        "verify_method": method,
    }
    if depends_on is not None:
        case["depends_on"] = depends_on
    if script_name is not None:
        case["script_name"] = script_name
    if script_content is not None:
        case["script_content"] = script_content
    return case


def _wait_completed(
    tmp_path: Path, capsys: Capsys, project_id: str, run_id: str, attempts: int = 900
) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for _ in range(attempts):
        status = call(
            tmp_path,
            "run.status",
            {"project_id": project_id, "run_id": run_id},
            capsys,
        )
        if "data" not in status:
            raise AssertionError(status)
        if status["data"]["run"]["status"] != "running":
            return status
        time.sleep(0.1)
    return status


def _run_plan(
    tmp_path: Path, capsys: Capsys, setup: dict[str, Any], plan: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    started = call(
        tmp_path,
        "run.start",
        {"project_id": setup["project"]["project_id"], "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )
    assert started["ok"] is True, started
    run_id = started["data"]["run_id"]
    _wait_completed(tmp_path, capsys, setup["project"]["project_id"], run_id)
    ws = _workspace(tmp_path)
    run = read_json(
        ws.project_dir(setup["project"]["project_id"]) / "runs" / run_id / "run.json"
    )
    return run_id, run


def _write_config(ws: Workspace, project_id: str, data: dict[str, Any]) -> None:
    (ws.project_dir(project_id) / "project.config.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


class _RecordingServer(ThreadingHTTPServer):
    seen: list[dict[str, str]]

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, _Handler)
        self.seen = []


class _Handler(BaseHTTPRequestHandler):
    server: _RecordingServer

    def do_GET(self) -> None:  # noqa: N802 - handler API
        self.server.seen.append({key: value for key, value in self.headers.items()})
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture()
def http_server() -> Iterator[_RecordingServer]:
    server = _RecordingServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _base_url(server: _RecordingServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


# -- C1 backup ---------------------------------------------------------------


def test_backup_manifest_records_sha256_and_restore_reports_missing_evidence(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    run_dir = ws.project_dir(project_id) / "runs" / "run_ev1"
    (run_dir / "evidence").mkdir(parents=True)
    (run_dir / "evidence" / "run.log").write_text("log", encoding="utf-8")
    write_model(
        run_dir / "run.json",
        Run(
            run_id="run_ev1",
            plan_id="plan_x",
            project_id=project_id,
            scope=Scope(),
            status="completed",
            started_at="2026-01-01T00:00:00+00:00",
            evidence_refs=["evidence/run.log"],
        ),
    )
    report_dir = ws.project_dir(project_id) / "reports"
    report_dir.mkdir(parents=True)
    (report_dir / "rep_x.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "report_id": "rep_x",
                "run_id": "run_ev1",
                "evidence_refs": ["evidence/run.log", "db:req-1:1"],
                "evidence_index": [
                    {"ref": "evidence/run.log", "exists": True, "bytes": 3}
                ],
            }
        ),
        encoding="utf-8",
    )

    archive = tmp_path / "backup.zip"
    created = call(tmp_path, "backup.create", {"path": str(archive)}, capsys)
    assert created["ok"] is True
    assert created["data"]["file_count"] == created["data"]["files"]
    with zipfile.ZipFile(archive) as handle:
        manifest = json.loads(handle.read("manifest.json"))
        assert manifest["file_count"] == len(manifest["files"])
        assert set(manifest["files"]) == set(manifest["sha256"])
        for name in manifest["files"]:
            assert manifest["sha256"][name] == hashlib.sha256(handle.read(name)).hexdigest()
        assert not any(name.endswith("evidence/run.log") for name in manifest["files"])

    restored = call(
        tmp_path,
        "backup.restore",
        {"path": str(archive), "target": str(tmp_path / "restored")},
        capsys,
    )
    assert restored["ok"] is True, restored
    data = restored["data"]
    assert data["verified"] is True
    assert data["missing_evidence"] == ["evidence/run.log"]
    assert "db:req-1:1" not in data["missing_evidence"]
    assert "证据未包含" in data["notes"]


def test_backup_restore_with_evidence_reports_no_missing_refs(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    run_dir = ws.project_dir(project_id) / "runs" / "run_ev2"
    (run_dir / "evidence").mkdir(parents=True)
    (run_dir / "evidence" / "run.log").write_text("log", encoding="utf-8")
    write_model(
        run_dir / "run.json",
        Run(
            run_id="run_ev2",
            plan_id="plan_x",
            project_id=project_id,
            scope=Scope(),
            status="completed",
            started_at="2026-01-01T00:00:00+00:00",
            evidence_refs=["evidence/run.log"],
        ),
    )
    archive = tmp_path / "full.zip"
    created = call(
        tmp_path,
        "backup.create",
        {"path": str(archive), "include_evidence": True},
        capsys,
    )
    assert created["data"]["includes_evidence"] is True
    restored = call(
        tmp_path,
        "backup.restore",
        {"path": str(archive), "target": str(tmp_path / "restored-full")},
        capsys,
    )
    assert restored["ok"] is True, restored
    assert restored["data"]["missing_evidence"] == []
    assert restored["data"]["notes"] == ""


def test_backup_restore_rejects_tampered_member(tmp_path: Path, capsys: Capsys) -> None:
    _setup(tmp_path, capsys)
    archive = tmp_path / "backup.zip"
    assert call(tmp_path, "backup.create", {"path": str(archive)}, capsys)["ok"] is True

    with zipfile.ZipFile(archive) as handle:
        items = {name: handle.read(name) for name in handle.namelist()}
    target_name = next(name for name in items if name.endswith("/project.json"))
    items[target_name] = items[target_name] + b"\n"
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "w") as handle:
        for name, data in items.items():
            handle.writestr(name, data)

    restored = call(
        tmp_path,
        "backup.restore",
        {"path": str(tampered), "target": str(tmp_path / "restored")},
        capsys,
    )
    assert restored["ok"] is False
    assert restored["error"]["code"] == "BACKUP_INVALID"
    assert not (tmp_path / "restored" / "workspace.json").exists()


def test_backup_restore_rejects_path_traversal(tmp_path: Path, capsys: Capsys) -> None:
    _setup(tmp_path, capsys)
    crafted = tmp_path / "crafted.zip"
    with zipfile.ZipFile(crafted, "w") as handle:
        handle.writestr("../escape.txt", "x")
        handle.writestr(
            "manifest.json",
            json.dumps({"file_count": 0, "files": [], "sha256": {}}),
        )
    restored = call(
        tmp_path,
        "backup.restore",
        {"path": str(crafted), "target": str(tmp_path / "restored")},
        capsys,
    )
    assert restored["ok"] is False
    assert restored["error"]["code"] == "BACKUP_INVALID"
    assert not (tmp_path / "escape.txt").exists()


# -- C2 data cleanup ---------------------------------------------------------


def _cleanup_scripts(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    dry = scripts / "dry_run.py"
    dry.write_text(
        "import json, pathlib\n"
        "state = pathlib.Path('data/state.json')\n"
        "rows = json.loads(state.read_text(encoding='utf-8'))['rows']\n"
        "pathlib.Path('dry.marker').write_text('dry', encoding='utf-8')\n"
        "print(json.dumps({'rows': len(rows)}))\n",
        encoding="utf-8",
    )
    clean = scripts / "cleanup.py"
    clean.write_text(
        "import json, pathlib, sys\n"
        "state = pathlib.Path('data/state.json')\n"
        "data = json.loads(state.read_text(encoding='utf-8'))\n"
        "prefix = sys.argv[1]\n"
        "kept = [row for row in data['rows'] if not row['marker'].startswith(prefix)]\n"
        "removed = len(data['rows']) - len(kept)\n"
        "data['rows'] = kept\n"
        "state.write_text(json.dumps(data), encoding='utf-8')\n"
        "pathlib.Path('cleanup.marker').write_text(prefix, encoding='utf-8')\n"
        "print(json.dumps({'rows': removed}))\n",
        encoding="utf-8",
    )
    return dry, clean


def test_data_cleanup_requires_configuration(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    result = call(
        tmp_path,
        "data.cleanup",
        {"project_id": setup["project"]["project_id"]},
        capsys,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"
    assert "project.config.json" in result["error"]["next_step"]


def test_data_cleanup_dry_run_then_confirm(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    root = tmp_path / "repo"
    (root / "data").mkdir()
    (root / "data" / "state.json").write_text(
        json.dumps({"rows": [{"marker": "run_abc_1"}, {"marker": "other_1"}]}),
        encoding="utf-8",
    )
    dry, clean = _cleanup_scripts(tmp_path)
    _write_config(
        ws,
        project_id,
        {
            "data": {
                "cleanup_dry_run": f'"{sys.executable}" "{dry}"',
                "cleanup": f'"{sys.executable}" "{clean}" {{run_id}}',
            }
        },
    )

    preview = call(tmp_path, "data.cleanup", {"project_id": project_id}, capsys)
    assert preview["ok"] is True, preview
    assert preview["data"]["dry_run"] is True
    assert preview["data"]["executed"] is False
    assert "2" in preview["data"]["stdout"]
    assert (root / "dry.marker").is_file()
    assert not (root / "cleanup.marker").exists()
    assert len(json.loads((root / "data" / "state.json").read_text())["rows"]) == 2

    denied = call(
        tmp_path,
        "data.cleanup",
        {"project_id": project_id, "dry_run": False, "run_id": "run_abc"},
        capsys,
    )
    assert denied["ok"] is False
    assert denied["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert not (root / "cleanup.marker").exists()

    applied = call(
        tmp_path,
        "data.cleanup",
        {"project_id": project_id, "dry_run": False, "run_id": "run_abc"},
        capsys,
        confirm=True,
    )
    assert applied["ok"] is True, applied
    assert applied["data"]["executed"] is True
    assert "1" in applied["data"]["stdout"]
    assert (root / "cleanup.marker").read_text(encoding="utf-8") == "run_abc"
    rows = json.loads((root / "data" / "state.json").read_text())["rows"]
    assert rows == [{"marker": "other_1"}]

    log_files = list((ws.root / "actions").glob("*.jsonl"))
    assert len(log_files) == 1
    entries = [json.loads(line) for line in log_files[0].read_text().splitlines()]
    assert [entry["phase"] for entry in entries] == [
        "started",
        "finished",
        "started",
        "finished",
    ]
    assert entries[1]["dry_run"] is True
    assert entries[1]["run_id"] == ""
    assert entries[3]["run_id"] == "run_abc"
    assert entries[3]["exit_code"] == 0
    for entry in entries:
        assert {"at", "command", "exit_code", "run_id", "dry_run", "phase"} <= set(entry)


# -- C3/C4 evidence budget and data marker -----------------------------------


def test_evidence_budget_keeps_failed_and_skips_passed(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        items=[
            {"text": "失败路径留下证据", "observable": True},
            {"text": "成功路径释放证据", "observable": True},
        ],
    )
    item_ids = [item["item_id"] for item in setup["task"]["acceptance_items"]]
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    _write_config(ws, project_id, {"evidence": {"media_budget_mb": 0.002, "video": False}})
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [
            _case(
                item_ids[0],
                "script:fail.py",
                script_name="fail.py",
                script_content="print('x' * 4000)\nraise SystemExit(1)\n",
            ),
            _case(
                item_ids[1],
                "script:pass.py",
                script_name="pass.py",
                script_content="print('y' * 4000)\n",
            ),
        ],
    )
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)

    failed_case, passed_case = run["cases"][0], run["cases"][1]
    assert failed_case["result"] == "failed"
    assert failed_case["evidence_refs"]
    for ref in failed_case["evidence_refs"]:
        assert (ws.project_dir(project_id) / "runs" / run_id / ref).is_file()
    assert passed_case["result"] == "passed"
    assert passed_case["evidence_refs"] == []
    markers = list(
        (ws.project_dir(project_id) / "runs" / run_id / "evidence").glob(
            f"{passed_case['case_id']}-*.skipped"
        )
    )
    assert markers
    assert "预算" in markers[0].read_text(encoding="utf-8")
    assert run["media_bytes"] >= 4000


def test_evidence_file_cap_truncates(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    ref = save_evidence(run_dir, "big.log", b"x" * 2048, file_cap_bytes=1024)
    assert ref == "evidence/big.log"
    assert len((run_dir / ref).read_bytes()) == 1024
    assert (run_dir / "evidence" / "big.log.truncated").is_file()
    assert DEFAULT_FILE_CAP_BYTES == 20 * 1024 * 1024


def test_run_injects_test_data_markers(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    script = (
        "import os\n"
        "print(os.environ.get('AITEST_RUN_ID'))\n"
        "print(os.environ.get('AITEST_DATA_PREFIX'))\n"
    )
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [
            _case(
                item_id,
                "script:env.py",
                script_name="env.py",
                script_content=script,
            )
        ],
    )
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)

    assert run["status"] == "completed"
    assert run["data_prefix"] == f"aitest_{run_id}_"
    ref = run["cases"][0]["evidence_refs"][0]
    output = (ws.project_dir(project_id) / "runs" / run_id / ref).read_text(encoding="utf-8")
    lines = output.splitlines()
    assert run_id in lines
    assert f"aitest_{run_id}_" in lines


# -- D4 service pre-probe ----------------------------------------------------


def _marker_script(scripts: Path, name: str) -> Path:
    script = scripts / name
    script.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    return script


def test_worker_uses_already_healthy_service_without_taking_over(
    tmp_path: Path, capsys: Capsys, http_server: _RecordingServer
) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    start_script = _marker_script(scripts, "start_service.py")
    stop_script = _marker_script(scripts, "stop_service.py")
    started_marker = tmp_path / "service-started.txt"
    stopped_marker = tmp_path / "service-stopped.txt"
    _write_config(
        ws,
        project_id,
        {
            "services": {
                "health": f"{_base_url(http_server)}/health",
                "start": f'"{sys.executable}" "{start_script}" "{started_marker}"',
                "stop": f'"{sys.executable}" "{stop_script}" "{stopped_marker}"',
                "timeout_seconds": 10,
            }
        },
    )
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [_case(item_id, "command: echo ok")],
    )
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)

    assert run["status"] == "completed"
    assert run["service_started"] is False
    assert run["cases"][0]["result"] == "passed"
    assert not started_marker.exists()
    assert not stopped_marker.exists()
    assert (ws.project_dir(project_id) / "runs" / run_id / "run.json").is_file()


# -- D5 link window and request id header ------------------------------------


def test_http_runner_injects_request_id_header(http_server: _RecordingServer) -> None:
    result = run_http_sequence(
        [{"url": f"{_base_url(http_server)}/ok", "method": "GET"}],
        request_id_header="X-Aitest-Request-Id",
        request_id="rid-1",
    )
    assert result.status == "passed"
    assert http_server.seen[0].get("X-Aitest-Request-Id") == "rid-1"


def test_worker_records_request_id_and_sends_header(
    tmp_path: Path, capsys: Capsys, http_server: _RecordingServer
) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    requests = [
        {
            "url": f"{_base_url(http_server)}/ok",
            "method": "GET",
            "expect": [{"path": "status", "value": 200}],
        }
    ]
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [_case(item_id, "http:" + json.dumps(requests))],
    )
    case_id = read_json(
        ws.project_dir(project_id) / "plans" / f"{plan['plan_id']}.json"
    )["cases"][0]["case_id"]
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)

    expected = f"{run_id}-{case_id}"
    assert run["request_ids"] == [expected]
    assert http_server.seen[0].get("X-Request-Id") == expected


def test_link_import_filters_logs_to_run_window(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    log_path = tmp_path / "app.jsonl"
    log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "trace_id": "req-1",
                        "logger": "api",
                        "level": "error",
                        "msg": "inside",
                        "timestamp": "2026-01-01T00:04:00Z",
                    }
                ),
                json.dumps(
                    {
                        "trace_id": "req-1",
                        "logger": "api",
                        "level": "error",
                        "msg": "outside",
                        "timestamp": "2026-01-01T00:20:00Z",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    _write_config(
        ws,
        project_id,
        {
            "link": {
                "log_paths": [str(log_path)],
                "log_format": "jsonl",
                "log_fields": _JSONL_FIELDS,
                "window_seconds": 300,
            }
        },
    )
    run_dir = ws.project_dir(project_id) / "runs" / "run_window"
    run_dir.mkdir(parents=True)
    write_model(
        run_dir / "run.json",
        Run(
            run_id="run_window",
            plan_id="plan_x",
            project_id=project_id,
            scope=Scope(),
            status="completed",
            started_at="2026-01-01T00:05:00+00:00",
        ),
    )

    imported = call_with_payload(
        tmp_path,
        "link.import",
        {"project_id": project_id, "run_id": "run_window", "request_id": "req-1"},
        capsys,
    )
    assert imported["ok"] is True, imported
    assert [node["message"] for node in imported["data"]["nodes"]] == ["inside"]
    assert any("运行时间窗" in gap["reason"] for gap in imported["data"]["gaps"])
    stored = read_json(run_dir / "link.json")
    assert [node["message"] for node in stored["nodes"]] == ["inside"]



# -- D1 module scope ---------------------------------------------------------


def test_task_module_scope_filters_acceptance_items_and_cases(
    tmp_path: Path, capsys: Capsys
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    project_id = project["project_id"]
    orders = call(
        tmp_path, "module.add", {"project_id": project_id, "name": "orders"}, capsys
    )["data"]["module_id"]
    billing = call(
        tmp_path, "module.add", {"project_id": project_id, "name": "billing"}, capsys
    )["data"]["module_id"]
    task = call(
        tmp_path,
        "task.add",
        {
            "project_id": project_id,
            "title": "下单验证",
            "acceptance_items": [
                {"text": "下单后列表出现订单", "module_id": orders},
                {"text": "账单金额正确", "module_id": billing},
            ],
        },
        capsys,
    )["data"]
    item_orders, item_billing = [item["item_id"] for item in task["acceptance_items"]]
    assert [item["module_id"] for item in task["acceptance_items"]] == [orders, billing]

    rejected = call(
        tmp_path,
        "task.add",
        {
            "project_id": project_id,
            "title": "未登记模块",
            "acceptance_items": [{"text": "失败", "module_id": "mod_missing"}],
        },
        capsys,
    )
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "INVALID_REQUEST"

    cases = [
        _case(item_orders, "command: echo orders"),
        _case(item_billing, "command: echo billing"),
    ]
    filtered = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": project_id,
            "task_id": task["task_id"],
            "scope": {
                "preset": "selected",
                "module_ids": [orders],
                "acceptance_item_ids": [item_orders],
            },
            "cases": cases,
        },
        capsys,
    )["data"]
    # The module filter helps pick items; it must not shrink the frozen M.
    assert filtered["coverage"] == {"selected": 1, "applicable": 2}
    assert filtered["scope"]["acceptance_item_ids"] == [item_orders]
    ws = _workspace(tmp_path)
    stored = read_json(
        ws.project_dir(project_id) / "plans" / f"{filtered['plan_id']}.json"
    )
    assert [case["acceptance_item_id"] for case in stored["cases"]] == [item_orders]

    unfiltered = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": project_id,
            "task_id": task["task_id"],
            "scope": {"preset": "all_applicable"},
            "cases": cases,
        },
        capsys,
    )["data"]
    assert unfiltered["coverage"] == {"selected": 2, "applicable": 2}
    assert set(unfiltered["scope"]["acceptance_item_ids"]) == {item_orders, item_billing}

    unknown_scope = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": project_id,
            "task_id": task["task_id"],
            "scope": {"preset": "all_applicable", "module_ids": ["mod_missing"]},
            "cases": cases,
        },
        capsys,
    )
    assert unknown_scope["ok"] is False
    assert unknown_scope["error"]["code"] == "INVALID_REQUEST"


# -- D3 project-level L1 checks ---------------------------------------------


def _l1_config(build_command: str, entries: list[str] | None = None) -> dict[str, Any]:
    return {
        "l1": {
            "build": build_command,
            "entry": entries or [],
            "timeout_seconds": 30,
        }
    }


def _exit_command(code: int) -> str:
    return f'"{sys.executable}" -c "import sys; sys.exit({code})"'


def test_worker_l1_build_and_entry_success_are_recorded(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    _write_config(ws, project_id, _l1_config(_exit_command(0), ["json"]))
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [_case(item_id, "command: echo ok", depends_on="build")],
    )
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)

    l1 = run["l1"]
    assert l1["build"]["ok"] is True
    assert l1["build"]["exit_code"] == 0
    assert l1["entries"] == [
        {"module": "json", "ok": True, "evidence_ref": "evidence/l1-entry-0.log"}
    ]
    run_dir = ws.project_dir(project_id) / "runs" / run_id
    assert (run_dir / l1["build"]["evidence_ref"]).is_file()
    assert (run_dir / l1["entries"][0]["evidence_ref"]).is_file()
    assert run["cases"][0]["result"] == "passed"
    assert "evidence/l1-build.log" in run["evidence_refs"]


def test_worker_l1_build_failure_blocks_depends_on_build(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        items=[
            {"text": "依赖构建的用例", "observable": True},
            {"text": "独立用例", "observable": True},
        ],
    )
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_ids = [item["item_id"] for item in setup["task"]["acceptance_items"]]
    _write_config(ws, project_id, _l1_config(_exit_command(3), ["json"]))
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [
            _case(item_ids[0], "command: echo ok", depends_on="build"),
            _case(item_ids[1], "command: echo ok"),
        ],
    )
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)

    assert run["l1"]["build"]["ok"] is False
    assert run["l1"]["build"]["exit_code"] == 3
    assert run["cases"][0]["result"] == "blocked"
    assert "L1 检查未通过" in run["cases"][0]["attempts"][0]["message"]
    assert run["cases"][1]["result"] == "passed"
    run_dir = ws.project_dir(project_id) / "runs" / run_id
    assert (run_dir / run["l1"]["build"]["evidence_ref"]).is_file()


def test_worker_l1_entry_failure_blocks_depends_on_build(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    _write_config(
        ws,
        project_id,
        _l1_config(_exit_command(0), ["json", "no_such_module_aitest_check"]),
    )
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [_case(item_id, "command: echo ok", depends_on="build")],
    )
    _, run = _run_plan(tmp_path, capsys, setup, plan)

    assert run["l1"]["build"]["ok"] is True
    assert [entry["ok"] for entry in run["l1"]["entries"]] == [True, False]
    assert run["cases"][0]["result"] == "blocked"


def test_worker_without_l1_config_keeps_l1_null(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    plan = _plan(tmp_path, capsys, setup, [_case(item_id, "command: echo ok")])
    _, run = _run_plan(tmp_path, capsys, setup, plan)
    assert run["l1"] is None
    assert run["cases"][0]["result"] == "passed"


# -- D6 report real chain gate ----------------------------------------------


class _DummyClient:
    """Placeholder model client; judge_mock and summarize_report are patched."""


def test_report_real_chain_requires_every_dependency_real(
    tmp_path: Path, capsys: Capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    call(tmp_path, "module.add", {"project_id": project_id, "name": "orders"}, capsys)
    plan = _plan(tmp_path, capsys, setup, [_case(item_id, "command: echo ok")])
    run_id, _ = _run_plan(tmp_path, capsys, setup, plan)

    monkeypatch.setattr("aitest.core.reports.build_client", lambda *a, **k: _DummyClient())
    judgement = {
        "name": "orders",
        "kind": "real",
        "reason": "直接调用真实服务",
        "evidence_refs": ["src/orders/app.py"],
        "read_files": ["src/orders/app.py"],
        "limit_reached": False,
    }
    monkeypatch.setattr("aitest.core.reports.judge_mock", lambda *a, **k: [dict(judgement)])
    real = call(
        tmp_path,
        "report.generate",
        {"project_id": project_id, "run_id": run_id},
        capsys,
    )["data"]
    real_report = read_json(
        ws.project_dir(project_id) / "reports" / f"{real['report_id']}.json"
    )
    assert real_report["real_chain"] is True
    assert real_report["mock_judgement"][0]["read_files"] == ["src/orders/app.py"]

    monkeypatch.setattr(
        "aitest.core.reports.judge_mock",
        lambda *a, **k: [dict(judgement, kind="mixed")],
    )
    mixed = call(
        tmp_path,
        "report.generate",
        {"project_id": project_id, "run_id": run_id},
        capsys,
    )["data"]
    mixed_report = read_json(
        ws.project_dir(project_id) / "reports" / f"{mixed['report_id']}.json"
    )
    assert mixed_report["real_chain"] is False


# -- D7 SQL / JUnit / pytest runners ----------------------------------------


def _junit_xml(failing: bool) -> str:
    case = (
        '<testcase classname="tests.sample" name="test_bad">'
        '<failure message="assertion broke">trace</failure></testcase>'
        if failing
        else '<testcase classname="tests.sample" name="test_ok"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<testsuite name="sample">{case}</testsuite>'
    )


def test_worker_junit_command_reads_fixed_xml_and_passes(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    (tmp_path / "repo" / "junit.xml").write_text(_junit_xml(False), encoding="utf-8")
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [_case(item_id, "junit:python -c \"print(open('junit.xml',encoding='utf-8').read())\"")],
    )
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)

    assert run["cases"][0]["result"] == "passed"
    refs = run["cases"][0]["evidence_refs"]
    assert any(ref.endswith("-junit.xml") for ref in refs)
    junit_ref = next(ref for ref in refs if ref.endswith("-junit.xml"))
    run_dir = ws.project_dir(project_id) / "runs" / run_id
    assert "<testsuite" in (run_dir / junit_ref).read_text(encoding="utf-8")


def test_worker_junit_failure_is_failed(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    (tmp_path / "repo" / "junit.xml").write_text(_junit_xml(True), encoding="utf-8")
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [_case(item_id, "junit:python -c \"print(open('junit.xml',encoding='utf-8').read())\"")],
    )
    _, run = _run_plan(tmp_path, capsys, setup, plan)
    assert run["cases"][0]["result"] == "failed"
    assert "JUnit" in run["cases"][0]["attempts"][0]["message"]


def test_worker_junit_ignores_stale_report_file(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    (tmp_path / "repo" / "junit.xml").write_text(_junit_xml(False), encoding="utf-8")
    plan = _plan(tmp_path, capsys, setup, [_case(item_id, "junit:echo ready")])
    _, run = _run_plan(tmp_path, capsys, setup, plan)
    assert run["cases"][0]["result"] == "error"
    assert "未找到 JUnit XML" in run["cases"][0]["attempts"][0]["message"]


def test_worker_junit_missing_report_is_error(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    plan = _plan(tmp_path, capsys, setup, [_case(item_id, "junit:echo no-xml")])
    _, run = _run_plan(tmp_path, capsys, setup, plan)
    assert run["cases"][0]["result"] == "error"
    assert "未找到 JUnit XML" in run["cases"][0]["attempts"][0]["message"]


def test_worker_sql_verification_maps_exit_codes(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        items=[
            {"text": "SQL 成功", "observable": True},
            {"text": "SQL 失败", "observable": True},
        ],
    )
    item_ids = [item["item_id"] for item in setup["task"]["acceptance_items"]]
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [
            _case(item_ids[0], f"sql:{_exit_command(0)}"),
            _case(item_ids[1], f"sql:{_exit_command(2)}"),
        ],
    )
    _, run = _run_plan(tmp_path, capsys, setup, plan)
    assert run["cases"][0]["result"] == "passed"
    assert run["cases"][1]["result"] == "failed"
    assert "退出码 2" in run["cases"][1]["attempts"][0]["message"]


def test_worker_pytest_uses_generated_junit_report(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    (tmp_path / "repo" / "test_sample.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    plan = _plan(tmp_path, capsys, setup, [_case(item_id, "pytest:-q test_sample.py")])
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)

    assert run["cases"][0]["result"] == "passed"
    refs = run["cases"][0]["evidence_refs"]
    junit_ref = next(ref for ref in refs if ref.endswith("-junit.xml"))
    run_dir = ws.project_dir(project_id) / "runs" / run_id
    assert "<testsuite" in (run_dir / junit_ref).read_text(encoding="utf-8")
    assert (run_dir / "junit.xml").is_file()


# -- G3 follow-up: service shape, incomplete delivery, screenshots ----------


def test_service_config_accepts_single_and_mapping_shapes() -> None:
    from aitest.worker import _services

    single = {"start": "run", "health": "http://127.0.0.1/health", "stop": "stop"}
    assert _services({"services": single}) == [("", single)]
    mapped = {"web": {"start": "web"}, "db": {"start": "db"}}
    assert _services({"services": mapped}) == [
        ("web", {"start": "web"}),
        ("db", {"start": "db"}),
    ]
    assert _services({"services": {}}) == []
    assert _services({"services": {"web": {"start": "web"}, "port": 8000}}) == []


def test_worker_starts_all_mapped_services_and_records_names(
    tmp_path: Path, capsys: Capsys, http_server: _RecordingServer
) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    web_marker = tmp_path / "web-started.txt"
    worker_marker = tmp_path / "worker-started.txt"
    web_start = _marker_script(scripts, "start_web.py")
    worker_start = _marker_script(scripts, "start_worker.py")
    _write_config(
        ws,
        project_id,
        {
            "services": {
                "web": {
                    "health": f"{_base_url(http_server)}/health",
                    "start": f'"{sys.executable}" "{web_start}" "{web_marker}"',
                },
                "worker": {
                    "health": "http://127.0.0.1:1/never",
                    "start": f'"{sys.executable}" "{worker_start}" "{worker_marker}"',
                },
            }
        },
    )
    plan = _plan(tmp_path, capsys, setup, [_case(item_id, "command: echo ok")])
    _, run = _run_plan(tmp_path, capsys, setup, plan)

    assert run["service_name"] == "web、worker"
    assert run["service_started"] is True
    assert not web_marker.exists()
    assert worker_marker.exists()
    assert run["cases"][0]["result"] == "passed"


def test_worker_blocks_build_dependent_cases_when_delivery_incomplete(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        items=[
            {"text": "依赖构建的用例", "observable": True},
            {"text": "独立用例", "observable": True},
        ],
    )
    project_id = setup["project"]["project_id"]
    task_id = setup["task"]["task_id"]
    item_ids = [item["item_id"] for item in setup["task"]["acceptance_items"]]
    saved = call(
        tmp_path,
        "delivery.save",
        {
            "project_id": project_id,
            "task_id": task_id,
            "done": ["改完了"],
        },
        capsys,
    )
    assert saved["data"]["complete"] is False
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [
            _case(item_ids[0], "command: echo ok", depends_on="build"),
            _case(item_ids[1], "command: echo ok"),
        ],
    )
    _, run = _run_plan(tmp_path, capsys, setup, plan)

    assert run["cases"][0]["result"] == "blocked"
    assert run["cases"][0]["attempts"][0]["message"] == "交付信息不完整：缺运行方式或版本"
    assert run["cases"][1]["result"] == "passed"


def test_worker_collects_test_results_screenshots(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    script = (
        "import pathlib\n"
        "out = pathlib.Path('test-results')\n"
        "out.mkdir(exist_ok=True)\n"
        "out.joinpath('shot.png').write_bytes(bytes([137, 80, 78, 71]))\n"
    )
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [
            _case(
                item_id,
                "script:shot.py",
                script_name="shot.py",
                script_content=script,
            )
        ],
    )
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)

    assert run["cases"][0]["result"] == "passed"
    refs = run["cases"][0]["evidence_refs"]
    shot = next(ref for ref in refs if ref.endswith("shot.png"))
    run_dir = ws.project_dir(project_id) / "runs" / run_id
    assert (run_dir / shot).is_file()


def test_worker_collects_script_page_screenshot_path(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    script = (
        "import pathlib\n"
        "class Page:\n"
        "    def screenshot(self, path):\n"
        "        target = pathlib.Path(path)\n"
        "        target.parent.mkdir(parents=True, exist_ok=True)\n"
        "        target.write_bytes(bytes([137, 80, 78, 71]))\n"
        "page = Page()\n"
        "page.screenshot(path='ui/live.png')\n"
    )
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [
            _case(
                item_id,
                "script:live.py",
                script_name="live.py",
                script_content=script,
            )
        ],
    )
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)

    refs = run["cases"][0]["evidence_refs"]
    shot = next(ref for ref in refs if ref.endswith("live.png"))
    run_dir = ws.project_dir(project_id) / "runs" / run_id
    assert (run_dir / shot).is_file()


def test_worker_collects_real_playwright_screenshot(tmp_path: Path, capsys: Capsys) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with sync_api.sync_playwright() as runtime:
            runtime.chromium.launch().close()
    except Exception as exc:  # noqa: BLE001 - a missing browser is an environment gap
        pytest.skip(f"Playwright 浏览器不可用：{exc}")
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    script = (
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as runtime:\n"
        "    browser = runtime.chromium.launch()\n"
        "    page = browser.new_page()\n"
        "    page.set_content('<h1>aitest</h1>')\n"
        "    page.screenshot(path='ui/playwright.png')\n"
        "    browser.close()\n"
    )
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [
            _case(
                item_id,
                "script:browser.py",
                script_name="browser.py",
                script_content=script,
            )
        ],
    )
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)

    assert run["cases"][0]["result"] == "passed"
    refs = run["cases"][0]["evidence_refs"]
    shot = next(ref for ref in refs if ref.endswith("playwright.png"))
    run_dir = ws.project_dir(project_id) / "runs" / run_id
    data = (run_dir / shot).read_bytes()
    assert data.startswith(b"\x89PNG")


def test_run_manual_rejects_automatic_case(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    plan = _plan(tmp_path, capsys, setup, [_case(item_id, "command: exit 1")])
    run_id, run = _run_plan(tmp_path, capsys, setup, plan)
    response = call(
        tmp_path,
        "run.manual",
        {
            "project_id": setup["project"]["project_id"],
            "run_id": run_id,
            "case_id": run["cases"][0]["case_id"],
            "step": "强行改判",
            "result": "passed",
        },
        capsys,
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "RUN_STATE_CONFLICT"


def test_data_cleanup_requires_run_id_for_data_prefix(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    ws = _workspace(tmp_path)
    project_id = setup["project"]["project_id"]
    _write_config(
        ws,
        project_id,
        {"data": {"cleanup": "echo {data_prefix}", "cleanup_dry_run": "echo dry"}},
    )
    missing = call(
        tmp_path,
        "data.cleanup",
        {"project_id": project_id, "dry_run": False},
        capsys,
        confirm=True,
    )
    assert missing["ok"] is False
    assert missing["error"]["code"] == "INVALID_REQUEST"
    traversal = call(
        tmp_path,
        "data.cleanup",
        {"project_id": project_id, "dry_run": False, "run_id": ".."},
        capsys,
        confirm=True,
    )
    assert traversal["ok"] is False
    assert traversal["error"]["code"] == "INVALID_REQUEST"


def test_plan_rejects_shell_characters_in_playwright_path(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    item_id = setup["task"]["acceptance_items"][0]["item_id"]
    response = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "scope": {
                "preset": "selected",
                "acceptance_item_ids": [item_id],
            },
            "cases": [_case(item_id, 'playwright:ui/a.ts" & calc')],
        },
        capsys,
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "INVALID_REQUEST"


def test_http_runner_rejects_unasserted_redirect(http_server: _RecordingServer) -> None:
    result = run_http_sequence([{"url": f"{_base_url(http_server)}/redirect"}])
    assert result.status == "blocked"
    assert "without following" in result.error


def test_http_runner_follows_same_host_redirect(http_server: _RecordingServer) -> None:
    result = run_http_sequence(
        [{"url": f"{_base_url(http_server)}/redirect", "follow_redirects": True}]
    )
    assert result.status == "passed"
