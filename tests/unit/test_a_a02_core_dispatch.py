"""A-02：统一装配、Command 派发/Response 回写与长期唯一核心生命周期。

跨平台单测（不依赖命名管道）：
- ``assemble_workspace_core`` 自动接线 B 用例、C/D 注册表叠加与冲突拒绝、
  启动恢复 blocked 拒绝服务；
- ``dispatch_frame``：正常 Command 经 LocalAPI 派发、坏帧/非法命令回协议
  错误、停机控制帧、未知控制动作；
- ``serve_connection`` 用假管道验证多帧回路与停机/断开返回；
- ``watch_parent`` 纯函数看门狗。

Windows 集成（真实 ``core_worker`` 子进程 + 命名管道）：
- doctor 与 B 的 query 跨进程跑通（证明子进程自动装配）；
- 客户端断开后同实例重连（长期唯一核心，不再"断开即退出"）；
- 坏帧回错误且核心存活；停机帧优雅退出后再获取得是**新**实例；
- 带崩溃残留（已提交记录 + 活动标记）的工作空间在核心启动时完成跨核心恢复。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.bootstrap import (
    CoreAssemblyBlocked,
    acquire_endpoint,
    assemble_workspace_core,
    make_pipe_connector,
    shutdown_endpoint,
)
from aitest.contracts.commands import Command
from aitest.contracts.responses import Response
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from aitest.interfaces.local.core_worker import (
    ShutdownControl,
    dispatch_frame,
    serve_connection,
    shutdown_frame,
    watch_parent,
)

win_only = pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="命名管道仅 Windows"
)

_SESSION = Session(session_id="test-session", entry_kind=EntryKind.AGENT_RELAY)


def _assembled_api(tmp_path: Path, *, instance_id: str = "core-a02") -> LocalAPI:
    return assemble_workspace_core(
        tmp_path, instance_id=instance_id
    ).api


def _command_frame(
    request_id: str,
    action: str,
    *,
    project_id: str | None = None,
    parameters: dict[str, object] | None = None,
) -> bytes:
    command = Command(
        request_id=request_id,
        action=action,
        project_id=project_id,
        parameters=parameters or {},
    )
    return command.model_dump_json().encode("utf-8")


# ------------------------------------------------------------ 统一装配


def test_assembly_auto_wires_b_use_cases(tmp_path: Path) -> None:
    assembly = assemble_workspace_core(tmp_path, instance_id="core-auto")
    built_in = {
        "save_context",
        "save_binding",
        "prepare_run",
        "publish_rules",
        "query",
    }
    assert built_in <= set(assembly.api.handlers)
    assert assembly.api.workspace_id == assembly.workspace.workspace_id
    # 启动恢复在健康工作空间上是无动作结论（不产生误报修复）。
    assert assembly.recovery.state in {"healthy", "repaired"}


def test_assembled_doctor_advertises_b_actions(tmp_path: Path) -> None:
    api = _assembled_api(tmp_path)
    response = api.dispatch(
        Command(request_id="req-doctor", action="doctor"), _SESSION
    )
    assert response.error is None
    assert response.result is not None
    assert response.result["status"] == "READY"
    assert "prepare_run" in response.result["supported_actions"]


def _seed_project_record(root: Path, project_id: str) -> None:
    """经底座 UoW 真实提交一条 project 记录（同时建成索引/投影）。"""
    unit = FileUnitOfWork(root)
    unit.begin("req-seed", project_id)
    unit.stage_record(
        aggregate_kind="project",
        record_id=project_id,
        expected_revision=None,
        payload={"project_id": project_id, "name": "demo"},
    )
    unit.commit("req-seed")


def test_assembled_b_query_routes_through_narrow_adapter(tmp_path: Path) -> None:
    _seed_project_record(tmp_path, "project-a02")
    api = _assembled_api(tmp_path)
    response = api.dispatch(
        Command(
            request_id="req-query",
            action="query",
            project_id="project-a02",
            parameters={"aggregate_kind": "project", "limit": 10},
        ),
        _SESSION,
    )
    assert response.error is None
    assert response.result is not None
    items = response.result["items"]
    assert len(items) == 1
    assert items[0]["record_id"] == "project-a02"


def test_extra_handlers_overlay_and_conflict_rejection(tmp_path: Path) -> None:
    def c_handler(command: Command) -> dict[str, object]:
        return {"echo": command.request_id}

    assembly = assemble_workspace_core(
        tmp_path,
        instance_id="core-c",
        extra_handlers={"c_custom_action": c_handler},
    )
    response = assembly.api.dispatch(
        Command(request_id="req-c", action="c_custom_action"), _SESSION
    )
    assert response.result == {"echo": "req-c"}

    # 同根第二核心必须先释放生命周期写锁后才能重新准入（A-02）。
    assembly.lifetime_lock.release()
    with pytest.raises(ValueError, match="conflicts with built-in actions"):
        assemble_workspace_core(
            tmp_path,
            instance_id="core-c2",
            extra_handlers={"query": c_handler},
        )


def test_second_lifetime_admission_for_same_root_is_rejected(
    tmp_path: Path,
) -> None:
    from aitest.application.errors import WorkspaceInUse

    assembly = assemble_workspace_core(tmp_path, instance_id="core-first")
    try:
        with pytest.raises(WorkspaceInUse):
            assemble_workspace_core(tmp_path, instance_id="core-second")
    finally:
        assembly.lifetime_lock.release()
    # 释放后同根可被新核心准入（模拟干净重启）。
    restarted = assemble_workspace_core(tmp_path, instance_id="core-restart")
    restarted.lifetime_lock.release()


def test_assembly_blocked_on_integrity_failure(tmp_path: Path) -> None:
    # 损坏当前权威指针，不能用已退出读取路径的legacy副本制造假反例。
    seeded = assemble_workspace_core(tmp_path, instance_id="core-seed")
    seeded.lifetime_lock.release()
    (tmp_path / "current.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(CoreAssemblyBlocked):
        assemble_workspace_core(tmp_path, instance_id="core-blocked")
    assert (tmp_path / "current.json").read_text(encoding="utf-8") == "{not-json"


# ------------------------------------------------------------ 帧派发


def test_dispatch_frame_routes_valid_command(tmp_path: Path) -> None:
    api = _assembled_api(tmp_path)
    outcome = dispatch_frame(
        api, _SESSION, _command_frame("req-1", "doctor")
    )
    assert isinstance(outcome, Response)
    assert outcome.error is None
    assert outcome.result is not None
    assert outcome.result["status"] == "READY"


def test_dispatch_frame_rejects_malformed_and_non_object(tmp_path: Path) -> None:
    api = _assembled_api(tmp_path)
    bad_utf8_json = dispatch_frame(api, _SESSION, b"{")
    assert isinstance(bad_utf8_json, Response)
    assert bad_utf8_json.error is not None
    assert bad_utf8_json.error.code == "MALFORMED_MESSAGE"

    non_object = dispatch_frame(api, _SESSION, b"[1, 2, 3]")
    assert isinstance(non_object, Response)
    assert non_object.error is not None
    assert non_object.error.code == "MALFORMED_MESSAGE"
    assert non_object.request_id == "invalid-request"


def test_dispatch_frame_rejects_invalid_command(tmp_path: Path) -> None:
    api = _assembled_api(tmp_path)
    # 缺少 request_id（Command 合同必填）。
    outcome = dispatch_frame(
        api, _SESSION, json.dumps({"action": "doctor"}).encode("utf-8")
    )
    assert isinstance(outcome, Response)
    assert outcome.error is not None
    assert outcome.error.code == "INVALID_COMMAND"
    assert outcome.request_id == "invalid-request"


def test_dispatch_frame_handles_shutdown_and_unknown_control(
    tmp_path: Path,
) -> None:
    api = _assembled_api(tmp_path)
    outcome = dispatch_frame(api, _SESSION, shutdown_frame())
    assert isinstance(outcome, ShutdownControl)
    assert outcome.request_id == "core-shutdown"

    unknown = dispatch_frame(
        api,
        _SESSION,
        json.dumps({"__aitest_control__": "explode"}).encode("utf-8"),
    )
    assert isinstance(unknown, Response)
    assert unknown.error is not None
    assert unknown.error.code == "UNKNOWN_CONTROL"


class _FakeServer:
    """按脚本读帧、记录回写；read 耗尽后抛异常模拟对端断开。"""

    def __init__(self, incoming: list[bytes]) -> None:
        self._incoming = list(incoming)
        self.written: list[bytes] = []

    def read_message(self) -> bytes:
        if not self._incoming:
            raise ConnectionError("peer closed")
        return self._incoming.pop(0)

    def write_message(self, payload: bytes) -> None:
        self.written.append(payload)


def test_serve_connection_dispatches_multiple_frames_and_acks_shutdown(
    tmp_path: Path,
) -> None:
    api = _assembled_api(tmp_path)
    server = _FakeServer(
        [
            _command_frame("req-a", "doctor"),
            _command_frame("req-b", "doctor"),
            shutdown_frame(),
        ]
    )
    result = serve_connection(server, api, connection_no=1, shutdown_blocker=lambda: None)
    assert result == "shutdown"
    assert len(server.written) == 3
    ack = Response.model_validate_json(server.written[2])
    assert ack.result == {"status": "shutting_down"}
    first = Response.model_validate_json(server.written[0])
    assert first.request_id == "req-a"


def test_serve_connection_returns_disconnected_when_peer_gone(
    tmp_path: Path,
) -> None:
    api = _assembled_api(tmp_path)
    server = _FakeServer(
        [_command_frame("req-a", "doctor")]
    )
    # 第一帧正常回写，第二次读抛异常 → 断开，核心不退出。
    result = serve_connection(server, api, connection_no=2)
    assert result == "disconnected"
    assert len(server.written) == 1


def test_serve_connection_survives_write_failure(tmp_path: Path) -> None:
    api = _assembled_api(tmp_path)

    class _WriteFails(_FakeServer):
        def write_message(self, payload: bytes) -> None:
            raise ConnectionError("peer gone during write")

    server = _WriteFails([_command_frame("req-a", "doctor")])
    assert serve_connection(server, api, connection_no=3) == "disconnected"


def test_watch_parent_detects_gone_parent_and_stop_event() -> None:
    alive = {"value": True}
    stop = threading.Event()

    def is_alive(_pid: int) -> bool:
        return alive["value"]

    alive["value"] = False
    assert watch_parent(4242, is_alive=is_alive, stop_event=stop) is True

    alive["value"] = True
    stop.set()
    assert watch_parent(4242, is_alive=is_alive, stop_event=stop) is False


def test_main_rejects_non_windows_and_bad_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aitest.interfaces.local import core_worker

    # 非 Windows 直接退出码 2（不触发任何装配）。
    monkeypatch.setattr(sys, "platform", "linux")
    assert core_worker.main(["--workspace-root", str(tmp_path),
                             "--workspace-id", "ws", "--instance-id", "i"]) == 2
    monkeypatch.setattr(sys, "platform", "win32")
    assert core_worker.main(
        ["--workspace-root", str(tmp_path), "--workspace-id", "ws",
         "--instance-id", "i", "--max-clients", "-1"]
    ) == 2


# ------------------------------------------------------------ Windows 真实管道集成


def _wait_core_gone(root: Path, workspace_id: str, timeout: float = 8.0) -> bool:
    connector = make_pipe_connector(root)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if connector(workspace_id) is None:
            return True
        time.sleep(0.1)
    return False


def _roundtrip(client: object, payload: bytes) -> Response:
    client.write_message(payload)  # type: ignore[attr-defined]
    return Response.model_validate_json(client.read_message())  # type: ignore[attr-defined]


@win_only
def test_worker_dispatches_commands_over_real_pipe(tmp_path: Path) -> None:
    workspace_id = "wsA02Dispatch"
    _seed_project_record(tmp_path, "project-a02")
    endpoint = acquire_endpoint(
        tmp_path, workspace_id=workspace_id, wait_timeout_seconds=8.0
    )
    try:
        response = _roundtrip(
            endpoint.connection, _command_frame("req-doc", "doctor")
        )
        assert response.error is None
        assert response.result is not None
        assert response.result["status"] == "READY"
        # 子进程全新解释器：prepare_run 可达即证明自动装配（不是空注册表）。
        assert "prepare_run" in response.result["supported_actions"]

        query = _roundtrip(
            endpoint.connection,
            _command_frame(
                "req-query",
                "query",
                project_id="project-a02",
                parameters={"aggregate_kind": "project"},
            ),
        )
        assert query.error is None
        assert query.result is not None
        assert len(query.result["items"]) == 1

        # 坏帧回协议错误，核心继续存活。
        bad = _roundtrip(endpoint.connection, b"{")
        assert bad.error is not None
        assert bad.error.code == "MALFORMED_MESSAGE"
        after_bad = _roundtrip(
            endpoint.connection, _command_frame("req-doc-2", "doctor")
        )
        assert after_bad.result is not None
        assert after_bad.result["status"] == "READY"
    finally:
        endpoint.connection.close()
        assert shutdown_endpoint(tmp_path, workspace_id=workspace_id)
        assert _wait_core_gone(tmp_path, workspace_id)


@win_only
def test_worker_is_long_lived_unique_core_across_reconnects(
    tmp_path: Path,
) -> None:
    workspace_id = "wsA02Reconnect"
    first = acquire_endpoint(
        tmp_path, workspace_id=workspace_id, wait_timeout_seconds=8.0
    )
    first.connection.close()
    time.sleep(0.3)

    # 断开后再获取：必须连回**同一实例**（长期唯一核心 + 保活重连）。
    second = acquire_endpoint(
        tmp_path, workspace_id=workspace_id, wait_timeout_seconds=8.0
    )
    try:
        assert second.instance_id == first.instance_id
        response = _roundtrip(
            second.connection, _command_frame("req-doc", "doctor")
        )
        assert response.result is not None
        assert response.result["status"] == "READY"
    finally:
        second.connection.close()

    # 优雅停机后再获取：新实例。
    assert shutdown_endpoint(tmp_path, workspace_id=workspace_id)
    assert _wait_core_gone(tmp_path, workspace_id)
    third = acquire_endpoint(
        tmp_path, workspace_id=workspace_id, wait_timeout_seconds=8.0
    )
    try:
        assert third.instance_id != first.instance_id
    finally:
        third.connection.close()
        assert shutdown_endpoint(tmp_path, workspace_id=workspace_id)
        assert _wait_core_gone(tmp_path, workspace_id)


@win_only
def test_worker_startup_recovers_stale_core_leavings(tmp_path: Path) -> None:
    workspace_id = "wsA02Recovery"
    # 模拟上一核心：真实提交 1 条记录后崩溃，留下已发布事实 + 活动标记。
    unit = FileUnitOfWork(tmp_path)
    unit.begin("req-crashed", "project-a02")
    unit.stage_record(
        aggregate_kind="project",
        record_id="project-a02",
        expected_revision=None,
        payload={"project_id": "project-a02", "name": "demo"},
    )
    unit.commit("req-crashed")
    marker_dir = tmp_path / "transactions"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "active.json").write_text(
        json.dumps(
            {
                "request_id": "req-crashed",
                "project_id": "project-a02",
                "intent_id": None,
                "commit_sequence": 1,
                "state": "in_progress",
            }
        ),
        encoding="utf-8",
    )

    endpoint = acquire_endpoint(
        tmp_path, workspace_id=workspace_id, wait_timeout_seconds=8.0
    )
    try:
        # 启动恢复闭合后核心仍能正常服务。
        response = _roundtrip(
            endpoint.connection,
            _command_frame(
                "req-query",
                "query",
                project_id="project-a02",
                parameters={"aggregate_kind": "project"},
            ),
        )
        assert response.error is None
        assert response.result is not None
        assert len(response.result["items"]) == 1
    finally:
        endpoint.connection.close()
        assert shutdown_endpoint(tmp_path, workspace_id=workspace_id)
        assert _wait_core_gone(tmp_path, workspace_id)

    # 重启后巡检：活动标记已清，权威提交序号仍在。
    inspect = RecoveryOrchestrator(
        tmp_path, instance_id="post-stop-inspect"
    ).inspect()
    assert inspect["active_marker"] is None
    assert 1 in inspect["committed_sequences"]
