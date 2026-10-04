"""真实文件边界、中断窗口和跨包反例的根因回归。"""

import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import portalocker
import pytest
from pydantic import TypeAdapter

from aitest.application.execution.commit import ExecutionCommitBatch, ExecutionCommitCoordinator
from aitest.application.execution.facts import project_attempt_fact
from aitest.application.execution.runner import SerialRunner
from aitest.application.execution.source_checks import (
    SourceProbeObservation,
    SourceVerificationService,
)
from aitest.application.planning.model_orchestration import OUTBOUND_UNRESOLVED
from aitest.application.planning.rules_markdown import (
    rule_markdown_from_payload,
    rule_markdown_to_payload,
)
from aitest.application.project.context import create_project
from aitest.application.project.serialization import project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.contracts.queries import QuerySpec
from aitest.domain.execution.runs import (
    AttemptState,
    FailureClass,
    OutputStreamName,
    RecoveryCheckpoint,
    RecoveryRecord,
    StepRevisionRef,
)
from aitest.domain.execution.sources import SourceVerificationState
from aitest.domain.planning.model_outbound import ModelOutboundPolicy
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.adapters.execution.external_result import (
    ExternalResultAdapter,
    ExternalResultPayload,
)
from aitest.infrastructure.adapters.model import HttpResponse
from aitest.infrastructure.credentials import EnvironmentSecretProvider, SecretManager
from aitest.infrastructure.file_store import index as index_module
from aitest.infrastructure.file_store.backup import BackupError, FileBackupStore
from aitest.infrastructure.file_store.execution_handles import FileExecutionHandleStore
from aitest.infrastructure.file_store.index import FileQueryIndex
from aitest.infrastructure.file_store.integrity import check_workspace
from aitest.infrastructure.file_store.maintenance import detect_activity_blocker
from aitest.infrastructure.file_store.migrations import FileMigrationManager
from aitest.infrastructure.file_store.recovery import seal_inflight_outputs
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.security import (
    KnownSecretRegistry,
    StreamSecretFilter,
    UnsafeMaterialError,
)
from aitest.interfaces.local.api import EntryKind, Session
from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
from tests.unit.test_a_a16_migration_rollback import _workspace
from tests.unit.test_command_adapter import _request as command_request
from tests.unit.test_model_orchestration import (
    _IntentProbeCaller,
    _policy,
    _world,
)
from tests.unit.test_model_orchestration import (
    _request as model_request,
)
from tests.unit.test_rules_markdown import _payload as rule_payload
from tests.unit.test_serial_runner import _attempt, _request
from tests.unit.test_source_checks import _request as source_request


@pytest.mark.parametrize("secret", ["1234", "短凭据", "x"])
def test_registered_short_credentials_are_filtered_at_every_stream_split(secret: str) -> None:
    registry = KnownSecretRegistry()
    registry.register(secret)
    raw = ("hello " + secret + " goodbye\n").encode()
    expected = b"hello [REDACTED] goodbye\n"
    for boundary in range(len(raw) + 1):
        stream = StreamSecretFilter(registry)
        actual = stream.feed(raw[:boundary]) + stream.feed(raw[boundary:]) + stream.flush()
        assert actual == expected


def test_batch_spool_cannot_bypass_another_instances_live_capture(tmp_path: Path) -> None:
    from tests.unit.test_a_a09_prefilter_persistence import _block

    store = FileSpoolStore(tmp_path)
    writer = store.open_stream(
        run_id="run-1", step_id="step-1", attempt_id="att-1", stream_name=OutputStreamName.STDOUT
    )
    try:
        with pytest.raises(portalocker.exceptions.LockException):
            FileSpoolStore(tmp_path).persist_blocks((_block(b"foreign bytes"),))
        assert not store.read_manifest("att-1").blocks
        assert not list((tmp_path / "spool").rglob("*.bin"))
    finally:
        writer.abort()


def test_confirmed_collection_rejects_corrupted_output_bytes(tmp_path: Path) -> None:
    store, handles = FileSpoolStore(tmp_path), FileExecutionHandleStore(tmp_path)
    adapter = CommandAdapter(spool_store=store, handle_store=handles)
    adapter.register(CommandRegistration("python", sys.executable, tmp_path))
    handle = adapter.start(command_request("python", ("-c", "print('safe')")))
    deadline = time.monotonic() + 5
    result = adapter.collect(handle)
    while not result.complete and time.monotonic() < deadline:
        time.sleep(0.01)
        result = adapter.collect(handle)
    try:
        assert result.complete and result.output_blocks
        stream = store._stream_path(result.attempt_id, OutputStreamName.STDOUT)
        stream.write_bytes(b"evil\n")
        for current in (adapter, CommandAdapter(spool_store=store, handle_store=handles)):
            with pytest.raises(ValueError, match="content failed verification"):
                current.collect(handle)
    finally:
        adapter.request_stop(handle)


def test_model_identity_includes_the_frozen_template_reference() -> None:
    from aitest.application.planning.model_orchestration import ModelGenerationConflictError
    from aitest.domain.planning.templates import TemplateRef

    unit, reader = _world()
    caller = _IntentProbeCaller(reader)
    model_request(unit_of_work=unit, reader=reader, caller=caller, generation_request_id="same")
    with pytest.raises(ModelGenerationConflictError):
        model_request(
            unit_of_work=unit,
            reader=reader,
            caller=caller,
            generation_request_id="same",
            template_ref=TemplateRef("different", "2"),
        )
    assert caller.call_count == 1


def test_model_late_response_is_history_and_never_a_current_draft() -> None:
    from aitest.application.planning.model_orchestration import request_model_draft
    from aitest.application.planning.substrate import RecordQuery
    from aitest.domain.planning.model_outbound import MaterialKind, ModelTaskType, ResponseCurrency
    from tests.support.memory_model import MemoryCredentialResolver, MemoryProjector
    from tests.unit.test_model_orchestration import PROJECT_ID, FixedClock

    unit, reader = _world()
    caller = _IntentProbeCaller(reader)
    arguments = dict(
        project_id=PROJECT_ID,
        policy=_policy(),
        task_type=ModelTaskType.CHECK_CONTENT_DRAFT,
        selected_material={MaterialKind.PROJECT_CONTEXT: "safe context"},
        unit_of_work=unit,
        reader=reader,
        projector=MemoryProjector(),
        credentials=MemoryCredentialResolver(),
        caller=caller,
        clock=FixedClock(),
        source_revision=1,
        base_manual_revision=0,
        generation_request_id="late",
        basis_is_current=lambda: caller.call_count == 0,
    )
    outcome = request_model_draft(**arguments)
    assert outcome.content is None and outcome.response_currency is ResponseCurrency.SOURCE_CHANGED
    history = reader.query(
        RecordQuery(project_id=PROJECT_ID, aggregate_kind="model_outbound_request")
    ).items[-1]
    assert history.payload["historical_draft_text"] == "draft: proposed checks"
    assert history.payload["generated_content_id"] is None
    assert not reader.query(
        RecordQuery(project_id=PROJECT_ID, aggregate_kind="generated_content")
    ).items
    replayed = request_model_draft(**arguments)
    assert replayed.content is None and caller.call_count == 1


def test_corrupt_migration_cycle_cannot_select_another_backup(tmp_path: Path) -> None:
    from aitest.infrastructure.file_store.migrations import MigrationError

    _workspace(tmp_path, preexisting=False)
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(("0001-workspace-schema-version",))
    cycle = tmp_path / "migrations/cycles" / f"{plan.plan_id}.json"
    cycle.write_text(json.dumps({"plan_id": plan.plan_id, "state": "unknown"}), encoding="utf-8")
    before = (tmp_path / "workspace.json").read_bytes()
    with pytest.raises(MigrationError, match="周期状态损坏"):
        manager.apply(plan.plan_id)
    assert (tmp_path / "workspace.json").read_bytes() == before


def test_runner_keeps_the_same_intent_namespaced_by_project(tmp_path: Path) -> None:
    port = FakeExecutionPort()
    port.register(FakeExecutionSpec("attempt-1", "run-1", "step-1"))
    port.register(FakeExecutionSpec("attempt-2", "run-2", "step-2"))
    runner = SerialRunner(
        port, commit_coordinator=ExecutionCommitCoordinator(FileUnitOfWork(tmp_path))
    )
    runner.execute_attempt(_attempt(), _request())
    second = replace(
        _request(),
        project_id="project-2",
        run_id="run-2",
        step_id="step-2",
        attempt_id="attempt-2",
        authorization_ref=replace(
            _request().authorization_ref, step_id="step-2", authorization_id="authorization-2"
        ),
    )
    attempt = replace(_attempt(), run_id="run-2", step_id="step-2", attempt_id="attempt-2")
    assert runner.execute_attempt(attempt, second).state is AttemptState.COMPLETED
    assert port.execution_order == ["attempt-1", "attempt-2"]


def test_uow_refuses_rewriting_a_previously_fingerprinted_body(tmp_path: Path) -> None:
    registry = KnownSecretRegistry()
    registry.register("synthetic-password")
    unit = FileUnitOfWork(tmp_path, registry=registry)
    unit.begin("r", "p")
    try:
        with pytest.raises(UnsafeMaterialError):
            unit.stage_record(
                aggregate_kind="generated_content",
                record_id="draft",
                expected_revision=0,
                payload={"text": "synthetic-password", "content_digest": "sha256:old"},
            )
        assert not unit.pending
        assert not (tmp_path / "records.json").exists()
    finally:
        unit.rollback()


def test_ownerless_checkpoint_cannot_be_taken_over_by_another_project(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)
    for project, expected in (("a", 0), ("b", 1)):
        unit.begin(project, project)
        unit.stage_record(
            aggregate_kind="execution_checkpoint",
            record_id="attempt",
            expected_revision=expected,
            payload={"checkpoint": "owner lives in the commit ledger"},
        )
        if project == "a":
            unit.commit()
        else:
            with pytest.raises(ValueError, match="cross-project ownership"):
                unit.commit()
            unit.rollback()
    assert unit.repo.current_revision("execution_checkpoint", "attempt") == 1


@pytest.mark.parametrize("resume_first", [True, False])
def test_migration_retains_first_prestate_after_forward_then_registry_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resume_first: bool,
) -> None:
    _workspace(tmp_path, preexisting=False)
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(("0001-workspace-schema-version",))

    def lose_registry(**_kwargs: object) -> None:
        raise OSError("injected interruption after forward")

    monkeypatch.setattr(manager, "_save_registry", lose_registry)
    with pytest.raises(OSError, match="injected interruption"):
        manager.apply(plan.plan_id)
    assert "schema_version" in json.loads((tmp_path / "workspace.json").read_text())
    restarted = FileMigrationManager(tmp_path)
    if resume_first:
        restarted.resume(plan.plan_id)
    restarted.rollback(plan.plan_id)
    assert "schema_version" not in json.loads((tmp_path / "workspace.json").read_text())


def test_capture_lease_blocks_maintenance_and_foreign_instance_salvage(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    writer = store.open_stream(
        run_id="r",
        step_id="s",
        attempt_id="a",
        stream_name=OutputStreamName.STDOUT,
        block_size=1,
    )
    writer.append(b"first complete block\n")
    assert store.read_manifest("a").blocks
    assert detect_activity_blocker(tmp_path) is not None
    try:
        with pytest.raises(portalocker.exceptions.LockException):
            FileSpoolStore(tmp_path).salvage_streams("a")
    finally:
        writer.abort()
    assert detect_activity_blocker(tmp_path) is not None
    FileSpoolStore(tmp_path).salvage_streams("a")
    assert detect_activity_blocker(tmp_path) is None


def test_salvage_filters_only_unclaimed_tail_without_overwriting_sealed_prefix(
    tmp_path: Path,
) -> None:
    store = FileSpoolStore(tmp_path)
    writer = store.open_stream(
        run_id="r",
        step_id="s",
        attempt_id="a",
        stream_name=OutputStreamName.STDOUT,
        block_size=1,
    )
    writer.append(b"first complete block\n")
    writer.close()
    old = store.read_manifest("a").blocks
    original = [store.read_block(block) for block in old]
    registry = KnownSecretRegistry()
    registry.register("1234")
    with (tmp_path / "spool" / "a" / "stdout.log").open("ab") as handle:
        handle.write(b"tail 1234\n")
    recovered = FileSpoolStore(tmp_path, registry=registry)
    manifest = recovered.salvage_streams("a")
    assert [recovered.read_block(block) for block in old] == original
    assert recovered.read_block(manifest.blocks[-1]) == b"tail [REDACTED]\n"


def test_query_deep_pages_read_only_shards_after_the_last_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = FileQueryIndex(tmp_path, shard_size=8)
    index.rebuild(
        [
            {
                "project_id": "p",
                "aggregate_kind": "case",
                "record_id": f"c{number:03}",
                "revision": 1,
                "commit_sequence": number + 1,
            }
            for number in range(80)
        ]
    )
    reads: list[int] = []
    original = index_module._ShardDirectory._read_shard

    def count(directory, info):
        reads[-1] += 1
        return original(directory, info)

    monkeypatch.setattr(index_module._ShardDirectory, "_read_shard", count)
    spec = QuerySpec(project_id="p", sort="commit_sequence", limit=5)
    items = []
    while True:
        reads.append(0)
        page = index.query_spec(spec)
        assert page.status == "ok"
        items.extend(item["record_id"] for item in page.items)
        if page.next_cursor is None:
            break
        spec = spec.model_copy(update={"cursor": page.next_cursor})
    assert len(items) == len(set(items)) == 80
    assert max(reads) <= 2


def test_report_cursor_preserves_replaced_revision_in_original_root(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path, shard_size=8)
    rows = [
        {
            "project_id": "p",
            "aggregate_kind": "report",
            "record_id": name,
            "report_id": name,
            "revision": 1,
            "content_revision": 1,
            "published_sequence": number,
            "commit_sequence": number,
            "business_outcome": "passed",
        }
        for name, number in (("b", 1), ("a", 2))
    ]
    index.rebuild(rows)
    spec = QuerySpec(project_id="p", aggregate_kind="report", descending=True, limit=1)
    first = index.query_spec(spec)
    assert first.items[0]["report_id"] == "a"
    index.publish(
        [
            {
                **rows[0],
                "revision": 2,
                "content_revision": 2,
                "published_sequence": 3,
                "commit_sequence": 3,
            }
        ],
        commit_sequence=3,
    )
    second = index.query_spec(spec.model_copy(update={"cursor": first.next_cursor}))
    assert second.commit_id == 2
    assert [(item["report_id"], item["revision"]) for item in second.items] == [("b", 1)]


def test_real_uow_runner_can_complete_then_recall_without_restarting(tmp_path: Path) -> None:
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=0)
    )
    for _ in range(2):
        unit = FileUnitOfWork(tmp_path)
        runner = SerialRunner(port, commit_coordinator=ExecutionCommitCoordinator(unit))
        result = runner.execute_attempt(_attempt(), _request())
        assert result.state is AttemptState.COMPLETED
    assert port.execution_order == ["attempt-1"]


def test_poll_slice_continues_same_execution_after_runner_restart(tmp_path: Path) -> None:
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=1)
    )
    runner = SerialRunner(
        port, commit_coordinator=ExecutionCommitCoordinator(FileUnitOfWork(tmp_path))
    )
    yielded = runner.execute_attempt(_attempt(), _request(), max_polls=1)
    assert yielded.state is AttemptState.RUNNING
    resumed = SerialRunner(
        port, commit_coordinator=ExecutionCommitCoordinator(FileUnitOfWork(tmp_path))
    )
    assert (
        resumed.execute_attempt(_attempt(), _request(), max_polls=1).state is AttemptState.COMPLETED
    )
    assert port.execution_order == ["attempt-1"]


def test_execution_intent_conflict_checks_actual_entry_arguments(tmp_path: Path) -> None:
    port = FakeExecutionPort()
    port.register(FakeExecutionSpec("attempt-1", "run-1", "step-1"))
    runner = SerialRunner(
        port, commit_coordinator=ExecutionCommitCoordinator(FileUnitOfWork(tmp_path))
    )
    runner.execute_attempt(_attempt(), _request(), max_polls=1)
    request = _request()
    changed = replace(
        request, registered_entry=replace(request.registered_entry, arguments=("changed",))
    )
    restarted = SerialRunner(
        port, commit_coordinator=ExecutionCommitCoordinator(FileUnitOfWork(tmp_path))
    )
    with pytest.raises(ValueError, match="different input"):
        restarted.execute_attempt(_attempt(), changed)
    assert port.execution_order == ["attempt-1"]


def test_repeated_real_command_collection_preserves_exit_fact(tmp_path: Path) -> None:
    handles = FileExecutionHandleStore(tmp_path)
    adapter = CommandAdapter(spool_store=FileSpoolStore(tmp_path), handle_store=handles)
    adapter.register(CommandRegistration("python", sys.executable, tmp_path))
    handle = adapter.start(command_request("python", ("-c", "print('safe')")))
    deadline = time.monotonic() + 5
    first = adapter.collect(handle)
    while not first.complete and time.monotonic() < deadline:
        time.sleep(0.01)
        first = adapter.collect(handle)
    try:
        assert first.complete
        assert first.exit_fact_ref is not None
        assert adapter.collect(handle) == first
        restarted = CommandAdapter(spool_store=FileSpoolStore(tmp_path), handle_store=handles)
        assert restarted.collect(handle) == first
        assert detect_activity_blocker(tmp_path) is None
        assert check_workspace(tmp_path)["ok"]
        backup = tmp_path.parent / (tmp_path.name + "-backup")
        FileBackupStore(tmp_path).create(backup)
        assert FileBackupStore(tmp_path).verify(backup)["ok"]
        restored = tmp_path.parent / (tmp_path.name + "-restored")
        FileBackupStore(tmp_path).restore(backup=backup, target=restored)
        recovered = CommandAdapter(
            spool_store=FileSpoolStore(restored),
            handle_store=FileExecutionHandleStore(restored),
        )
        assert recovered.collect(handle) == first
    finally:
        adapter.request_stop(handle)


def test_rollback_can_repeat_after_reverse_write_then_registry_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace(tmp_path, preexisting=False)
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(("0001-workspace-schema-version",))
    manager.apply(plan.plan_id)
    monkeypatch.setattr(
        manager,
        "_save_registry",
        lambda **_: (_ for _ in ()).throw(OSError("injected rollback interruption")),
    )
    with pytest.raises(OSError, match="rollback interruption"):
        manager.rollback(plan.plan_id)
    assert "schema_version" not in json.loads((tmp_path / "workspace.json").read_text())
    restarted = FileMigrationManager(tmp_path)
    restarted.rollback(plan.plan_id)
    assert not restarted.inspect()["applied"]


def test_migration_new_cycle_resume_keeps_its_own_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace(tmp_path, preexisting=False)
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(("0001-workspace-schema-version",))
    old = manager.apply(plan.plan_id).backup_path
    manager.rollback(plan.plan_id)
    workspace = json.loads((tmp_path / "workspace.json").read_text())
    workspace["new_cycle_fact"] = "must be backed up"
    (tmp_path / "workspace.json").write_text(json.dumps(workspace), encoding="utf-8")
    monkeypatch.setattr(
        manager,
        "_save_registry",
        lambda **_: (_ for _ in ()).throw(OSError("injected new-cycle interruption")),
    )
    with pytest.raises(OSError):
        manager.apply(plan.plan_id)
    resumed = FileMigrationManager(tmp_path).resume(plan.plan_id)
    assert resumed.backup_path is not None and resumed.backup_path != old
    assert "new_cycle_fact" in (resumed.backup_path / "workspace.json").read_text()


def test_sealed_spool_does_not_make_a_live_execution_safe_for_maintenance(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    handles = FileExecutionHandleStore(tmp_path)
    adapter = CommandAdapter(spool_store=store, handle_store=handles)
    adapter.register(CommandRegistration("python", sys.executable, tmp_path))
    handle = adapter.start(
        command_request(
            "python",
            (
                "-c",
                "import time; time.sleep(20)",
            ),
        )
    )
    try:
        # 故障注入：采集侧先封口，真实子进程及 Job 仍存活。
        for writer in adapter._runtimes[handle.handle_id].writers.values():
            writer.close()
        states = list((tmp_path / "spool").rglob("capture-*.json"))
        assert len(states) == 2
        assert all(json.loads(p.read_text())["state"] == "sealed" for p in states)
        assert adapter.inspect(handle).process_reachable
        assert detect_activity_blocker(tmp_path) is not None
        assert seal_inflight_outputs(tmp_path) == ()
    finally:
        reattached = CommandAdapter(spool_store=store, handle_store=handles)
        stop = reattached.request_stop(handle)
        assert stop.stop_confirmed
        assert reattached.request_stop(handle) == stop
        collected = adapter.collect(handle)
        deadline = time.monotonic() + 5
        while not collected.complete and time.monotonic() < deadline:
            time.sleep(0.01)
            collected = adapter.collect(handle)
        assert collected.complete
        assert collected.exit_fact_ref.termination_reason.value == "confirmed_stop"
        assert detect_activity_blocker(tmp_path) is None


def test_reader_metadata_failure_cannot_be_published_as_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileSpoolStore(tmp_path)

    def fail(*_args, **_kwargs):
        raise OSError("synthetic metadata failure")

    monkeypatch.setattr(store, "persist_redaction_summary", fail)
    adapter = CommandAdapter(spool_store=store)
    adapter.register(CommandRegistration("python", sys.executable, tmp_path))
    handle = adapter.start(command_request("python", ("-c", "print('safe')")))
    deadline = time.monotonic() + 5
    while adapter.inspect(handle).process_reachable and time.monotonic() < deadline:
        time.sleep(0.01)
    result = adapter.collect(handle)
    assert not result.complete
    assert result.exit_fact_ref is None
    assert result.capture_completeness.value == "gap"


def test_default_core_model_flow_persists_filtered_draft_and_reuses_original_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_value = "synthetic-model-credential-1234"
    monkeypatch.setenv("AITEST_REGRESSION_MODEL_SECRET", secret_value)
    manager = SecretManager(
        (
            EnvironmentSecretProvider(
                {
                    ("model", "test-model"): "AITEST_REGRESSION_MODEL_SECRET",
                }
            ),
        )
    )
    policy = _policy()
    core = assemble_workspace_core(
        tmp_path,
        instance_id="model-regression",
        model_endpoint=policy.endpoint.address,
        model_secret_reference=("model", "test-model"),
        secret_manager=manager,
    )
    calls = []

    class Transport:
        def post(self, url, *, headers, body, timeout_seconds):
            calls.append(url)
            assert secret_value not in body.decode()
            assert headers["Authorization"] == "Bearer " + secret_value
            return HttpResponse(
                200,
                json.dumps(
                    {
                        "id": secret_value,
                        "choices": [{"message": {"content": "draft " + secret_value}}],
                    }
                ).encode(),
            )

    core.model_provider._transport = Transport()
    human = Session(session_id="human-model", entry_kind=EntryKind.HUMAN_UI)
    relay = Session(session_id="relay-model", entry_kind=EntryKind.AGENT_RELAY)
    project = create_project(
        project_id=policy.project_id,
        workspace_id=core.workspace.workspace_id,
        name="model regression",
        goal="draft only",
        created_at_commit="0",
    )
    try:
        context = Command(
            request_id="context-request",
            intent_id="context-intent",
            action="save_context",
            project_id=policy.project_id,
            expected_revision=0,
            parameters={"project": project_to_payload(project)},
        )
        assert core.api.dispatch(context, human).error is None
        policy_command = Command(
            request_id="policy-request",
            intent_id="policy-intent",
            action="save_model_outbound_policy",
            project_id=policy.project_id,
            expected_revision=0,
            parameters={
                "project_revision": 1,
                "policy": TypeAdapter(ModelOutboundPolicy).dump_python(
                    policy,
                    mode="json",
                ),
            },
        )
        assert core.api.dispatch(policy_command, relay).error is not None
        policy_result = core.api.dispatch(
            policy_command.model_copy(update={"request_id": "policy-human"}), human
        )
        assert policy_result.error is None, policy_result.error
        command = Command(
            request_id="draft-request",
            intent_id="draft-intent",
            action="generate_draft",
            project_id=policy.project_id,
            expected_revision=0,
            parameters={
                "generation_mode": "model",
                "project_revision": 1,
                "policy_revision": 1,
                "source_revision": 1,
                "base_manual_revision": 0,
                "task_type": "check_content_draft",
                "draft_kind": "check_content",
                "selected_material": {"project_context": "safe selected context"},
            },
        )
        first = core.api.dispatch(command, relay)
        assert first.error is None, first.error
        assert first.result["status"] == "draft_ready", first.result
        second = core.api.dispatch(command.model_copy(update={"request_id": "draft-repeat"}), relay)
        assert second.error is None
        assert second.result["content"] == first.result["content"]
        assert len(calls) == 1
        core.lifetime_lock.release()
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert secret_value.encode() not in path.read_bytes(), path
        assert any(
            b"[REDACTED]" in path.read_bytes()
            for path in (tmp_path / "record-store").glob("*.json")
        )
    finally:
        core.lifetime_lock.release()


def test_real_uow_freezes_execution_snapshot_at_actual_publication_boundary(tmp_path: Path) -> None:
    from tests.support.persistent_evidence_fixture import fixture_facts, save_fixture_bytes
    fixture = Path(__file__).parents[1] / "contracts/fixtures/execution_facts/success.json"
    facts = ExecutionFacts.model_validate_json(fixture.read_text(encoding="utf-8"))
    frozen = facts.attempts[0]
    attempt = replace(
        _attempt(),
        state=AttemptState.COMPLETED,
        step_revision_ref=StepRevisionRef(
            frozen.step_revision_ref.step_revision_id,
            frozen.step_revision_ref.revision_no,
            frozen.step_revision_ref.digest,
        ),
    )
    checkpoint = RecoveryRecord(
        RecoveryCheckpoint(attempt.run_id, attempt.step_id, attempt.attempt_id, "completed"),
        attempt,
    )
    facts = facts.model_copy(update={"attempts": (project_attempt_fact(attempt, is_current=True),)})
    unit = FileUnitOfWork(tmp_path)
    for revision in (1, 2):
        save_fixture_bytes(tmp_path, facts.project_id)
        facts = fixture_facts(facts)
        unit.begin(f"publish-{revision}", facts.project_id)
        result = ExecutionCommitCoordinator(unit).stage_and_commit(
            ExecutionCommitBatch(
                checkpoint=checkpoint,
                evidence_refs=(),
                facts=facts,
            )
        )
        assert result.facts.snapshot_cursor == result.committed["commit_sequence"]
        assert result.facts.snapshot_commit_id == f"commit-{result.committed['commit_sequence']}"
        assert result.facts.snapshot_revision == 1
        stored = unit.repo.read(
            aggregate_kind="execution_facts",
            record_id=result.facts.snapshot_commit_id,
            revision=1,
        )
        assert stored.payload == result.facts.model_dump(mode="json")
        assert unit.repo.current_revision("execution_checkpoint", attempt.attempt_id) == revision


def test_launch_failure_releases_all_spool_leases_without_erasing_uncertain_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileSpoolStore(tmp_path)
    original = store.open_stream

    def fail_stderr(**parameters):
        if parameters["stream_name"] is OutputStreamName.STDERR:
            raise OSError("injected second stream failure")
        return original(**parameters)

    monkeypatch.setattr(store, "open_stream", fail_stderr)
    handles = FileExecutionHandleStore(tmp_path)
    adapter = CommandAdapter(spool_store=store, handle_store=handles)
    adapter.register(CommandRegistration("python", sys.executable, tmp_path))
    with pytest.raises(OSError, match="second stream failure"):
        adapter.start(command_request("python", ("-c", "import time; time.sleep(20)")))
    assert handles.find_by_attempt("attempt-1") is not None
    assert not adapter._runtimes
    assert detect_activity_blocker(tmp_path) is not None
    # 能取跨实例采集锁：失败路径已释放首个 writer，且没有删除不确定事实。
    FileSpoolStore(tmp_path).salvage_streams("attempt-1")


@pytest.mark.skipif(os.name != "nt", reason="真实 Windows Junction 边界")
def test_backup_refuses_junction_parent_before_any_restore_write(tmp_path: Path) -> None:
    import _winapi

    unit = FileUnitOfWork(tmp_path / "workspace")
    unit.begin("save", "p")
    unit.stage_record(
        aggregate_kind="case", record_id="c", expected_revision=0, payload={"project_id": "p"}
    )
    unit.commit()
    backup = tmp_path / "backup"
    FileBackupStore(unit.workspace.root).create(backup)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "junction"
    _winapi.CreateJunction(str(outside), str(link))
    assert link.is_junction()
    with pytest.raises(BackupError, match="链接"):
        FileBackupStore(unit.workspace.root).restore(backup=backup, target=link / "new-target")
    assert not list(outside.iterdir())


def test_missing_task_cannot_be_saved_as_delivery(tmp_path: Path) -> None:
    from aitest.application.project.persistence import save_delivery
    from tests.unit.test_task_and_delivery_persistence import PROJECT_ID, _delivery, _start

    stack = _start(tmp_path)
    with pytest.raises((ValueError, KeyError)):
        save_delivery(
            _delivery(),
            project_id=PROJECT_ID,
            unit_of_work=stack.unit_of_work,
            reader=stack.reader,
            expected_revision=None,
        )
    assert not (tmp_path / "records.json").exists()


@pytest.mark.parametrize(
    "action,parameter", [("save_context", "project"), ("save_binding", "binding")]
)
def test_public_b_entry_refuses_payload_from_another_project(tmp_path, action, parameter):
    from tests.contracts.test_b_use_case_registration import (
        _api,
        _binding_payload,
        _project_payload,
        _session,
        _start,
        _write_command,
    )

    stack = _start(tmp_path)
    payload = _project_payload() if action == "save_context" else _binding_payload()
    command = _write_command(
        action=action, request_id="foreign-entry", parameters={parameter: payload}
    )
    command = command.model_copy(update={"project_id": "foreign-project"})
    response = _api(stack).dispatch(command, _session())
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert not (tmp_path / "records.json").exists()


def test_legacy_graph_remains_unverified_and_keeps_history_payload_unchanged() -> None:
    from aitest.application.project.serialization import dependency_graph_from_payload

    payload = {"project_id": "p", "modules": [], "dependencies": [], "revision": 1}
    graph = dependency_graph_from_payload(payload)
    assert graph.edges_declared is False
    assert "edges_declared" not in payload


def test_matching_request_and_wrong_plan_cannot_override_execution_facts_plan() -> None:
    from aitest.domain.planning.runtime_revision import RuntimeRevisionRefusalCode
    from tests.unit.test_runtime_revision import _codes, _decide, _failure_in_progress, _plan

    decision = _decide(
        _failure_in_progress(), plan=_plan(plan_id="unrelated"), base_plan_revision_id="unrelated"
    )
    assert not decision.accepted
    assert RuntimeRevisionRefusalCode.PLAN_REVISION_MISMATCH in _codes(decision)


def test_gate_failure_never_executes_the_guarded_handler() -> None:
    from aitest.interfaces.local.api import LocalAPI

    class FailedGate:
        def check(self, action):
            raise OSError("synthetic gate failure")

    calls = []
    api = LocalAPI(
        instance_id="i",
        workspace_id="w",
        handlers={"query": lambda _: calls.append(1)},
        capability_gate=FailedGate(),
    )
    result = api.dispatch(
        Command(request_id="r", action="query", project_id="p"),
        Session(session_id="s", entry_kind=EntryKind.AGENT_RELAY),
    )
    assert result.error is not None
    assert result.error.code == "CAPABILITY_DEGRADED"
    assert calls == []


def test_digest_alone_never_proves_runtime_load_source(tmp_path: Path) -> None:
    outcome = SourceVerificationService().check(
        source_request(tmp_path),
        SourceProbeObservation(
            observed_source_digest="sha256:source-1",
            failure_class=FailureClass.PASSED,
        ),
    )
    assert outcome.verification.state is SourceVerificationState.UNVERIFIED
    assert outcome.check_result.failure_class is not FailureClass.PASSED


def test_source_mismatch_overrides_probe_passed(tmp_path: Path) -> None:
    outcome = SourceVerificationService().check(
        source_request(tmp_path),
        SourceProbeObservation(
            observed_source_digest="sha256:different",
            failure_class=FailureClass.PASSED,
        ),
    )
    assert outcome.verification.state is SourceVerificationState.MISMATCH
    assert outcome.check_result.failure_class is FailureClass.SOURCE_ERROR


@pytest.mark.parametrize(
    "text",
    [
        "## detail\nbody",
        "## 步骤\nbody",
        "\nbody\n",
        "```python\n## heading\n```",
        "~~~\n## heading\n~~~",
    ],
)
def test_arbitrary_rule_body_is_lossless_in_markdown(text: str) -> None:
    payload = rule_payload(text=text, steps=("first\nsecond", " trailing "))
    assert rule_markdown_to_payload(rule_markdown_from_payload(payload)) == payload


@pytest.mark.parametrize("change", ["assertions", "source", "attachments", "expected"])
def test_external_import_identity_includes_all_decisive_inputs(change: str) -> None:
    adapter = ExternalResultAdapter()
    payload = ExternalResultPayload(
        "i", "schema/1", "instance", "record", {"same": True}, {"ok": False}
    )
    adapter.validate(
        payload, expected_schema="schema/1", expected_assertions={"ok": True}, verification_id="v1"
    )
    expected = {"ok": True}
    if change == "assertions":
        payload = replace(payload, assertion_values={"ok": True})
    elif change == "source":
        payload = replace(payload, source_record_id="different")
    elif change == "attachments":
        payload = replace(payload, attachment_refs=("different",))
    else:
        expected = {"ok": False}
    with pytest.raises(ValueError, match="conflicts"):
        adapter.validate(
            payload, expected_schema="schema/1", expected_assertions=expected, verification_id="v2"
        )


def test_unreadable_successful_model_draft_does_not_call_provider_again() -> None:
    unit, reader = _world()
    caller = _IntentProbeCaller(reader)
    assert (
        model_request(
            unit_of_work=unit, reader=reader, caller=caller, generation_request_id="g"
        ).content
        is not None
    )
    original = reader.read

    def unreadable(**kwargs):
        if kwargs["aggregate_kind"] == "generated_content":
            raise OSError("saved draft unavailable")
        return original(**kwargs)

    reader.read = unreadable
    outcome = model_request(
        unit_of_work=unit, reader=reader, caller=caller, generation_request_id="g"
    )
    assert outcome.status == OUTBOUND_UNRESOLVED
    assert outcome.content is None
    assert caller.call_count == 1


def test_two_file_uows_claim_one_model_generation_without_repeating_the_call(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from aitest.application.planning.model_ports import ModelCallResult, ModelCallStatus
    from aitest.application.planning.substrate_adapter import PortsRecordReader, PortsUnitOfWork

    raw_one, raw_two = FileUnitOfWork(tmp_path), FileUnitOfWork(tmp_path)
    one = PortsUnitOfWork(raw_one, repository=raw_one.repo)
    two = PortsUnitOfWork(raw_two, repository=raw_two.repo)
    entered, release = Event(), Event()

    class Caller:
        calls = 0

        def call(self, request):
            self.calls += 1
            entered.set()
            assert release.wait(5)
            return ModelCallResult(status=ModelCallStatus.OK, draft_text="original draft")

    caller = Caller()
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(
            model_request,
            unit_of_work=one,
            reader=PortsRecordReader(raw_one.repo),
            caller=caller,
            generation_request_id="concurrent-generation",
        )
        try:
            assert entered.wait(5)
            second = model_request(
                unit_of_work=two,
                reader=PortsRecordReader(raw_two.repo),
                caller=caller,
                generation_request_id="concurrent-generation",
            )
            assert second.status == OUTBOUND_UNRESOLVED
            assert caller.calls == 1
        finally:
            release.set()
        original = first.result(timeout=5)
    recalled = model_request(
        unit_of_work=two,
        reader=PortsRecordReader(raw_two.repo),
        caller=caller,
        generation_request_id="concurrent-generation",
    )
    assert recalled.content == original.content
    assert caller.calls == 1
