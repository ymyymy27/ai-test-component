"""A-10：连接事实跨重启持久化、来源会话核对与动作级降级。

覆盖：
- 核心连接台账（CoreConnectionLedger）：连接/停机事实 fsync 追加、序号连续、
  跨“重启”读回、撕裂尾行显式报错；
- 探测事实台账（ConnectionFactStore）+ 可恢复 ConnectionMonitor；
- LocalAPIConnectionBridge 接入 LocalAPI：真实本地 TCP 端点可达、重启水合、
  失败只降级 test_connection，doctor/业务用例不受影响（动作级降级）；
- 凭据缺失只影响真实依赖凭据的动作（Git/文件核验照常）；
- Windows：真实凭据管理器端到端（写/读/删/不落盘）、来源 SID 取证、
  真实管道连接台账。
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.bootstrap import (
    CoreConnectionLedger,
    acquire_endpoint,
    shutdown_endpoint,
)
from aitest.contracts.commands import Command
from aitest.infrastructure.adapters.execution.verification import (
    IndependentFileVerifier,
)
from aitest.infrastructure.adapters.source_control import (
    GitSourceControl,
    GitUnavailable,
)
from aitest.infrastructure.connections import (
    ConnectionFactStore,
    ConnectionMonitor,
    EndpointConfig,
    LocalAPIConnectionBridge,
    TransportErrorKind,
    TransportFact,
)
from aitest.infrastructure.credentials import (
    SecretManager,
    SecretUnavailable,
    WindowsCredentialProvider,
)
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session

win_only = pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="Windows 专用能力"
)
_SESSION = Session(session_id="a10", entry_kind=EntryKind.INTERACTIVE_CLI)
_SCRIPT_UNREACHABLE = TransportFact(
    False, 3, TransportErrorKind.CONNECTION_REFUSED.value, "connection refused"
)
_SCRIPT_REACHABLE = TransportFact(True, 2, None, "")


class _ScriptedProbe:
    def __init__(self, facts: list[TransportFact]) -> None:
        self._facts = facts

    def probe(
        self, endpoint: EndpointConfig, *, timeout_seconds: float = 2.0
    ) -> TransportFact:
        return self._facts.pop(0)


def _endpoint(address: str = "https://api.example.com:443") -> EndpointConfig:
    return EndpointConfig.from_address(address)


# ------------------------------------------------------------ 核心连接台账


def test_core_connection_ledger_persists_facts_across_restart(tmp_path: Path) -> None:
    ledger = CoreConnectionLedger(
        tmp_path,
        session_probe=lambda: 2,
        user_probe=lambda: "S-1-5-21-x",
    )
    first = ledger.record(
        "connected", pipe_workspace_id="ws-a10", instance_id="core-1"
    )
    second = ledger.record(
        "connected", pipe_workspace_id="ws-a10", instance_id="core-1"
    )
    third = ledger.record(
        "shutdown_requested",
        pipe_workspace_id="ws-a10",
        instance_id="core-1",
    )

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert third["seq"] == 3
    assert first["source_session_id"] == 2
    assert first["source_user_sid"] == "S-1-5-21-x"

    # 模拟宿主/核心重启：新实例从同一工作空间读回全部事实。
    reloaded = CoreConnectionLedger(tmp_path).facts()
    assert [fact["kind"] for fact in reloaded] == [
        "connected",
        "connected",
        "shutdown_requested",
    ]
    assert ledger.last("shutdown_requested")["instance_id"] == "core-1"
    assert ledger.path.exists()


def test_core_connection_ledger_rejects_torn_tail(tmp_path: Path) -> None:
    ledger = CoreConnectionLedger(
        tmp_path, session_probe=lambda: 1, user_probe=lambda: "S-1"
    )
    ledger.record("connected", pipe_workspace_id="ws", instance_id="core-1")
    with ledger.path.open("ab") as handle:
        handle.write(b'{"schema":"aitest.core-connection/1.0","kin')  # 撕裂尾行
    # 历史行仍可解析，撕裂尾行显式抛错，绝不静默截断。
    with pytest.raises(json.JSONDecodeError):
        CoreConnectionLedger(tmp_path).facts()


def test_core_connection_ledger_carries_no_payload_or_secret(tmp_path: Path) -> None:
    ledger = CoreConnectionLedger(
        tmp_path, session_probe=lambda: 1, user_probe=lambda: "S-1"
    )
    ledger.record("connected", pipe_workspace_id="ws", instance_id="core-1")
    raw = ledger.path.read_text(encoding="utf-8")
    # 台账只含连接元数据，固定键集合，不含业务载荷位。
    fact = json.loads(raw)
    assert set(fact) == {
        "schema",
        "seq",
        "kind",
        "at",
        "pipe_workspace_id",
        "instance_id",
        "source_session_id",
        "source_user_sid",
    }


# ------------------------------------------------------------ 探测事实台账


def test_connection_fact_store_rebuilds_state_per_endpoint(tmp_path: Path) -> None:
    store = ConnectionFactStore(tmp_path)
    endpoint_a = _endpoint("https://a.example.com:443")
    endpoint_b = _endpoint("https://b.example.com:8443")
    store.append(
        endpoint_address=endpoint_a.base_address,
        fact=_SCRIPT_UNREACHABLE,
        source_session="S-1",
        observed_at=1.0,
    )
    store.append(
        endpoint_address=endpoint_b.base_address,
        fact=_SCRIPT_REACHABLE,
        source_session="S-1",
        observed_at=2.0,
    )
    store.append(
        endpoint_address=endpoint_a.base_address,
        fact=_SCRIPT_REACHABLE,
        source_session="S-1",
        observed_at=3.0,
    )

    state_a = store.load(endpoint_a.base_address)
    assert state_a is not None
    assert state_a.reachable is True
    assert len(state_a.attempts) == 2
    state_b = store.load(endpoint_b.base_address)
    assert state_b is not None
    assert state_b.attempts == (_SCRIPT_REACHABLE,)
    assert store.load("https://never-probed:1") is None


def test_connection_monitor_recovers_history_on_restart(tmp_path: Path) -> None:
    endpoint = _endpoint()
    first = ConnectionMonitor(
        endpoint,
        probe=_ScriptedProbe([_SCRIPT_UNREACHABLE]),
        clock=iter([1.0, 1.0]).__next__,
        store=ConnectionFactStore(tmp_path),
        source_session="S-1",
    )
    first.probe_once(timeout_seconds=0.1)

    # 全新 monitor 实例模拟核心重启：历史尝试从台账恢复并继续累积。
    second = ConnectionMonitor(
        endpoint,
        probe=_ScriptedProbe([_SCRIPT_REACHABLE]),
        clock=iter([2.0, 2.0]).__next__,
        store=ConnectionFactStore(tmp_path),
        source_session="S-1",
    )
    state = second.probe_once(timeout_seconds=0.1)
    assert len(state.attempts) == 2
    # 历史事实经 JSON 台账重建为等值新对象（模拟重启，非同一实例）。
    assert state.attempts[0] == _SCRIPT_UNREACHABLE
    assert state.reachable is True


def test_bridge_hydrates_local_api_state_after_restart(tmp_path: Path) -> None:
    endpoint = _endpoint("https://model.example.com:8443")
    store = ConnectionFactStore(tmp_path)
    bridge = LocalAPIConnectionBridge(
        endpoint,
        store,
        # 统一重试口径：只读探测最多 3 次重试（共 4 次）后终局失败。
        probe=_ScriptedProbe([_SCRIPT_UNREACHABLE] * 4),
        source_session="S-1",
    )
    api = LocalAPI(
        instance_id="core-old",
        workspace_id="ws",
        connector=bridge,
        connection_persistence=bridge,
        sleeper=lambda _seconds: None,
    )
    failed = api.dispatch(
        Command(
            request_id="req-1",
            action="test_connection",
            project_id="project-a10",
            intent_id="intent-1",
            expected_revision=0,
        ),
        _SESSION,
    )
    assert failed.error is not None
    assert failed.error.code == "CONNECTIVITY_FAILED"
    # 台账已逐条落盘 4 条不可达事实（含每次重试）。
    assert len(store.load(endpoint.base_address).attempts) == 4

    # 新核心：不重新探测，启动水合即可读到历史结论。
    bridge_two = LocalAPIConnectionBridge(
        endpoint,
        store,
        probe=_ScriptedProbe([_SCRIPT_REACHABLE]),
        source_session="S-1",
    )
    api_two = LocalAPI(
        instance_id="core-new",
        workspace_id="ws",
        connector=bridge_two,
        connection_persistence=bridge_two,
        sleeper=lambda _seconds: None,
    )
    hydrated = api_two.connection_state
    assert hydrated["connected"] is False
    assert hydrated["attempts"] == 4
    assert hydrated["recovered"] is True

    # 再探测成功：历史不丢，结论翻转为可达。
    recovered = api_two.dispatch(
        Command(
            request_id="req-2",
            action="test_connection",
            project_id="project-a10",
            intent_id="intent-2",
            expected_revision=0,
        ),
        _SESSION,
    )
    assert recovered.error is None
    assert recovered.result["connected"] is True
    final_state = store.load(endpoint.base_address)
    assert final_state is not None
    assert len(final_state.attempts) == 5
    assert final_state.reachable is True


def test_connection_failure_degrades_only_test_connection(tmp_path: Path) -> None:
    endpoint = _endpoint()
    bridge = LocalAPIConnectionBridge(
        endpoint,
        ConnectionFactStore(tmp_path),
        probe=_ScriptedProbe([_SCRIPT_UNREACHABLE] * 10),
    )

    def local_handler(command: Command) -> dict[str, object]:
        return {"runs_locally": True}

    api = LocalAPI(
        instance_id="core",
        workspace_id="ws",
        handlers={"local_action": local_handler},
        connector=bridge,
        connection_persistence=bridge,
        sleeper=lambda _seconds: None,
    )
    # 外网能力不可用：test_connection 失败（重试口径由应用层统一给终局）。
    failed = api.dispatch(
        Command(
            request_id="req-fail",
            action="test_connection",
            project_id="project-a10",
            intent_id="intent-fail",
            expected_revision=0,
        ),
        _SESSION,
    )
    assert failed.error is not None
    assert failed.error.code == "CONNECTIVITY_FAILED"
    # 同核心上的本地动作不受影响。
    doctor = api.dispatch(
        Command(request_id="req-doc", action="doctor"), _SESSION
    )
    assert doctor.result is not None
    assert doctor.result["status"] == "READY"
    local = api.dispatch(
        Command(request_id="req-local", action="local_action"), _SESSION
    )
    assert local.result == {"runs_locally": True}


def test_bridge_probes_real_local_listener(tmp_path: Path) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        endpoint = EndpointConfig(
            "http", "127.0.0.1", port, f"http://127.0.0.1:{port}"
        )
        bridge = LocalAPIConnectionBridge(
            endpoint,
            ConnectionFactStore(tmp_path),
            timeout_seconds=2.0,
        )
        api = LocalAPI(
            instance_id="core",
            workspace_id="ws",
            connector=bridge,
            connection_persistence=bridge,
        )
        response = api.dispatch(
            Command(
                request_id="req-real",
                action="test_connection",
                project_id="project-a10",
                intent_id="intent-real",
                expected_revision=0,
            ),
            _SESSION,
        )
        assert response.error is None
        assert response.result is not None
        assert response.result["connected"] is True
        stored = ConnectionFactStore(tmp_path).load(endpoint.base_address)
        assert stored is not None
        assert stored.last_fact.reachable is True
    finally:
        listener.close()


def test_missing_credentials_do_not_degrade_local_capabilities(
    tmp_path: Path,
) -> None:
    secret = SecretManager.default()
    reference = f"a10-missing-{int(time.time() * 1000)}"
    # 未登记/未配置的凭据不可解析。
    assert secret.has_secret(reference, purpose="model") is False
    with pytest.raises(SecretUnavailable):
        secret.resolve(reference, purpose="model")

    # 同工作空间的文件独立核验不依赖凭据，照常工作。
    target = tmp_path / "artifact.bin"
    payload = b"independent verification payload"
    target.write_bytes(payload)
    import hashlib

    digest = hashlib.sha256(payload).hexdigest()
    fact = IndependentFileVerifier().verify(target, digest)
    assert fact.state.value == "verified"


@pytest.mark.skipif(shutil.which("git") is None, reason="本机无 git")
def test_git_source_works_without_any_credentials(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "a10@example.invalid"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "A10"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    (repo / "README.md").write_text("# demo", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    git = GitSourceControl()
    assert git.is_available()
    describe = git.describe(repo)
    assert describe["is_repository"] is True
    assert describe["branch"] in {"master", "main"}
    assert isinstance(describe["head_commit"], str)
    # 非仓库目录如实报 False，不被凭据/GitHub 能力影响。
    non_repo = tmp_path / "plain"
    non_repo.mkdir()
    assert git.is_repository(non_repo) is False
    # git 二进制不存在：明确不可用，不得静默转 plain/降级成“非仓库”。
    missing = GitSourceControl(executable="git-definitely-absent-a10")
    assert missing.is_available() is False
    with pytest.raises(GitUnavailable):
        missing.is_repository(repo)


# ------------------------------------------------------------ Windows 真实能力


@win_only
def test_windows_identity_probes_return_session_and_sid() -> None:
    from aitest.interfaces.local.pipe import (
        current_session_id,
        current_user_sid,
    )

    assert isinstance(current_session_id(), int)
    sid = current_user_sid()
    assert isinstance(sid, str)
    assert sid.startswith("S-1-")


@win_only
def test_windows_credential_end_to_end_never_hits_disk(tmp_path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    secret_value = "a10-secret-token-不要落盘"
    reference = "a10-cred-it"
    target = WindowsCredentialProvider._build_target("model", reference)

    class _Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredWriteW.argtypes = [
        ctypes.POINTER(_Credential), wintypes.DWORD
    ]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredDeleteW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.CredDeleteW.restype = wintypes.BOOL

    blob = secret_value.encode("utf-8")
    credential = _Credential()
    credential.Flags = 0
    credential.Type = 1  # CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(
        ctypes.create_string_buffer(blob), ctypes.POINTER(wintypes.BYTE)
    )
    credential.Persist = 1  # CRED_PERSIST_ENTERPRISE（当前用户）
    try:
        assert advapi32.CredWriteW(ctypes.byref(credential), 0)

        manager = SecretManager.default()
        assert manager.has_secret(reference, purpose="model") is True
        resolved = manager.resolve(reference, purpose="model")
        assert resolved.source == "windows_credential_manager"
        assert resolved.reveal() == secret_value
        # str/repr 绝不带正文。
        assert secret_value not in str(resolved)
        assert secret_value not in repr(resolved)
        resolved.clear()
        assert resolved.reveal() == ""
    finally:
        advapi32.CredDeleteW(target, 1, 0)

    assert SecretManager.default().has_secret(reference, purpose="model") is False
    # 凭据正文绝不出现在工作空间任何文件中。
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret_value.encode("utf-8") not in path.read_bytes()


@win_only
def test_real_pipe_connections_are_recorded_in_ledger(tmp_path: Path) -> None:
    workspace_id = "wsA10Ledger"
    endpoint = acquire_endpoint(
        tmp_path, workspace_id=workspace_id, wait_timeout_seconds=8.0
    )
    first_instance = endpoint.instance_id
    endpoint.connection.close()
    time.sleep(0.3)

    # 重连同实例：台账追加第二条 connected。
    reconnect = acquire_endpoint(
        tmp_path, workspace_id=workspace_id, wait_timeout_seconds=8.0
    )
    try:
        assert reconnect.instance_id == first_instance
    finally:
        reconnect.connection.close()
        assert shutdown_endpoint(tmp_path, workspace_id=workspace_id)

    ledger = CoreConnectionLedger(tmp_path)
    facts = ledger.facts()
    connected = [fact for fact in facts if fact["kind"] == "connected"]
    shutdowns = [
        fact for fact in facts if fact["kind"] == "shutdown_requested"
    ]
    # acquire ×2 + shutdown 自身建立的第三条已核对连接。
    assert len(connected) == 3
    assert len(shutdowns) == 1
    for fact in connected:
        assert fact["instance_id"] == first_instance
        assert isinstance(fact["source_session_id"], int)
        assert isinstance(fact["source_user_sid"], str)
        assert fact["source_user_sid"].startswith("S-1-")
    assert shutdowns[0]["instance_id"] == first_instance
