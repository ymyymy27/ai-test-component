"""Additional isolated audit observations; no network, no product files modified."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from aitest.application.execution.runner import SerialRunner
from aitest.domain.planning.plans import RunTier
from aitest.domain.review.reports import (
    Coverage,
    DecisionFacts,
    SourceIdentityState,
    evaluate_review,
)
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.migrations import FileMigrationManager
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.pipe import NamedPipeServer, check_peer_identity, current_user_sid
from tests.unit.test_runtime_revision import _case, _change, _decide, _failure_in_progress, _plan
from tests.unit.test_serial_runner import _attempt, _request


def empty_full() -> dict[str, object]:
    empty = frozenset()
    facts = DecisionFacts(
        tier=RunTier.FULL,
        coverage=Coverage(empty, empty, empty, empty, empty, empty),
        source_identity_state=SourceIdentityState.MATCHED,
        critical_paths_satisfied=True,
        required_evidence_valid=True,
        environment_evidence_complete=True,
    )
    result = evaluate_review(facts)
    assert result.business_outcome.value == "passed" and result.evidence_grade.value == "D"
    return {
        "selected": 0,
        "executed": 0,
        "verified": 0,
        "business_outcome": result.business_outcome.value,
        "evidence_grade": result.evidence_grade.value,
        "gap": result.primary_gap.code.value,
    }


def runtime_scope() -> dict[str, object]:
    case = _case()
    original = replace(
        case, links=replace(case.links, acceptance_item_ids=frozenset({"AC-A", "AC-B"}))
    )
    proposed = replace(
        original,
        revision=2,
        links=replace(original.links, acceptance_item_ids=frozenset({"AC-A", "AC-C"})),
    )
    decision = _decide(_failure_in_progress(), frozen_case=original, change=_change(proposed))
    wrong_plan = _decide(_failure_in_progress(), plan=replace(_plan(), plan_id="unrelated-plan"))
    assert decision.accepted and wrong_plan.accepted
    return {
        "required_case_removed_acceptance": "AC-B",
        "added_acceptance": "AC-C",
        "scope_substitution_accepted": decision.accepted,
        "unrelated_plan_same_revision_accepted": wrong_plan.accepted,
        "boundary": "real application gate, derived running ExecutionFacts fixture; no live run",
    }


def snapshot_scope(root: Path) -> dict[str, object]:
    source = root / "source"
    source.mkdir(parents=True)
    (source / "a.py").write_text("x=1\n", encoding="utf-8")
    store = FileSourceSnapshotStore(root / "workspace")
    selected = store.pin(canonical_path=str(source), purpose="selected", selected_paths=("a.py",))
    whole = store.pin(canonical_path=str(source), purpose="whole")
    (source / "b.py").write_text("x=2\n", encoding="utf-8")
    drift = store.detect_changes(whole["snapshot_id"])
    assert selected["snapshot_id"] == whole["snapshot_id"] and drift["state"] == "unchanged"
    return {
        "same_snapshot_id": True,
        "whole_request_recorded_selection": whole["selected_paths"],
        "whole_request_recorded_purpose": whole["purpose"],
        "added_file": "b.py",
        "drift_state": drift["state"],
    }


def migration_rollback(root: Path) -> dict[str, object]:
    root.mkdir(parents=True)
    workspace = {"workspace_id": "preexisting", "schema_version": "1.0", "writer_epoch": 1}
    records = {"records": {}, "commit": 0, "intents": {}}
    (root / "workspace.json").write_text(json.dumps(workspace), encoding="utf-8")
    (root / "records.json").write_text(json.dumps(records), encoding="utf-8")
    manager = FileMigrationManager(root)
    plan = manager.plan(("0001-workspace-schema-version", "0002-records-intents-container"))
    manager.apply(plan.plan_id)
    manager.rollback(plan.plan_id)
    after_workspace = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
    after_records = json.loads((root / "records.json").read_text(encoding="utf-8"))
    assert "schema_version" not in after_workspace and "intents" not in after_records
    return {
        "preexisting_schema_version_removed": True,
        "preexisting_intents_container_removed": True,
        "backup_created": True,
        "boundary": "real built-in forward/backward migration, temporary files",
    }


def event_identity(root: Path) -> dict[str, object]:
    journal = FileEventJournal(root, instance_id="event-core")
    unit = FileUnitOfWork(root, journal=journal)
    unit.begin(request_id="request", project_id="p", intent_id="intent")
    for kind in ("rule_draft", "rule_version"):
        unit.stage_record(
            aggregate_kind=kind,
            record_id="same-id",
            expected_revision=0,
            payload={"project_id": "p", "kind": kind},
        )
    commit = unit.commit()
    events = journal.read().events
    assert len(commit["created"]) == 2 and len(events) == 1
    return {
        "records_created": len(commit["created"]),
        "events_created": len(events),
        "same_id_and_revision_across_distinct_aggregates": True,
        "boundary": "real FileUnitOfWork + FileEventJournal",
    }


def peer_identity() -> dict[str, object]:
    if os.name != "nt":
        return {"state": "not_run", "reason": "Windows only"}
    server = NamedPipeServer("audit-workspace", instance_id="audit-instance")
    expected = current_user_sid()
    observed = server._process_user_sid(os.getpid())
    assert expected and observed == ""
    check_peer_identity(
        client_session_id=1,
        expected_session_id=1,
        client_user_sid="",
        expected_user_sid="",
    )
    return {
        "current_user_sid_verified": bool(expected),
        "server_sid_lookup_returned_empty": observed == "",
        "two_empty_sids_accepted": True,
        "boundary": "real Win32 lookup for own process; no foreign-user connection attempted",
    }


def command_poll_and_capture(root: Path) -> dict[str, object]:
    root.mkdir(parents=True)
    store = FileSpoolStore(root)
    adapter = CommandAdapter(spool_store=store, stream_block_size=1)
    adapter.register(CommandRegistration("entry-1", sys.executable, root))
    request = _request()
    request = replace(
        request,
        registered_entry=replace(
            request.registered_entry,
            entrypoint=sys.executable,
            arguments=(
                "-c",
                'import time; print("EARLY", flush=True); '
                'time.sleep(2.5); print("LATE", flush=True)',
            ),
        ),
        timeout_ms=10000,
    )
    started = time.monotonic()
    pending = SerialRunner(adapter, store).execute_attempt(_attempt(), request)
    elapsed = time.monotonic() - started
    during = store.read_manifest("attempt-1")
    inspection = adapter.inspect(pending.execution_handle_ref)
    adapter._runtimes[pending.execution_handle_ref.handle_id].process.wait(timeout=5)
    collection = adapter.collect(pending.execution_handle_ref)
    assert pending.state.value == "pending_verification" and inspection.state.value == "running"
    assert not during.blocks and collection.complete
    return {
        "declared_timeout_ms": 10000,
        "runner_return_seconds": round(elapsed, 3),
        "runner_state": pending.state.value,
        "reason": pending.unknown_reason_ref,
        "process_still_running": True,
        "early_flushed_output_saved_before_exit": bool(during.blocks),
        "after_exit_complete": collection.complete,
        "boundary": "real Windows Python subprocess; controlled read-only command, no network",
    }


def main() -> None:
    observations = {
        "empty_full": empty_full(),
        "runtime_scope": runtime_scope(),
        "peer_identity": peer_identity(),
    }
    with tempfile.TemporaryDirectory(prefix="aitest-extended-audit-") as directory:
        root = Path(directory)
        for name, probe in [
            ("snapshot_scope", snapshot_scope),
            ("migration_rollback", migration_rollback),
            ("event_identity", event_identity),
            ("command_poll_and_capture", command_poll_and_capture),
        ]:
            observations[name] = probe(root / name)
    target = Path(__file__).with_name("extended-observations.json")
    target.write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(observations, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
