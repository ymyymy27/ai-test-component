"""Isolated fix rechecks. Temporary synthetic data; no external requests."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import traceback
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path.cwd()))

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.recovery import recover_attempt
from aitest.application.execution.runner import SerialRunner
from aitest.application.project.context import detect_context_gaps, move_binding, register_graph
from aitest.application.project.persistence import load_delivery, save_delivery
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.execution.runs import (
    AttemptState,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    OutputStreamName,
    RecoveryCheckpoint,
)
from aitest.domain.project.context import (
    BindingForm,
    Delivery,
    DriveKind,
    EnvironmentRef,
    LocalProject,
    LocalProjectBinding,
    Module,
    ModuleDependencyGraph,
)
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
from aitest.infrastructure.file_store.backup import FileBackupStore
from aitest.infrastructure.file_store.checkpoints import FileCheckpointStore
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.execution_handles import (
    FileExecutionHandleStore,
    PersistedExecutionHandle,
)
from aitest.infrastructure.file_store.integrity import check_workspace
from aitest.infrastructure.file_store.migrations import FileMigrationManager
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, Session
from aitest.interfaces.local.pipe import NamedPipeServer, check_peer_identity, current_user_sid
from tests.recovery.test_execution_recovery import _attempt as recovery_attempt
from tests.recovery.test_execution_recovery import _handle
from tests.unit.test_runtime_revision import _case, _change, _decide, _failure_in_progress, _plan
from tests.unit.test_serial_runner import FakeExecutionPort, _attempt, _request


def snapshot(root: Path):
    source = root / "source"
    source.mkdir(parents=True)
    (source / "a.py").write_text("x=1\n", encoding="utf-8")
    store = FileSourceSnapshotStore(root / "workspace")
    selected = store.pin(canonical_path=str(source), purpose="selected", selected_paths=("a.py",))
    whole = store.pin(canonical_path=str(source), purpose="whole")
    (source / "b.py").write_text("x=2\n", encoding="utf-8")
    drift = store.detect_changes(whole["snapshot_id"])
    assert selected["snapshot_id"] != whole["snapshot_id"] and drift["state"] == "changed"
    return {"A-15": "original counterexample fixed", "different_scope_ids": True, "drift": drift}


def migration(root: Path):
    root.mkdir(parents=True)
    before_w = {"workspace_id": "existing", "schema_version": "1.0", "writer_epoch": 1}
    before_r = {"records": {}, "commit": 0, "intents": {}}
    (root / "workspace.json").write_text(json.dumps(before_w), encoding="utf-8")
    (root / "records.json").write_text(json.dumps(before_r), encoding="utf-8")
    manager = FileMigrationManager(root)
    plan = manager.plan(("0001-workspace-schema-version", "0002-records-intents-container"))
    manager.apply(plan.plan_id)
    manager.rollback(plan.plan_id)
    after_w = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
    after_r = json.loads((root / "records.json").read_text(encoding="utf-8"))
    assert before_w == after_w and before_r == after_r
    return {"A-16": "original counterexample fixed", "preexisting_fields_preserved": True}


def events(root: Path):
    journal = FileEventJournal(root, instance_id="events")
    unit = FileUnitOfWork(root, journal=journal)
    unit.begin(request_id="req", project_id="p")
    for kind in ("rule_draft", "rule_version"):
        unit.stage_record(
            aggregate_kind=kind,
            record_id="same",
            expected_revision=None,
            payload={"project_id": "p"},
        )
    unit.commit()
    count = len(journal.read().events)
    assert count == 2
    return {"A-17": "original counterexample fixed", "events": count}


def sid(root: Path):
    if os.name != "nt":
        return {"state": "not_run", "reason": "Windows only"}
    server = NamedPipeServer("audit", instance_id="audit")
    expected, observed = current_user_sid(), server._process_user_sid(os.getpid())
    denied = False
    try:
        check_peer_identity(
            client_session_id=1, expected_session_id=1, client_user_sid="", expected_user_sid=""
        )
    except Exception:
        denied = True
    assert expected and expected == observed and denied
    return {
        "A-18": "original counterexample fixed",
        "own_sid_matches": True,
        "empty_sids_rejected": denied,
        "foreign_user_connection": "not_run",
    }


def project_and_publish(root: Path):
    assembly = assemble_workspace_core(root, instance_id="recheck")
    session = Session("audit-ui", EntryKind.HUMAN_UI)
    from aitest.application.project.serialization import project_to_payload

    try:

        def send(action, project, params, key, expected=0):
            return assembly.api.dispatch(
                Command(
                    request_id=key,
                    action=action,
                    project_id=project,
                    intent_id=key + "-intent",
                    expected_revision=expected,
                    parameters=params,
                ),
                session,
            )

        project = LocalProject("p", assembly.workspace.workspace_id, "p", "test", "1")
        other_payload = replace(project, local_project_id="payload-other")
        mismatch = send(
            "save_context",
            "envelope-other",
            {"project": project_to_payload(other_payload)},
            "mismatch",
        )
        saved = send("save_context", "p", {"project": project_to_payload(project)}, "saved")
        assert mismatch.error is None and saved.error is None
        unsafe = Delivery(
            "delivery-unsafe", "unregistered", "v1", "run", verified_in_scope=("required",)
        )
        try:
            save_delivery(unsafe, project_id="p", unit_of_work=assembly_dependency(assembly))
            unsafe_denied = False
        except ValueError:
            unsafe_denied = True
        plain = Delivery("delivery-plain", "unregistered", "v1", "run")
        from aitest.application.project.serialization import delivery_to_payload

        plain_response = send(
            "save_delivery", "p", {"delivery": delivery_to_payload(plain, project_id="p")}, "plain"
        )
        reader = assembly_reader(assembly)
        try:
            load_delivery(reader, project_id="foreign", delivery_id="delivery-plain", revision=1)
            foreign_denied = False
        except ValueError:
            foreign_denied = True
        published = []
        for index, text in enumerate(("original", "new edit", "stale edit"), 1):
            response = send(
                "publish_rules",
                "p",
                {
                    "draft": {
                        "rule_id": "rule",
                        "revision": index,
                        "scope": "module",
                        "text": text,
                        "source": "human",
                        "confirmed": True,
                    }
                },
                f"publish-{index}",
                expected=0,
            )
            published.append(
                {
                    "error": response.error.code if response.error else None,
                    "published": bool(response.result.get("published"))
                    if response.result
                    else False,
                }
            )
        assert unsafe_denied and foreign_denied and published[0]["published"]
        assert not published[1]["published"] and not published[2]["published"]
        return {
            "B-12": "partial: envelope mismatch still accepted; foreign delivery rejected",
            "project_envelope_mismatch_accepted": mismatch.error is None,
            "B-13": "self-declared verified scope rejected; real projection not wired",
            "B-14": published,
            "plain_unregistered_task_accepted": plain_response.error is None,
            "default_handlers": sorted(assembly.api.handlers),
            "boundary": "real default API and file store; synthetic human session",
        }
    finally:
        assembly.lifetime_lock.release()


def assembly_dependency(assembly):
    from aitest.application.planning.substrate_adapter import PortsUnitOfWork

    return PortsUnitOfWork(
        assembly.unit_of_work, repository=assembly.unit_of_work.repo, sequence=assembly.unit_of_work
    )


def assembly_reader(assembly):
    from aitest.application.planning.substrate_adapter import PortsRecordReader

    return PortsRecordReader(assembly.unit_of_work.repo)


def project_rules(root: Path):
    original = LocalProjectBinding(
        "b", 1, "p", "C:/old", BindingForm.PLAIN, manifest_digest="sha256:old", confirmed=True
    )
    moved = move_binding(original, canonical_path="C:/new", drive_kind=DriveKind.FIXED)
    supplied = move_binding(
        original, canonical_path="C:/new", drive_kind=DriveKind.FIXED, manifest_digest="sha256:new"
    )
    assert supplied.binding is not None and not supplied.binding.confirmed
    assert supplied.binding.manifest_digest != original.manifest_digest
    module = Module("m", "p", "module", "pure function", "f(x)")
    project = LocalProject("p", "w", "p", "test", "1", modules=(module,))
    env = EnvironmentRef("env", 1, "Python 3.13", "stdlib")
    declared = register_graph(project_id="p", modules=(module,), dependencies=())
    missing = ModuleDependencyGraph("p", modules=(module,))
    gaps = detect_context_gaps(project=project, graph=declared, environment=env)
    assert not gaps
    unknown_gaps = detect_context_gaps(project=project, graph=missing, environment=env)
    return {
        "B-16": "old confirmation/digest not inherited",
        "missing_new_digest_has_binding": moved.binding is not None,
        "B-17": "explicit no-dependency declaration accepted",
        "unknown_dependency_gaps": [gap.kind for gap in unknown_gaps],
    }


def runtime_identity(root: Path):
    original = replace(
        _case(), links=replace(_case().links, acceptance_item_ids=frozenset({"AC-A", "AC-B"}))
    )
    proposed = replace(
        original,
        revision=2,
        links=replace(original.links, acceptance_item_ids=frozenset({"AC-A", "AC-C"})),
    )
    substitution = _decide(_failure_in_progress(), frozen_case=original, change=_change(proposed))
    original_wrong = _decide(_failure_in_progress(), plan=replace(_plan(), plan_id="unrelated"))
    matched_request_wrong_facts = _decide(
        _failure_in_progress(),
        plan=replace(_plan(), plan_id="unrelated"),
        base_plan_revision_id="unrelated",
    )
    assert not substitution.accepted and not original_wrong.accepted
    assert matched_request_wrong_facts.accepted
    return {
        "B-15": "partial",
        "substitution_rejected": True,
        "original_wrong_plan_rejected": True,
        "request_and_plan_unrelated_to_facts_accepted": True,
        "facts_plan": "plan-1@1",
        "request_and_plan": "unrelated@1",
        "boundary": "real runtime application gate, derived running fixture",
    }


def command(root: Path):
    root.mkdir(parents=True)
    spool = FileSpoolStore(root)
    checkpoints = FileCheckpointStore(root)
    adapter = CommandAdapter(spool_store=spool, stream_block_size=1)
    adapter.register(CommandRegistration("entry-1", sys.executable, root))
    request = _request()
    request = replace(
        request,
        registered_entry=replace(
            request.registered_entry,
            entrypoint=sys.executable,
            arguments=(
                "-c",
                'import time; print("EARLY",flush=True); time.sleep(2.5); print("LATE",flush=True)',
            ),
        ),
        timeout_ms=10000,
    )
    observations = {}

    class ObservedRunner(SerialRunner):
        def inspect_attempt(self, attempt):
            nonlocal observations
            manifest = spool.read_manifest(attempt.attempt_id)
            if manifest.blocks:
                observations["early_blocks_before_exit"] = True
            return super().inspect_attempt(attempt)

    started = time.monotonic()
    final = ObservedRunner(adapter, spool, checkpoint_store=checkpoints).execute_attempt(
        _attempt(), request
    )
    elapsed = time.monotonic() - started
    assert final.state is AttemptState.COMPLETED and observations.get("early_blocks_before_exit")
    again = adapter.collect(final.execution_handle_ref)
    assert not again.complete

    class Lost:
        def inspect(self, handle):
            return ExecutionInspectionResult(
                handle_id=handle.handle_id,
                state=ExecutionInspectionState.LOST,
                process_reachable=False,
                identity_matches=False,
            )

    recovered = SerialRunner(Lost(), spool, checkpoint_store=checkpoints).recover_pending()
    assert recovered[0].attempt.state is AttemptState.COMPLETED
    return {
        "C-12": "default original early-poll cutoff fixed",
        "elapsed_seconds": round(elapsed, 2),
        "C-13": "early bytes persisted before exit",
        "C-10": "completed preserved after LOST",
        "C-14_new": "second collect complete=False after first complete=True",
        "second_capture_completeness": again.capture_completeness.value,
        "job_closed": adapter._runtimes[final.execution_handle_ref.handle_id].job_handle is None,
    }


def live_recovery(root: Path):
    spool = FileSpoolStore(root)
    writer = spool.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
        block_size=1024,
    )
    writer.append(b"live-unsealed")
    attempt = replace(recovery_attempt(), execution_handle_ref=_handle())
    checkpoint = RecoveryCheckpoint(
        run_id=attempt.run_id,
        step_id=attempt.step_id,
        attempt_id=attempt.attempt_id,
        last_committed_stage="running",
        output_cursors=(),
        output_block_refs=(),
        resolved_input_digest=attempt.resolved_input_digest,
        side_effect_class=attempt.side_effect_class,
        execution_handle_ref=_handle(),
    )
    result = recover_attempt(
        checkpoint,
        attempt,
        spool,
        inspection=ExecutionInspectionResult(
            handle_id=_handle().handle_id,
            state=ExecutionInspectionState.RUNNING,
            process_reachable=True,
            identity_matches=True,
        ),
    )
    before = spool.read_manifest("attempt-1")
    writer.close()
    assert result.action.value == "reattach" and not before.blocks
    return {"C-11": "fixed", "writer_close_succeeded": True, "salvaged_live_tail": False}


def real_commit(root: Path):
    spool = FileSpoolStore(root)
    unit = FileUnitOfWork(root)
    fake = FakeExecutionPort()
    runner = SerialRunner(fake, spool, commit_coordinator=ExecutionCommitCoordinator(unit))
    error = None
    try:
        runner.execute_attempt(_attempt(), _request(), max_polls=1)
    except ValueError as caught:
        error = str(caught)
    assert error and "revision conflict" in error and len(fake.started) == 1
    assert unit.repo.current_revision("execution_checkpoint", "attempt-1") == 1
    return {
        "C-05": "real coordinator path still broken",
        "error": error,
        "external_start_calls": len(fake.started),
        "stored_checkpoint_revision": 1,
        "stored_stage": unit.repo.read(
            aggregate_kind="execution_checkpoint", record_id="attempt-1", revision=1
        ).payload["checkpoint"]["last_committed_stage"],
        "boundary": "real FileUnitOfWork, fake external execution, no side effects",
    }


def missing_owner(root: Path):
    unit = FileUnitOfWork(root)
    for index, project in enumerate(("p-a", "p-b")):
        unit.begin(request_id=f"req-{index}", project_id=project)
        unit.stage_record(
            aggregate_kind="execution_checkpoint",
            record_id="same",
            expected_revision=index,
            payload={"attempt_id": "same"},
        )
        unit.commit()
    assert unit.repo.current_revision("execution_checkpoint", "same") == 2
    return {
        "A-11": "partial",
        "missing_payload_owner_cross_project_revision_accepted": True,
        "boundary": "real UOW, owner-less payload as used by C checkpoint codec",
    }


def handle_closure(root: Path):
    FileUnitOfWork(root)
    handles = FileExecutionHandleStore(root)
    handles.save(PersistedExecutionHandle("attempt-1", "startup", _handle()))
    integrity = check_workspace(root)
    backup = FileBackupStore(root).create(root.parent / "handle-backup")
    manifest = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
    text = json.dumps(manifest)
    assert not integrity["ok"] and "execution-handles" not in text
    return {
        "A-06": "partial: new execution-handles excluded",
        "integrity_errors": integrity["errors"],
        "execution_handles_in_backup": False,
        "boundary": "real handle store/checker/backup",
    }


def old_empty(root: Path):
    spec = importlib.util.spec_from_file_location(
        "old_extended", Path("docs/validation/p1-e2e-audit-20261003/extended_probes.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.empty_full()
    assert result["business_outcome"] == "passed"
    return {"D-09": "unchanged, original counterexample still reproduced", **result}


def model_replay(root: Path):
    from tests.support.memory_model import MemoryModelCaller
    from tests.unit.test_model_orchestration import _request as model_request
    from tests.unit.test_model_orchestration import _world

    unit, reader = _world()
    caller = MemoryModelCaller()
    first = model_request(
        unit_of_work=unit, reader=reader, caller=caller, generation_request_id="same-generation"
    )
    same = model_request(
        unit_of_work=unit, reader=reader, caller=caller, generation_request_id="same-generation"
    )
    assert same.is_reused and caller.call_count == 1

    class MissingDraft:
        def query(self, query):
            return reader.query(query)

        def read(self, *, aggregate_kind, record_id, revision):
            if aggregate_kind == "generated_content":
                raise ValueError("fault injection: saved draft unreadable")
            return reader.read(
                aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
            )

    repeated = model_request(
        unit_of_work=unit,
        reader=MissingDraft(),
        caller=caller,
        generation_request_id="same-generation",
    )
    assert repeated.is_draft_ready and caller.call_count == 2
    return {
        "B-10": "partial",
        "normal_retransmission_external_calls": 1,
        "after_unreadable_saved_draft_external_calls": caller.call_count,
        "original_success_outbound_was_preserved": first.is_draft_ready,
        "boundary": "real orchestration; memory model/store and read fault; no network",
    }


def migration_interruption(root: Path):
    root.mkdir(parents=True)
    before = {"workspace_id": "existing", "writer_epoch": 1}
    (root / "workspace.json").write_text(json.dumps(before), encoding="utf-8")
    (root / "records.json").write_text(json.dumps({"records": {}, "commit": 0}), encoding="utf-8")
    manager = FileMigrationManager(root)
    plan = manager.plan(("0001-workspace-schema-version",))
    with patch.object(
        manager, "_save_registry", side_effect=OSError("injected registry save failure")
    ):
        from contextlib import suppress

        with suppress(OSError):
            manager.apply(plan.plan_id)
    assert json.loads((root / "workspace.json").read_text())["schema_version"] == "1.0"
    manager.resume(plan.plan_id)
    manager.rollback(plan.plan_id)
    after = json.loads((root / "workspace.json").read_text())
    assert after != before and after["schema_version"] == "1.0"
    return {
        "A-16": "partial: forward write before registry not recoverable as rollback",
        "field_absent_before": True,
        "field_remains_after_resume_rollback": True,
        "boundary": "real manager, registry-save fault injection; no physical power loss",
    }


def maintenance_live(root: Path):
    from aitest.infrastructure.file_store.maintenance import detect_activity_blocker

    spool = FileSpoolStore(root)
    writer = spool.open_stream(
        run_id="run",
        step_id="step",
        attempt_id="attempt",
        stream_name=OutputStreamName.STDOUT,
        block_size=1,
    )
    try:
        writer.append(b"first\n")
        manifest_exists = (root / "spool" / "attempt" / "manifest.json").exists()
        blocker = detect_activity_blocker(root)
        assert manifest_exists and blocker is None
        return {
            "A-07": "partial: active writer with sealed prefix not detected",
            "writer_still_open": True,
            "manifest_exists": manifest_exists,
            "blocker": blocker,
        }
    finally:
        writer.close()


def finite_resume(root: Path):
    root.mkdir(parents=True)
    spool = FileSpoolStore(root)
    adapter = CommandAdapter(spool_store=spool, stream_block_size=1)
    adapter.register(CommandRegistration("entry-1", sys.executable, root))
    request = replace(
        _request(),
        registered_entry=replace(
            _request().registered_entry,
            entrypoint=sys.executable,
            arguments=("-c", 'import time;time.sleep(0.4);print("done")'),
        ),
        timeout_ms=5000,
    )
    runner = SerialRunner(adapter, spool)
    pending = runner.execute_attempt(_attempt(), request, max_polls=1)
    runtime = adapter._runtimes[pending.execution_handle_ref.handle_id]
    runtime.process.wait(timeout=5)
    same = runner.execute_attempt(_attempt(), request, max_polls=1)
    assert pending.state is AttemptState.PENDING_VERIFICATION and same.state is pending.state
    real = adapter.collect(pending.execution_handle_ref)
    assert real.complete
    return {
        "C-12": "partial: finite slice never resumes collection for the same intent",
        "before": pending.state.value,
        "after_process_exit_same_intent": same.state.value,
        "manual_collect_complete": real.complete,
    }


def query_boundaries(root: Path):
    from aitest.contracts.queries import QuerySpec
    from aitest.infrastructure.file_store.index import FileQueryIndex, _ShardDirectory

    index = FileQueryIndex(root / "pages", shard_size=8)
    rows = [
        {
            "project_id": "p",
            "aggregate_kind": "case",
            "record_id": f"c-{n:03}",
            "revision": 1,
            "commit_sequence": n + 1,
        }
        for n in range(80)
    ]
    index.rebuild(rows)
    base = QuerySpec(
        project_id="p", aggregate_kind="case", sort="commit_sequence", limit=5, descending=False
    )
    cursor, read_counts, count = None, [], 0
    original = _ShardDirectory._read_shard

    def observed_read(directory, info):
        nonlocal count
        count += 1
        return original(directory, info)

    with patch.object(_ShardDirectory, "_read_shard", observed_read):
        while True:
            count = 0
            page = index.query_spec(base.model_copy(update={"cursor": cursor}))
            assert page.status == "ok"
            read_counts.append(count)
            cursor = page.next_cursor
            if not cursor:
                break
    assert read_counts[-1] > 2
    reports = FileQueryIndex(root / "reports", shard_size=8)
    original_rows = [
        {
            "project_id": "p",
            "aggregate_kind": "report",
            "record_id": "report-b",
            "report_id": "report-b",
            "revision": 1,
            "commit_sequence": 1,
            "business_outcome": "passed",
        },
        {
            "project_id": "p",
            "aggregate_kind": "report",
            "record_id": "report-a",
            "report_id": "report-a",
            "revision": 1,
            "commit_sequence": 2,
            "business_outcome": "passed",
        },
    ]
    reports.rebuild(original_rows)
    spec = QuerySpec(project_id="p", aggregate_kind="report", limit=1, descending=True)
    first = reports.query_spec(spec)
    assert first.next_cursor
    reports.publish([{**original_rows[0], "revision": 2, "commit_sequence": 3}], commit_sequence=3)
    second = reports.query_spec(spec.model_copy(update={"cursor": first.next_cursor}))
    assert second.status == "ok" and not second.items and second.commit_id == 2
    return {
        "A-05": "partial",
        "shard_reads_by_page": read_counts,
        "old_report_page_expected": "report-b@1",
        "old_report_page_actual": [],
        "old_page_status": second.status,
        "old_page_commit": second.commit_id,
        "boundary": "real FileQueryIndex; 80 synthetic rows and two saved report summaries",
    }


def source_and_import(root: Path):
    from aitest.application.execution.source_checks import (
        SourceCheckRequest,
        SourceProbeObservation,
        SourceVerificationService,
    )
    from aitest.domain.execution.runs import FailureClass
    from aitest.domain.execution.sources import SourceCheckType
    from aitest.infrastructure.adapters.execution.external_result import (
        ExternalResultAdapter,
        ExternalResultPayload,
    )

    request = SourceCheckRequest(
        verification_id="v",
        check_result_id="c",
        project_id="p",
        attempt_id="a",
        plan_revision_ref=_request().authorization_ref.plan_revision_ref,
        expected_source_binding_digest="sha256:expected",
        materialized_snapshot_ref=str(root),
        check_type=SourceCheckType.LOAD,
        scope="module",
        source_snapshot_ref="snapshot",
        environment_ref="env",
        rules_revision="r",
        adapter_version="1",
        observed_source_digest="sha256:expected",
    )
    service = SourceVerificationService()
    absent = service.check(
        request, SourceProbeObservation(observed_source_digest="sha256:expected")
    )
    mismatch = service.check(
        request,
        SourceProbeObservation(
            observed_source_digest="sha256:other", failure_class=FailureClass.PASSED
        ),
    )
    adapter = ExternalResultAdapter()
    payload = ExternalResultPayload(
        import_id="import-1",
        external_schema="schema",
        source_instance_id="source",
        source_record_id="row",
        content={"raw": "same"},
        assertion_values={"state": "failed"},
    )
    first = adapter.validate(
        payload,
        expected_schema="schema",
        expected_assertions={"state": "passed"},
        verification_id="v1",
    )
    second = adapter.validate(
        replace(payload, assertion_values={"state": "passed"}),
        expected_schema="schema",
        expected_assertions={"state": "passed"},
        verification_id="v2",
    )
    assert absent.verification.state.value == "verified"
    assert (
        mismatch.verification.state.value == "mismatch"
        and mismatch.check_result.failure_class is FailureClass.PASSED
    )
    assert second.import_ref.idempotency_state == "duplicate"
    assert first.verification.observation != second.verification.observation
    return {
        "C-04": "partial",
        "no_entry_import_evidence_state": absent.verification.state.value,
        "mismatched_source_verification": mismatch.verification.state.value,
        "mismatched_source_check": mismatch.check_result.failure_class.value,
        "C-03": "partial: same external identity accepts changed assertion values",
        "first_import_observation": first.verification.observation.value,
        "second_import_observation": second.verification.observation.value,
        "second_import_idempotency": second.import_ref.idempotency_state,
        "boundary": "real services, synthetic facts; no actual external import or host",
    }


def markdown_and_legacy_graph(root: Path):
    from aitest.application.planning.portable import rule_draft_to_payload
    from aitest.application.planning.rules_markdown import (
        rule_markdown_from_payload,
        rule_markdown_to_payload,
    )
    from aitest.application.project.serialization import (
        dependency_graph_from_payload,
        dependency_graph_to_payload,
    )
    from tests.unit.test_rules_markdown import _draft

    draft = _draft(text="ordinary body\n## detail inside body\nmore body")
    rendered = rule_markdown_from_payload(rule_draft_to_payload(draft))
    try:
        rule_markdown_to_payload(rendered)
    except ValueError as error:
        markdown_error = str(error)
    else:
        raise AssertionError("expected body heading round-trip failure")
    module = Module("m", "p", "module", "pure function", "f(x)")
    unknown_graph = ModuleDependencyGraph(project_id="p", modules=(module,))
    payload = dependency_graph_to_payload(unknown_graph)
    payload.pop("edges_declared")
    read_back = dependency_graph_from_payload(payload)
    assert not unknown_graph.edges_declared and read_back.edges_declared
    from aitest.infrastructure.security import KnownSecretRegistry, guard_bytes

    registry = KnownSecretRegistry()
    synthetic = "1234"
    registry.register(synthetic)
    safe, changed = guard_bytes(b"observed " + synthetic.encode() + b" bytes", registry)
    assert synthetic.encode() in safe and not changed
    return {
        "B-04": "new Markdown body heading fails export/import round-trip",
        "markdown_error": markdown_error,
        "B-17": "partial: missing legacy declaration field becomes True",
        "unknown_before_serialization": not unknown_graph.edges_declared,
        "legacy_read_edges_declared": read_back.edges_declared,
        "A-09": "registered synthetic four-character credential is ignored",
        "short_known_value_retained": True,
        "boundary": "pure codecs and in-memory synthetic credential; no real secret or network",
    }


def main():
    observations = {}
    with tempfile.TemporaryDirectory(prefix="aitest-fix-recheck-") as directory:
        base = Path(directory)
        for probe in (
            snapshot,
            migration,
            events,
            sid,
            project_and_publish,
            project_rules,
            runtime_identity,
            command,
            live_recovery,
            real_commit,
            missing_owner,
            handle_closure,
            old_empty,
            model_replay,
            migration_interruption,
            maintenance_live,
            finite_resume,
            query_boundaries,
            source_and_import,
            markdown_and_legacy_graph,
        ):
            try:
                observations[probe.__name__] = probe(base / probe.__name__)
            except Exception as error:
                observations[probe.__name__] = {
                    "probe_error": str(error),
                    "traceback": traceback.format_exc(),
                }
    target = Path(__file__).with_name("observations.json")
    target.write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(observations, ensure_ascii=False, indent=2))
    if any("probe_error" in result for result in observations.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
