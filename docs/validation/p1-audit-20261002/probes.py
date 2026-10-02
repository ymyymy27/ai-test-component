"""Repeat focused phase-one observations with synthetic, isolated inputs.

Run from the repository root: uv run --no-sync python -m
docs.validation.p1-audit-20261002.probes
No real model, business target, editor, or power-loss acceptance is performed.
The script prints JSON and does not overwrite historical evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from aitest.application.execution.recovery import invalidate_downstream_attempts
from aitest.application.planning.model_ports import ModelCallResult, ModelCallStatus
from aitest.application.planning.prepare_run import preparation_payload, prepare_run
from aitest.application.planning.substrate import RecordQuery
from aitest.bootstrap import CoreAssemblyBlocked, assemble_workspace_core
from aitest.contracts.queries import QuerySpec
from aitest.domain.execution.runs import AttemptState, ConsumedOutput, PlanRevisionRef
from aitest.domain.review.defects import (
    IssueRecord,
    IssueSeverity,
    IssueStatus,
    effective_blocking_issue_ids,
    mark_duplicate,
    undo_disposition,
)
from aitest.domain.review.reports import (
    Coverage,
    ReviewGap,
    ReviewGapCode,
    evaluate_review,
)
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
from aitest.infrastructure.file_store import atomic
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.index import FileQueryIndex
from aitest.infrastructure.file_store.integrity import check_workspace
from aitest.infrastructure.file_store.maintenance import FileMaintenanceService
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, Session
from tests.contracts.test_draft_and_rule_publication_entrypoint import (
    _command,
    _draft_parameters,
)
from tests.support.memory_substrate import FixedClock, MemoryReader, MemoryStore, MemoryUnitOfWork
from tests.unit.test_model_orchestration import _request
from tests.unit.test_prepare_run_flow import _build_case, _build_plan, _inputs_from
from tests.unit.test_review_decision import _complete_facts
from tests.unit.test_serial_runner import _attempt


def run() -> dict[str, object]:
    observations: dict[str, object] = {}
    with TemporaryDirectory(prefix="aitest-recheck-20261002-") as directory:
        root = Path(directory)
        workspace = root / "post-publication"
        journal = FileEventJournal(workspace, instance_id="synthetic")
        uow = FileUnitOfWork(workspace, journal=journal)
        uow.begin("synthetic-request", "p", intent_id="synthetic-intent")
        uow.stage_record(
            aggregate_kind="case",
            record_id="case",
            expected_revision=0,
            payload={"project_id": "p"},
        )
        real_write = atomic.write_json

        def fail_ledger(path: Path, value: dict[str, object], **kwargs: object) -> None:
            if path.name == "commit.json":
                raise OSError("synthetic post-publication fault")
            real_write(path, value, **kwargs)

        try:
            with patch.object(atomic, "write_json", side_effect=fail_ledger):
                uow.commit()
        except OSError:
            pass
        uow.rollback()
        recovered = RecoveryOrchestrator(workspace, instance_id="synthetic").run()
        observations["A-COMMIT-01-post-publication"] = {
            "record_revision": uow.repo.current_revision("case", "case"),
            "index_status": FileQueryIndex(workspace).query_spec(QuerySpec(project_id="p")).status,
            "visible_events": len(journal.read().events),
            "committed_sequences": recovered.committed_sequences,
            "second_recovery_state": RecoveryOrchestrator(workspace, instance_id="synthetic")
            .run()
            .state,
        }

        index = FileQueryIndex(root / "query")
        index.rebuild(
            [
                {
                    "project_id": "p",
                    "aggregate_kind": "case",
                    "record_id": str(i),
                    "revision": 1,
                    "commit_sequence": i,
                }
                for i in (1, 2)
            ]
        )
        first = index.query_spec(QuerySpec(project_id="p", limit=1))
        foreign = index.query_spec(QuerySpec(project_id="q", limit=1, cursor=first.next_cursor))
        observations["A-QUERY-01-query-binding"] = {"foreign_project_status": foreign.status}

        first_core = assemble_workspace_core(root / "two-cores", instance_id="core-one")
        second_core = assemble_workspace_core(root / "two-cores", instance_id="core-two")
        observations["A-CORE-02-lifetime-admission"] = {
            "two_instances_assembled_for_same_workspace": first_core.api.instance_id
            != second_core.api.instance_id,
            "same_workspace": first_core.api.workspace_id == second_core.api.workspace_id,
            "default_actions": sorted(first_core.api.handlers),
            "boundary": "in-process assembly only; no real host lifecycle",
        }

        draft_root = root / "draft-restart"
        draft_core = assemble_workspace_core(draft_root, instance_id="draft-core")
        draft_response = draft_core.api.dispatch(
            _command("generate_draft", "synthetic-draft", _draft_parameters()),
            Session(session_id="synthetic-human", entry_kind=EntryKind.HUMAN_UI),
        )
        integrity = check_workspace(draft_root)
        restart_blocked = False
        try:
            assemble_workspace_core(draft_root, instance_id="restarted-core")
        except CoreAssemblyBlocked:
            restart_blocked = True
        observations["A-INTEGRITY-03-inline-digest"] = {
            "draft_response_has_error": draft_response.error is not None,
            "integrity_ok_after_generated_draft": integrity["ok"],
            "integrity_errors": integrity["errors"],
            "core_restart_blocked": restart_blocked,
            "boundary": "default LocalAPI, synthetic template input, real temporary file store",
        }

        source = root / "source"
        source.mkdir()
        (source / "main.py").write_text("print('synthetic')\n", encoding="utf-8")
        snapshot_root = root / "snapshot-workspace"
        snapshots = FileSourceSnapshotStore(snapshot_root)
        pinned = snapshots.pin(canonical_path=str(source), purpose="synthetic")
        digest = str(pinned["files"][0]["sha256"])
        (snapshot_root / "snapshots" / "blobs" / digest).unlink()
        observations["A-BACKUP-02-snapshot-closure"] = {
            "missing_blob_integrity_ok": check_workspace(snapshot_root)["ok"],
            "missing_blob_materialization_rejected": False,
        }
        materialized = snapshots.materialize(str(pinned["snapshot_id"]), str(root / "out"))
        observations["A-BACKUP-02-snapshot-closure"]["missing_blob_materialization_rejected"] = (
            materialized["state"] == "rejected"
        )

        maintenance_root = root / "maintenance"
        maintenance_root.mkdir()
        target = maintenance_root / "records.json"
        target.write_text("{}", encoding="utf-8")
        candidate = maintenance_root / ".records.json.abcdefgh"
        content = b"synthetic permanently referenced bytes"
        candidate.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        os.utime(candidate, ns=(1, 1))
        diagnostics = maintenance_root / "diagnostics"
        diagnostics.mkdir()
        (diagnostics / "permanent.jsonl").write_text(
            json.dumps({"attachment": "sha256:" + digest}) + "\n",
            encoding="utf-8",
        )
        service = FileMaintenanceService(maintenance_root)
        result = service.reclaim(relative_paths=(candidate.name,), dry_run=False)
        observations["A-MAINTENANCE-02-jsonl-reference"] = {
            "permanently_referenced_candidate_removed": candidate.name in result.removed,
            "candidate_exists_after_reclaim": candidate.exists(),
        }

    case = _build_case()
    inputs = _inputs_from(_build_plan(case), case)
    store = MemoryStore()
    prepare_run(
        inputs, unit_of_work=MemoryUnitOfWork(store), reader=MemoryReader(store), clock=FixedClock()
    )
    changed = replace(
        inputs,
        execution_source=inputs.execution_source.model_copy(
            update={"resolved_input_digest": "sha256:synthetic-change"},
        ),
    )
    prepared = prepare_run(
        changed,
        unit_of_work=MemoryUnitOfWork(store),
        reader=MemoryReader(store),
        clock=FixedClock(),
    )
    observations["B-PREPARE-01-resolved-input-only"] = {
        "same_request_payload": preparation_payload(inputs) == preparation_payload(changed),
        "status": prepared.status.value,
        "blocking_codes": [reason.code for reason in prepared.blocking_reasons],
        "changed_inputs": [rule.source_kind for rule in prepared.invalidation_rules],
    }

    class SyntheticModel:
        def call(self, request: object) -> ModelCallResult:
            return ModelCallResult(
                status=ModelCallStatus.OK, draft_text="password=synthetic-audit-secret"
            )

    store = MemoryStore()
    reader = MemoryReader(store)
    _request(unit_of_work=MemoryUnitOfWork(store), reader=reader, caller=SyntheticModel())
    records = reader.query(
        RecordQuery(
            project_id="project-ticket",
            aggregate_kind="generated_content",
        )
    )
    observations["B-MODEL-03-response-before-save"] = {
        "synthetic_secret_saved_in_draft": any(
            "synthetic-audit-secret" in str(record.payload.get("draft_text", ""))
            for record in records.items
        ),
        "boundary": "synthetic model and memory store; no real credential",
    }

    upstream = ConsumedOutput(
        upstream_attempt_id="upstream-old", value_ref="old", output_object_digest="sha256:old"
    )
    downstream = replace(_attempt(), state=AttemptState.COMPLETED, consumed_outputs=(upstream,))
    observations["C-INVALIDATION-01"] = {
        "completed_downstream_invalidated": bool(
            invalidate_downstream_attempts(
                (downstream,),
                previous_plan_revision=PlanRevisionRef("plan", 1, "sha256:one"),
                current_plan_revision=PlanRevisionRef("plan", 2, "sha256:two"),
                affected_upstream_attempt_ids=("upstream-old",),
            )
        )
    }
    from importlib import import_module

    observations["C-DURABILITY-01"] = import_module(
        "docs.validation.p1-audit-20261001.additional_probes",
    ).run()["C-DURABILITY-01"]

    facts = replace(
        _complete_facts(),
        noncritical_gaps=(ReviewGap(ReviewGapCode.NONCRITICAL_UNKNOWN, "synthetic gap"),),
    )
    decision = evaluate_review(facts)
    observations["D-DECISION-01"] = {
        "outcome": decision.business_outcome.value,
        "grade": decision.evidence_grade.value,
    }
    ids = frozenset({"case"})
    Coverage(ids, ids, frozenset(), ids, frozenset(), frozenset())
    observations["D-COVERAGE-01"] = {"reuse_without_verification_accepted": True}
    issue = IssueRecord(
        "issue",
        "project",
        1,
        "synthetic",
        IssueStatus.CONFIRMED,
        IssueSeverity.P1,
        owner="owner",
        confirmed_by="human",
    )
    canonical = replace(issue, issue_id="canonical", severity=IssueSeverity.P2)
    duplicate = mark_duplicate(
        issue,
        canonical_issue=canonical,
        reason="synthetic",
        evidence_refs=("evidence",),
        confirmed_by="human",
    )
    rejected = False
    try:
        mark_duplicate(
            canonical,
            canonical_issue=duplicate,
            reason="synthetic",
            evidence_refs=("evidence",),
            confirmed_by="human",
            issues={"issue": duplicate, "canonical": canonical},
        )
    except ValueError:
        rejected = True
    observations["D-ISSUE-01"] = {"cycle_rejected_before_return": rejected}
    reopened = undo_disposition(duplicate)
    observations["D-ISSUE-02"] = {
        "reopened_severity": reopened.severity.value,
        "blocking_ids": sorted(effective_blocking_issue_ids({"issue": reopened})),
    }
    return observations


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
