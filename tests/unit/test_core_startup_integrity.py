"""Actual creation, discovery, process birth and connection facts must agree."""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import aitest.bootstrap as bootstrap
from aitest.application.errors import WorkspaceInUse
from aitest.bootstrap import SystemProcessLauncher, await_core_launch_claim, make_pipe_connector
from aitest.infrastructure import path_compat
from aitest.infrastructure.file_store import core_launch
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.core_launch import FileCoreLaunchStore, ProcessFact
from aitest.infrastructure.file_store.locking import LifetimeWriterLock
from aitest.infrastructure.security import known_secrets
from aitest.interfaces.local import core_worker, pipe
from aitest.interfaces.local.editor_host import CoreStartupObservation, EditorHost

IDENTITY = "a" * 64
INSTANCE = "core-0123456789ab"


def _saved(root, **changes):
    value = dict(
        schema=FileCoreLaunchStore.SCHEMA,
        endpoint_workspace_id="workspace",
        instance_id=INSTANCE,
        state="created",
        process_id=12345,
        process_identity=IDENTITY,
        exit_code=None,
        reason=None,
    )
    value.update(changes)
    FileCoreLaunchStore(root).save(value)
    return value


class Child:
    pid = 12345

    def __init__(self, cmd, **kwargs):
        self.cmd, self.kwargs = cmd, kwargs
        self.code = None
        self.terminated = 0

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated += 1
        self.code = 1

    def wait(self, timeout):
        if self.code is None:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        return self.code


def _launcher(root, monkeypatch, *, probe=None):
    children = []

    def create(cmd, **kwargs):
        child = Child(cmd, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(bootstrap.subprocess, "Popen", create)
    launcher = SystemProcessLauncher(
        root,
        process_probe=probe or (lambda pid: ProcessFact(True, IDENTITY)),
    )
    return launcher, children


def test_failed_creation_does_not_publish_discovery(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("controlled creation failure")

    monkeypatch.setattr(bootstrap.subprocess, "Popen", fail)
    with pytest.raises((WorkspaceInUse, OSError)):
        SystemProcessLauncher(tmp_path).start("workspace")
    assert not (tmp_path / ".core-instance-id").exists()


def test_repeated_claim_preserves_first_child_and_discovery(tmp_path, monkeypatch):
    children = []

    class Alive:
        # Query the real current process birth, while mocking only Popen.
        pid = os.getpid()

        def __init__(self, cmd, **kwargs):
            children.append(cmd)

        def poll(self):
            return None

    monkeypatch.setattr(bootstrap.subprocess, "Popen", Alive)
    launcher = SystemProcessLauncher(tmp_path)
    first = launcher.start("workspace")
    second = SystemProcessLauncher(tmp_path).start("workspace")
    assert len(children) == 1
    assert first == second == (tmp_path / ".core-instance-id").read_text(encoding="utf-8")


def test_live_child_is_reused_across_launcher_instances(tmp_path, monkeypatch):
    first, children = _launcher(tmp_path, monkeypatch)
    instance = first.start("workspace")
    second = SystemProcessLauncher(tmp_path, process_probe=lambda pid: ProcessFact(True, IDENTITY))
    assert second.start("workspace") == instance
    assert len(children) == 1 and children[0].terminated == 0


def test_two_hosts_cannot_create_children_during_one_claim(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    children = []
    errors = []

    def create(cmd, **kwargs):
        child = Child(cmd, **kwargs)
        children.append(child)
        entered.set()
        assert release.wait(3)
        return child

    monkeypatch.setattr(bootstrap.subprocess, "Popen", create)
    first = SystemProcessLauncher(tmp_path, process_probe=lambda pid: ProcessFact(True, IDENTITY))

    def start():
        try:
            first.start("workspace")
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=start)
    thread.start()
    assert entered.wait(3)
    second = SystemProcessLauncher(tmp_path, process_probe=lambda pid: ProcessFact(True, IDENTITY))
    try:
        with pytest.raises(WorkspaceInUse, match="claiming"):
            second.start("workspace")
        assert len(children) == 1
        assert not first.instance_id_path.exists()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and errors == []
    assert second.start("workspace") == first.instance_id_path.read_text(encoding="utf-8")
    assert len(children) == 1


def test_absent_discovery_does_not_bypass_existing_os_writer_lock(tmp_path, monkeypatch):
    lock = LifetimeWriterLock(tmp_path / "writer.lock")
    lock.acquire()
    launcher, children = _launcher(tmp_path, monkeypatch)
    try:
        with pytest.raises(WorkspaceInUse):
            launcher.start("workspace")
        assert lock.held and children == [] and not launcher.instance_id_path.exists()
        assert FileCoreLaunchStore(tmp_path).read() is None
    finally:
        lock.release()


def test_stopping_core_is_waited_until_verified_exit_before_replacement(tmp_path, monkeypatch):
    _saved(tmp_path, state="stopping", reason="shutdown_requested")
    facts = iter(
        [ProcessFact(True, IDENTITY), ProcessFact(False, IDENTITY, 0), ProcessFact(True, "b" * 64)]
    )
    launcher, children = _launcher(tmp_path, monkeypatch, probe=lambda pid: next(facts))
    monkeypatch.setattr(bootstrap.time, "sleep", lambda seconds: None)
    assert launcher.start("workspace") != INSTANCE
    assert len(children) == 1 and children[0].terminated == 0


def test_stopping_timeout_preserves_living_core_instead_of_declaring_exit(tmp_path, monkeypatch):
    value = _saved(tmp_path, state="stopping", reason="shutdown_requested")
    launcher, children = _launcher(tmp_path, monkeypatch)
    clock = iter([0.0, 2.1])
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: next(clock))
    with pytest.raises(WorkspaceInUse, match="shutdown has not completed"):
        launcher.start("workspace")
    assert children == [] and FileCoreLaunchStore(tmp_path).read() == value


@pytest.mark.parametrize("state", ["creating", "publication_uncertain"])
def test_uncertain_creation_blocks_respawn_and_preserves_facts(tmp_path, monkeypatch, state):
    _saved(
        tmp_path,
        state=state,
        process_id=None,
        process_identity=None,
        reason="publication_failed" if state == "publication_uncertain" else None,
    )
    before = FileCoreLaunchStore(tmp_path).path.read_bytes()
    launcher, children = _launcher(tmp_path, monkeypatch)
    with pytest.raises(WorkspaceInUse, match="uncertain"):
        launcher.start("workspace")
    assert children == [] and FileCoreLaunchStore(tmp_path).path.read_bytes() == before


def test_legacy_unresponsive_pointer_does_not_authorize_new_child(tmp_path, monkeypatch):
    (tmp_path / ".core-instance-id").write_text(INSTANCE, encoding="utf-8")
    launcher, children = _launcher(tmp_path, monkeypatch)
    with pytest.raises(WorkspaceInUse, match="legacy"):
        launcher.start("workspace")
    assert children == []
    assert (tmp_path / ".core-instance-id").read_text(encoding="utf-8") == INSTANCE


def test_live_core_cannot_be_reassigned_to_another_endpoint(tmp_path, monkeypatch):
    value = _saved(tmp_path)
    launcher, children = _launcher(tmp_path, monkeypatch)
    with pytest.raises(WorkspaceInUse, match="different endpoint"):
        launcher.start("other-workspace")
    assert children == [] and FileCoreLaunchStore(tmp_path).read() == value


def test_unqueryable_existing_process_is_not_presumed_dead(tmp_path, monkeypatch):
    value = _saved(tmp_path)
    launcher, children = _launcher(tmp_path, monkeypatch, probe=lambda pid: ProcessFact(None))
    with pytest.raises(WorkspaceInUse, match="cannot be verified"):
        launcher.start("workspace")
    assert children == [] and FileCoreLaunchStore(tmp_path).read() == value


@pytest.mark.parametrize("replacement", [ProcessFact(False), ProcessFact(True, "b" * 64)])
def test_verified_exit_or_reused_pid_permits_new_claim_without_killing_foreign_process(
    tmp_path,
    monkeypatch,
    replacement,
):
    _saved(tmp_path)
    probes = iter([replacement, ProcessFact(True, "c" * 64)])
    launcher, children = _launcher(tmp_path, monkeypatch, probe=lambda pid: next(probes))
    result = launcher.start("workspace")
    assert result != INSTANCE and len(children) == 1 and children[0].terminated == 0
    history = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (tmp_path / "core/launch-history").glob("*.json")
    ]
    assert any(item["state"] == "exited" and item["instance_id"] == INSTANCE for item in history)


@pytest.mark.parametrize("phase", ["created_fact", "pointer"])
def test_publication_failure_stops_only_owned_child_and_preserves_old_pointer(
    tmp_path,
    monkeypatch,
    phase,
):
    _saved(tmp_path, state="exited", exit_code=0, reason="process_exited")
    (tmp_path / ".core-instance-id").write_text(INSTANCE, encoding="utf-8")
    launcher, children = _launcher(tmp_path, monkeypatch)
    if phase == "created_fact":
        original = launcher._launches.save

        def save(value):
            if value["state"] == "created":
                raise OSError("controlled header publication failure")
            original(value)

        monkeypatch.setattr(launcher._launches, "save", save)
    else:

        def publish(*args):
            raise OSError("controlled pointer publication failure")

        monkeypatch.setattr(launcher._launches, "publish_instance_id", publish)
    with pytest.raises(WorkspaceInUse, match="publication failed"):
        launcher.start("workspace")
    assert len(children) == 1 and children[0].terminated == 1
    assert (tmp_path / ".core-instance-id").read_text(encoding="utf-8") == INSTANCE
    fact = FileCoreLaunchStore(tmp_path).read()
    assert fact["state"] == "exited" and fact["exit_code"] == 1


def test_unverified_birth_retains_uncertain_state_after_stopping_owned_child(tmp_path, monkeypatch):
    launcher, children = _launcher(tmp_path, monkeypatch, probe=lambda pid: ProcessFact(None))
    with pytest.raises(WorkspaceInUse):
        launcher.start("workspace")
    assert children[0].terminated == 1 and not launcher.instance_id_path.exists()
    assert FileCoreLaunchStore(tmp_path).read()["state"] == "publication_uncertain"
    with pytest.raises(WorkspaceInUse, match="uncertain"):
        launcher.start("workspace")
    assert len(children) == 1


def test_failed_owned_termination_keeps_uncertain_creation_and_blocks_new_child(
    tmp_path, monkeypatch
):
    launcher, children = _launcher(tmp_path, monkeypatch)

    def fail_publish(*args):
        raise OSError("controlled publication failure")

    class Unstoppable(Child):
        def terminate(self):
            raise OSError("controlled termination failure")

    def create(cmd, **kwargs):
        child = Unstoppable(cmd, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(bootstrap.subprocess, "Popen", create)
    monkeypatch.setattr(launcher._launches, "publish_instance_id", fail_publish)
    with pytest.raises(WorkspaceInUse):
        launcher.start("workspace")
    assert FileCoreLaunchStore(tmp_path).read()["state"] == "publication_uncertain"
    with pytest.raises(WorkspaceInUse, match="uncertain"):
        launcher.start("workspace")
    assert len(children) == 1 and children[0].code is None


@pytest.mark.parametrize("code", [1, 3, 5, 7, 259])
def test_exact_early_exit_is_persisted_and_host_does_not_wait_full_timeout(
    tmp_path,
    monkeypatch,
    code,
):
    launcher, children = _launcher(tmp_path, monkeypatch)
    first = launcher.start("workspace")
    children[0].code = code
    monkeypatch.setattr(launcher, "start", lambda workspace: first)
    host = EditorHost(
        connector=lambda workspace: None,
        launcher=launcher,
        wait_timeout_seconds=5,
        poll_interval_seconds=0.01,
    )
    started = time.monotonic()
    with pytest.raises(WorkspaceInUse, match=f"exit_code={code}"):
        host.acquire("workspace")
    assert time.monotonic() - started < 1
    value = FileCoreLaunchStore(tmp_path).read()
    assert value["state"] == "exited" and value["exit_code"] == code
    other = SystemProcessLauncher(tmp_path)
    assert other.observe_start(first) == CoreStartupObservation("exited", code, "process_exited")


@pytest.mark.parametrize(
    "field,value",
    [
        ("process_id", True),
        ("process_id", 2**32),
        ("process_identity", "invalid"),
        ("exit_code", True),
        ("state", "ready"),
        ("reason", "secret raw traceback"),
    ],
)
def test_malformed_startup_fact_blocks_spawn_without_rewriting(tmp_path, monkeypatch, field, value):
    saved = _saved(tmp_path)
    saved[field] = value
    path = FileCoreLaunchStore(tmp_path).path
    path.write_text(json.dumps(saved), encoding="utf-8")
    original = path.read_bytes()
    launcher, children = _launcher(tmp_path, monkeypatch)
    with pytest.raises(WorkspaceInUse, match="cannot be verified"):
        launcher.start("workspace")
    assert children == [] and path.read_bytes() == original


def test_duplicate_and_oversized_startup_facts_are_rejected(tmp_path):
    store = FileCoreLaunchStore(tmp_path)
    value = _saved(tmp_path)
    store.path.write_text(json.dumps(value)[:-1] + ',"state":"created"}', encoding="utf-8")
    with pytest.raises(WorkspaceInUse):
        store.read()
    store.path.write_bytes(b" " * 8193)
    with pytest.raises(WorkspaceInUse):
        store.read()


@pytest.mark.parametrize("secret_part", ["identity", "serialized_pid"])
def test_known_credentials_block_before_any_startup_history_write(tmp_path, secret_part):
    value = dict(
        schema=FileCoreLaunchStore.SCHEMA,
        endpoint_workspace_id="workspace",
        instance_id=INSTANCE,
        state="created",
        process_id=12345,
        process_identity=IDENTITY,
        exit_code=None,
        reason=None,
    )
    known_secrets().register(IDENTITY if secret_part == "identity" else "12345")
    with pytest.raises(WorkspaceInUse, match="safe identity"):
        FileCoreLaunchStore(tmp_path).save(value)
    assert list(tmp_path.rglob("*")) == []


def test_junction_ancestor_is_rejected_before_claim_or_spawn(tmp_path, monkeypatch):
    original = path_compat.is_junction
    monkeypatch.setattr(
        path_compat, "is_junction", lambda path: Path(path) == tmp_path or original(path)
    )
    children = []
    monkeypatch.setattr(
        bootstrap.subprocess, "Popen", lambda *args, **kwargs: children.append(args)
    )
    with pytest.raises(WorkspaceInUse, match="filesystem link"):
        SystemProcessLauncher(tmp_path)
    assert children == [] and list(tmp_path.rglob("*")) == []


@pytest.mark.parametrize("name", ["../outside", "nested/id", ".", "", "C:/outside"])
def test_pointer_filename_cannot_escape_workspace(tmp_path, name):
    with pytest.raises(WorkspaceInUse):
        SystemProcessLauncher(tmp_path, instance_id_file=name)
    assert list(tmp_path.rglob("*")) == []


def test_child_gate_waits_for_matching_creation_and_pointer(tmp_path, monkeypatch):
    _saved(tmp_path, state="creating", process_id=None, process_identity=None)
    calls = []

    def publish(_seconds):
        calls.append("waiting")
        _saved(tmp_path, process_id=os.getpid())
        (tmp_path / ".core-instance-id").write_text(INSTANCE, encoding="utf-8")

    monkeypatch.setattr(bootstrap.time, "sleep", publish)
    monkeypatch.setattr(bootstrap, "probe_process", lambda pid: ProcessFact(True, IDENTITY))
    assert await_core_launch_claim(tmp_path, "workspace", INSTANCE)
    assert calls == ["waiting"]


@pytest.mark.parametrize(
    "change",
    [
        dict(instance_id="core-abcdefabcdef"),
        dict(endpoint_workspace_id="other"),
        dict(process_id=1),
        dict(process_identity="b" * 64),
        dict(state="exited", exit_code=5, reason="process_exited"),
    ],
)
def test_child_gate_rejects_foreign_or_dead_claim_before_assembly(tmp_path, monkeypatch, change):
    _saved(tmp_path, **(dict(process_id=os.getpid()) | change))
    (tmp_path / ".core-instance-id").write_text(INSTANCE, encoding="utf-8")
    monkeypatch.setattr(bootstrap, "probe_process", lambda pid: ProcessFact(True, IDENTITY))
    assert not await_core_launch_claim(tmp_path, "workspace", INSTANCE, timeout_seconds=0.1)


def test_worker_refuses_business_assembly_when_launch_gate_fails(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap, "await_core_launch_claim", lambda *a, **kw: False)
    monkeypatch.setattr(bootstrap, "assemble_workspace_core", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(sys, "platform", "win32")
    assert (
        core_worker.main(
            [
                "--workspace-root",
                str(tmp_path),
                "--workspace-id",
                "workspace",
                "--instance-id",
                INSTANCE,
                "--launch-claim",
            ]
        )
        == 7
    )
    assert calls == [] and list(tmp_path.rglob("*")) == []


@pytest.mark.parametrize(
    "pid,identity,alive,accepted",
    [
        (12345, IDENTITY, True, True),
        (54321, IDENTITY, True, False),
        (12345, "b" * 64, True, False),
        (12345, IDENTITY, None, False),
        (12345, IDENTITY, False, False),
    ],
)
def test_discovery_checks_actual_pipe_pid_and_birth(
    tmp_path,
    monkeypatch,
    pid,
    identity,
    alive,
    accepted,
):
    _saved(tmp_path)
    (tmp_path / ".core-instance-id").write_text(INSTANCE, encoding="utf-8")
    closed = []

    class Client:
        peer_process_id = pid

        def __init__(self, *args, **kwargs):
            pass

        def connect(self, **kwargs):
            pass

        def close(self):
            closed.append(True)

    monkeypatch.setattr(pipe, "NamedPipeClient", Client)
    monkeypatch.setattr(bootstrap, "probe_process", lambda p: ProcessFact(alive, identity))
    result = make_pipe_connector(tmp_path)("workspace")
    assert (result is not None) is accepted
    assert closed == ([] if accepted else [True])


@pytest.mark.parametrize(
    "fault", ["pid", "session", "foreign_session", "foreign_user", "empty_sid"]
)
def test_client_rejects_unverified_server_and_closes_handle(monkeypatch, fault):
    closed = []

    def pid(handle, output):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_ulong)).contents.value = 12345
        return fault != "pid"

    def session(handle, output):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_ulong)).contents.value = (
            2 if fault == "foreign_session" else 1
        )
        return fault != "session"

    kernel = SimpleNamespace(
        kernel32=SimpleNamespace(
            CreateFileW=lambda *a: 99,
            GetNamedPipeServerProcessId=pid,
            GetNamedPipeServerSessionId=session,
            GetCurrentProcessId=lambda: 67890,
            CloseHandle=lambda handle: closed.append(handle),
            CancelIoEx=lambda *args: True,
        )
    )
    client = pipe.NamedPipeClient.__new__(pipe.NamedPipeClient)
    client._init_io()
    client._kernel, client._handle, client._peer_pid = kernel, None, None
    client._session_id, client._pipe_name = 1, "controlled-pipe"

    def sid(kernel, process_id):
        if process_id == 12345:
            return (
                "foreign" if fault == "foreign_user" else (None if fault == "empty_sid" else "user")
            )
        return "user"

    monkeypatch.setattr(pipe, "_query_process_user_sid", sid)
    with pytest.raises(pipe.PipeUnavailable, match="服务端身份"):
        client.connect()
    assert closed == [99] and client.peer_process_id is None and client._handle is None


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows process birth")
def test_windows_probe_distinguishes_signaled_exit_259_from_living_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3); exit(259)"])
    try:
        live = core_launch.probe_process(child.pid)
        assert live.alive is True and live.identity
        assert child.wait(timeout=5) == 259
        dead = core_launch.probe_process(child.pid)
        assert dead == ProcessFact(False, live.identity, 259)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="actual child/pipe startup failure")
def test_real_worker_reports_blocked_recovery_exit_to_host(tmp_path):
    # Damage the authority referenced by current, rather than the obsolete
    # records.json pointer retained from the backed-up format migration.
    seeded = bootstrap.assemble_workspace_core(tmp_path, instance_id="core-seed")
    current = FileCommitStore(tmp_path).read_current()
    authority = tmp_path / "record-store" / f"{current['manifest']['record_header']['root']}.json"
    seeded.lifetime_lock.release()
    authority.write_text("{not-json", encoding="utf-8")
    launcher = SystemProcessLauncher(tmp_path)
    host = EditorHost(
        connector=make_pipe_connector(tmp_path),
        launcher=launcher,
        wait_timeout_seconds=8,
        poll_interval_seconds=0.02,
    )
    try:
        with pytest.raises(WorkspaceInUse, match="exit_code=5"):
            host.acquire("workspace")
        value = FileCoreLaunchStore(tmp_path).read()
        assert value["state"] == "exited" and value["exit_code"] == 5
        assert authority.read_text(encoding="utf-8") == "{not-json"
    finally:
        for child in launcher._children.values():
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="actual venv/core/pipe process identity")
def test_default_venv_launch_binds_actual_core_pid_and_preserves_dependencies(tmp_path):
    launcher = SystemProcessLauncher(tmp_path)
    host = EditorHost(
        connector=make_pipe_connector(tmp_path),
        launcher=launcher,
        wait_timeout_seconds=8,
        poll_interval_seconds=0.02,
    )
    endpoint = None
    try:
        endpoint = host.acquire("workspace")
        value = FileCoreLaunchStore(tmp_path).read()
        child = launcher._children[endpoint.instance_id]
        assert endpoint.connection.peer_process_id == child.pid == value["process_id"]
        assert core_launch.probe_process(child.pid) == ProcessFact(True, value["process_identity"])
        # Repeating a claim while a verified connection is held still reuses
        # the same actual interpreter; no new writer or pointer is created.
        assert launcher.start("workspace") == endpoint.instance_id
        assert len(launcher._children) == 1
        endpoint.connection.write_message(core_worker.shutdown_frame())
        endpoint.connection.read_message()
        assert child.wait(timeout=5) == 0
    finally:
        if endpoint is not None:
            endpoint.connection.close()
        for child in launcher._children.values():
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="actual current-interpreter venv contract")
def test_direct_interpreter_preserves_venv_package_loading_and_has_no_redirector():
    program, launcher = core_launch.python_launch_program(sys.executable)
    env = dict(os.environ)
    env.pop("__PYVENV_LAUNCHER__", None)
    if launcher is not None:
        env["__PYVENV_LAUNCHER__"] = launcher
    code = (
        "import os,sys,json,portalocker; "
        "print(json.dumps(dict(pid=os.getpid(),executable=sys.executable,"
        "prefix=sys.prefix,dependency=portalocker.__file__)),flush=True)"
    )
    with subprocess.Popen(
        [program, "-c", code], env=env, stdout=subprocess.PIPE, text=True, encoding="utf-8"
    ) as child:
        output, _ = child.communicate(timeout=5)
        result = json.loads(output)
        assert child.returncode == 0 and result["pid"] == child.pid
        assert Path(result["executable"]).resolve() == Path(sys.executable).resolve()
        assert Path(result["prefix"]).resolve() == Path(sys.prefix).resolve()
        import portalocker

        assert Path(result["dependency"]).resolve() == Path(portalocker.__file__).resolve()


def _immediate_client(begin, transferred):
    def result(handle, overlap, output, wait):
        assert overlap is not None
        ctypes.cast(output, ctypes.POINTER(ctypes.c_ulong)).contents.value = transferred()
        return True

    client = pipe.NamedPipeClient.__new__(pipe.NamedPipeClient)
    client._init_io()
    client._handle = 99
    client._kernel = SimpleNamespace(
        kernel32=SimpleNamespace(
            CreateEventW=lambda *args: 77,
            CloseHandle=lambda *args: True,
            CancelIoEx=lambda *args: True,
            GetOverlappedResult=result,
            WriteFile=begin,
            ReadFile=begin,
        )
    )
    return client


def test_client_completes_partial_writes_without_duplicating_frame():
    captured = bytearray()
    transferred = [0]

    def write(handle, buffer, size, output, overlap):
        assert output is None and overlap is not None
        transferred[0] = min(size, 3)
        captured.extend(bytes(buffer[: transferred[0]]))
        return True

    client = _immediate_client(write, lambda: transferred[0])
    payload = b'{"action":"query"}'
    client.write_message(payload)
    assert captured == len(payload).to_bytes(4, "big") + payload


@pytest.mark.parametrize("success,count", [(False, 2), (True, 0), (True, 999)])
def test_client_failed_writes_cannot_claim_complete_command(success, count):
    def write(handle, buffer, size, output, overlap):
        assert output is None and overlap is not None
        ctypes.set_last_error(5 if not success else 0)
        return success

    client = _immediate_client(write, lambda: count)
    with pytest.raises(pipe.PipeUnavailable):
        client.write_message(b"query")


def test_client_rejects_oversized_response_before_allocating_or_reading_body():
    calls = []
    client = pipe.NamedPipeClient.__new__(pipe.NamedPipeClient)
    client._handle = 99

    def read(size, *, deadline=None, on_wait=None):
        assert deadline is not None
        calls.append(size)
        assert size == 4
        return (pipe.MAX_MESSAGE_BYTES + 1).to_bytes(4, "big")

    client._read_exact = read
    with pytest.raises(pipe.PipeUnavailable, match="超过上限"):
        client.read_message()
    assert calls == [4]


@pytest.mark.parametrize("success,count", [(False, 2), (True, 0), (True, 999)])
def test_client_failed_reads_do_not_accept_partial_or_false_success(success, count):
    def read(handle, buffer, size, output, overlap):
        assert output is None and overlap is not None
        ctypes.set_last_error(5 if not success else 0)
        return success

    client = _immediate_client(read, lambda: count)
    with pytest.raises(pipe.PipeUnavailable):
        client._read_exact(4)


def test_valid_instance_filename_rejects_windows_device_names() -> None:
    """实例文件名不得是 Windows 设备名（3.13 用 ntpath.isreserved，3.12 用等语义回退）。"""
    from aitest.infrastructure.file_store.core_launch import valid_instance_filename

    for name in ("CON", "con", "Con.txt", "NUL", "PRN", "AUX", "COM1", "lpt9", " aux "):
        assert valid_instance_filename(name) is False, name
    for name in ("instance", "con-instance", "console", "COM10", "workspace-1"):
        assert valid_instance_filename(name) is True, name
    for name in (".", "..", "a/b", "a\\b", "", "x" * 129):
        assert valid_instance_filename(name) is False, name
