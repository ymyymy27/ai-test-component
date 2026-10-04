"""A 包跨进程装配测试：SystemProcessLauncher / 管道连接器 / acquire_endpoint。

集成测试用真实 :func:`subprocess.Popen` 启动
:mod:`aitest.interfaces.local.core_worker` 子进程并完成管道连接核对；
纯单元测试用桩 launcher/connector 验证装配路径不依赖子进程。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from aitest.bootstrap import (
    SystemProcessLauncher,
    acquire_endpoint,
    make_editor_host,
    make_pipe_connector,
    shutdown_endpoint,
)
from aitest.infrastructure.file_store.core_launch import ProcessFact
from aitest.interfaces.local.editor_host import (
    CoreEndpoint,
    EditorHost,
    WorkspaceInUse,
)
from aitest.interfaces.local.pipe import NamedPipeClient

win_only = pytest.mark.skipif(not sys.platform.startswith("win"), reason="命名管道仅 Windows")


# ----- 纯单元：launcher / connector 行为 ----------------------------------


def test_launcher_writes_instance_id_file(tmp_path: Path) -> None:
    # 改用假入口模块避免真启动子进程
    fake_module = "aitest.interfaces.local.core_worker"
    launcher = SystemProcessLauncher(
        tmp_path,
        worker_module=fake_module,
        process_probe=lambda pid: ProcessFact(True, "a" * 64),
    )
    # 拦截 Popen，避免真启动
    import aitest.bootstrap as bootstrap_mod

    captured: list[list[str]] = []

    class _FakePopen:
        pid = 123456

        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            captured.append(cmd)

        def poll(self) -> None:
            return None

    monkey = pytest.MonkeyPatch()
    monkey.setattr(bootstrap_mod.subprocess, "Popen", _FakePopen)
    try:
        instance_id = launcher.start("wsLauncherFile")
    finally:
        monkey.undo()

    assert instance_id.startswith("core-")
    id_file = launcher.instance_id_path
    assert id_file.exists()
    assert id_file.read_text(encoding="utf-8") == instance_id
    cmd = captured[0]
    assert any("--instance-id" in arg for arg in cmd)
    # A-02：launcher 必须把宿主 PID 透传给核心做父进程保活。
    assert "--parent-pid" in cmd
    parent_index = cmd.index("--parent-pid")
    assert cmd[parent_index + 1].isdigit()


def test_connector_returns_none_when_no_id_file(tmp_path: Path) -> None:
    connector = make_pipe_connector(tmp_path)
    assert connector("wsNoIdFile") is None


def test_connector_returns_none_when_pipe_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    id_path = tmp_path / ".core-instance-id"
    id_path.write_text("core-missing", encoding="utf-8")

    from aitest.interfaces.local import pipe as pipe_mod

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def connect(self, *, timeout_ms: int = 200) -> None:
            raise pipe_mod.PipeUnavailable("no pipe")

    monkeypatch.setattr(pipe_mod, "NamedPipeClient", _FakeClient)
    connector = make_pipe_connector(tmp_path)
    assert connector("wsNoPipe") is None


def test_connector_returns_client_when_connect_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    id_path = tmp_path / ".core-instance-id"
    id_path.write_text("core-live", encoding="utf-8")

    from aitest.interfaces.local import pipe as pipe_mod

    class _FakeClient:
        def __init__(self, workspace_id: str, *, instance_id: str) -> None:
            self.workspace_id = workspace_id
            self.instance_id = instance_id

        def connect(self, *, timeout_ms: int = 200) -> None:
            return None

    monkeypatch.setattr(pipe_mod, "NamedPipeClient", _FakeClient)
    connector = make_pipe_connector(tmp_path)
    result = connector("wsLive")
    assert result is not None
    connection, instance_id = result
    assert instance_id == "core-live"
    assert isinstance(connection, _FakeClient)


def test_make_editor_host_injects_launcher_and_connector(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class _RecordingLauncher:
        def start(self, workspace_id: str) -> str:
            captured["started"] = workspace_id
            return "core-recorded"

    host = make_editor_host(tmp_path, launcher=_RecordingLauncher())
    assert isinstance(host, EditorHost)
    # connector 缺省 instance-id 文件 → None → host 应启动 launcher
    with pytest.raises(WorkspaceInUse):
        host.acquire("wsRecording")
    assert captured.get("started") == "wsRecording"


def test_make_editor_host_uses_default_launcher_when_none(tmp_path: Path) -> None:
    # 不真启动子进程：只校验 make_editor_host(launcher=None) 返回
    # 装配好的 EditorHost，且内置 launcher 是 SystemProcessLauncher 实例
    host = make_editor_host(tmp_path, launcher=None, wait_timeout_seconds=0.1)
    assert isinstance(host, EditorHost)
    # EditorHost 私有 launcher 字段应为 SystemProcessLauncher
    from aitest.bootstrap import SystemProcessLauncher as _SPL

    assert isinstance(host._launcher, _SPL)


# ----- 集成：真实子进程 + 命名管道（Windows） -----------------------------


def _wait_core_gone(root: Path, workspace_id: str, timeout: float = 8.0) -> bool:
    connector = make_pipe_connector(root)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if connector(workspace_id) is None:
            return True
        time.sleep(0.1)
    return False


@win_only
def test_acquire_endpoint_with_real_subprocess(tmp_path: Path) -> None:
    """端到端：SystemProcessLauncher 启动 core_worker 子进程，
    acquire_endpoint 通过管道完成连接，返回已核对身份的 CoreEndpoint。

    A-02 后核心是长期唯一核心：客户端断开不退出，必须用停机控制帧
    显式结束工作空间生命周期，结束后管道消失、可重新拉起。
    """
    workspace_id = "wsRealSubprocess"
    endpoint = acquire_endpoint(
        tmp_path,
        workspace_id=workspace_id,
        wait_timeout_seconds=8.0,
    )
    assert isinstance(endpoint, CoreEndpoint)
    assert endpoint.workspace_id == workspace_id
    assert endpoint.instance_id.startswith("core-")
    # connector 返回的 connection 是 NamedPipeClient 实例
    from aitest.interfaces.local.pipe import NamedPipeClient

    assert isinstance(endpoint.connection, NamedPipeClient)
    # 客户端断开不杀核心；显式停机后核心退出、管道释放。
    endpoint.connection.close()
    assert shutdown_endpoint(tmp_path, workspace_id=workspace_id)
    assert _wait_core_gone(tmp_path, workspace_id)


@win_only
def test_acquire_endpoint_reconnects_same_core_then_starts_new_after_shutdown(
    tmp_path: Path,
) -> None:
    """首次 acquire 启动长期核心；连接关闭后核心继续存活，再次
    acquire 必须连回**同一实例**（保活重连）；显式停机后再 acquire
    才启动新核心（instance_id 不同）。
    """
    workspace_id = "wsNewCore"
    first = acquire_endpoint(
        tmp_path,
        workspace_id=workspace_id,
        wait_timeout_seconds=8.0,
    )
    assert isinstance(first.connection, NamedPipeClient)
    first.connection.close()
    # 等待核心完成旧管道关闭与重建。
    time.sleep(0.3)

    second = acquire_endpoint(
        tmp_path,
        workspace_id=workspace_id,
        wait_timeout_seconds=8.0,
    )
    assert first.instance_id == second.instance_id
    assert isinstance(second.connection, NamedPipeClient)
    second.connection.close()

    # 停机后再获取：新核心新实例。
    assert shutdown_endpoint(tmp_path, workspace_id=workspace_id)
    assert _wait_core_gone(tmp_path, workspace_id)
    third = acquire_endpoint(
        tmp_path,
        workspace_id=workspace_id,
        wait_timeout_seconds=8.0,
    )
    assert third.instance_id != first.instance_id
    try:
        assert isinstance(third.connection, NamedPipeClient)
    finally:
        third.connection.close()
        assert shutdown_endpoint(tmp_path, workspace_id=workspace_id)
        assert _wait_core_gone(tmp_path, workspace_id)
