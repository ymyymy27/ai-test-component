"""Reproduce audit findings with synthetic data in disposable directories.

Run from the repository root with:
    uv run python -m docs.validation.p1-audit-20261001.probes
No real credentials, model calls, editor operations, or business executions.
"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from aitest.application.execution.recovery import invalidate_downstream_attempts
from aitest.application.planning.prepare_run import preparation_payload, prepare_run
from aitest.bootstrap import CoreBootstrap
from aitest.contracts.commands import Command
from aitest.contracts.queries import QuerySpec
from aitest.domain.execution.runs import AttemptState, ConsumedOutput, PlanRevisionRef
from aitest.domain.planning.model_outbound import MaterialKind
from aitest.domain.planning.plans import RunTier
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
    DecisionFacts,
    ReviewGap,
    ReviewGapCode,
    SourceIdentityState,
    evaluate_review,
)
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore, SnapshotError
from aitest.infrastructure.file_store import atomic
from aitest.infrastructure.file_store.backup import FileBackupStore
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.index import FileQueryIndex
from aitest.infrastructure.file_store.migrations import FileMigrationManager
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.projections import SafeMaterialProjector
from aitest.interfaces.local.api import EntryKind, Session
from tests.support.memory_substrate import FixedClock, MemoryReader, MemoryStore, MemoryUnitOfWork
from tests.unit.test_prepare_run_flow import _build_case, _build_plan, _inputs_from
from tests.unit.test_serial_runner import _attempt


def run() -> dict[str, object]:
    results: dict[str, object] = {}
    with TemporaryDirectory(prefix="aitest-audit-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        (source / "one.py").write_text("original", encoding="utf-8")
        (source / "two.py").write_text("other", encoding="utf-8")
        snapshots = FileSourceSnapshotStore(root / "snap-workspace")
        snapshot = snapshots.pin(canonical_path=str(source), purpose="prepare")
        snapshot_id = str(snapshot["snapshot_id"])
        (source / "one.py").write_text("changed", encoding="utf-8")
        try:
            snapshots.materialize(snapshot_id, str(root / "materialized"))
        except SnapshotError:
            results["A-SOURCE-01"] = {"old_bytes_available": False, "changed_copy_written": True}
        selected = snapshots.pin(
            canonical_path=str(source), purpose="analysis", selected_paths=("one.py",)
        )
        results["A-SOURCE-02"] = snapshots.detect_changes(str(selected["snapshot_id"]))
        renamed = snapshots.pin(canonical_path=str(source), purpose="prepare")
        snapshots.pin(canonical_path=str(source), purpose="analysis")
        results["A-SOURCE-03"] = {
            "stored_purpose_after_repin": snapshots.read_pinned(str(renamed["snapshot_id"]))[
                "purpose"
            ]
        }
        escape_record = snapshots.read_pinned(str(renamed["snapshot_id"]))
        escape_record["files"] = [
            {
                "relative_path": "../snapshot-outside.txt",
                "size": 4,
                "sha256": sha256(b"FAKE").hexdigest(),
            }
        ]
        (root / "snapshot-outside.txt").write_bytes(b"FAKE")
        atomic.write_json(
            root / "snap-workspace/snapshots" / f"{renamed['snapshot_id']}.json", escape_record
        )
        target_parent = root / "snapshot-target"
        target_parent.mkdir()
        result = snapshots.materialize(str(renamed["snapshot_id"]), str(target_parent / "inner"))
        results["A-PATH-01"] = {
            "outside_destination_written": (target_parent / "snapshot-outside.txt").exists(),
            "verified": result["verified"],
        }

        uow_root = root / "uow"
        uow = FileUnitOfWork(uow_root)
        for revision in (0, 1):
            uow.begin(f"request-{revision}", "project", intent_id="same-intent")
            uow.stage_record(
                aggregate_kind="case",
                record_id="case",
                expected_revision=revision,
                payload={"project_id": "project", "value": "same"},
            )
            uow.commit()
        results["A-INTENT-01"] = {
            "revision_after_same_intent": uow.repo.current_revision("case", "case")
        }
        journal = FileEventJournal(uow_root, instance_id="audit")
        failed_uow = FileUnitOfWork(uow_root, journal=journal)
        failed_uow.begin("failed-request", "project", intent_id="failed-intent")
        failed_uow.stage_record(
            aggregate_kind="case",
            record_id="phantom",
            expected_revision=0,
            payload={"project_id": "project"},
        )
        original_write = atomic.write_json

        def fail_records(
            path: Path, value: dict[str, Any], **kwargs: Any
        ) -> None:
            if path.name == "records.json":
                raise OSError("synthetic publication failure")
            original_write(path, value, **kwargs)

        with patch.object(atomic, "write_json", side_effect=fail_records), suppress(OSError):
            failed_uow.commit()
        failed_uow.rollback()
        results["A-COMMIT-01"] = {
            "record_revision": failed_uow.repo.current_revision("case", "phantom"),
            "visible_index_rows": len(
                FileQueryIndex(uow_root)
                .query_spec(QuerySpec(project_id="project", record_id="phantom"))
                .items
            ),
            "visible_events": len(journal.read().events),
            "active_marker_exists": (uow_root / "transactions/active.json").exists(),
            "recovery_state": RecoveryOrchestrator(uow_root, instance_id="audit").run().state,
        }

        index = FileQueryIndex(root / "index")
        rows = [
            {
                "project_id": "p",
                "aggregate_kind": "case",
                "record_id": str(i),
                "revision": 1,
                "commit_sequence": i,
            }
            for i in (1, 2)
        ]
        index.rebuild(rows)
        spec = QuerySpec(project_id="p", sort="commit_sequence", descending=True, limit=1)
        page = index.query_spec(spec)
        index.rebuild(rows + [{**rows[0], "record_id": "3", "commit_sequence": 3}])
        next_page = index.query_spec(spec.model_copy(update={"cursor": page.next_cursor}))
        results["A-QUERY-01"] = {
            "first_id": page.items[0]["record_id"],
            "next_id": next_page.items[0]["record_id"],
        }
        atomic.write_json(index.path, {"version": 1, "rows": ["corrupt-row"]})
        results["A-QUERY-02"] = {"corrupt_index_status": index.query_spec(spec).status}

        backup_source = root / "backup-source"
        backup_source.mkdir()
        for name in ("snapshots", "diagnostics", "exports", "migrations"):
            (backup_source / name).mkdir()
            (backup_source / name / "permanent.txt").write_text("history", encoding="utf-8")
        backup_store = FileBackupStore(backup_source)
        backup = backup_store.create(root / "backup")
        results["A-BACKUP-01"] = {
            "verified": backup_store.verify(backup)["ok"],
            "omitted_directories": [
                name
                for name in ("snapshots", "diagnostics", "exports", "migrations")
                if not (backup / name).exists()
            ],
        }
        (root / "backup-outside.txt").write_bytes(b"FAKE")
        atomic.write_json(
            backup / "backup.json",
            {"files": {"../backup-outside.txt": sha256(b"FAKE").hexdigest()}},
        )
        backup_target_parent = root / "restore-target"
        backup_target_parent.mkdir()
        restored = backup_store.restore(backup=backup, target=backup_target_parent / "inner")
        results["A-PATH-02"] = {
            "outside_target_written": (backup_target_parent / "backup-outside.txt").exists(),
            "verified": restored.verified,
        }
        migration_root = root / "migration"
        atomic.write_json(migration_root / "workspace.json", {"workspace_id": "audit"})
        atomic.write_json(migration_root / "transactions/active.json", {"state": "unknown"})
        migrations = FileMigrationManager(migration_root)
        plan = migrations.plan(["0001-workspace-schema-version"])
        results["A-MIGRATION-01"] = {
            "state_with_unresolved_active_marker": migrations.apply(plan.plan_id).state
        }

        projector = SafeMaterialProjector().project(
            material={MaterialKind.PROJECT_CONTEXT: '{"api_key": "AUDIT_FAKE_SECRET"}'},
            source_snippets_enabled=False,
        )
        results["A-SECRET-01"] = {
            "projection_status": projector.status.value,
            "synthetic_secret_retained": "AUDIT_FAKE_SECRET"
            in projector.projected[0].projected_text,
        }
        bootstrap = CoreBootstrap()
        bootstrap.register_use_cases("D", {"query": lambda command: {"token": "AUDIT_FAKE_SECRET"}})
        api = bootstrap.create(root / "api").api
        response = api.dispatch(
            Command(request_id="audit-api", action="query"), Session("audit", EntryKind.HUMAN_UI)
        )
        assert response.result is not None
        results["A-SECRET-02"] = {
            "synthetic_secret_in_response": response.result["token"] == "AUDIT_FAKE_SECRET"
        }

        case = _build_case()
        inputs = _inputs_from(_build_plan(case), case)
        varied = replace(
            inputs,
            selected_case_ids=(),
            execution_source=inputs.execution_source.model_copy(
                update={"resolved_input_digest": "sha256:changed"}
            ),
        )
        results["B-PREPARE-01"] = {
            "changed_scope_and_input_have_same_payload": preparation_payload(inputs)
            == preparation_payload(varied)
        }
        store = MemoryStore()
        prepare_run(
            inputs,
            unit_of_work=MemoryUnitOfWork(store),
            reader=MemoryReader(store),
            clock=FixedClock(),
        )
        changed_source = replace(
            inputs,
            snapshot=inputs.snapshot.model_copy(
                update={"content_identity": "sha256:changed-bytes"}
            ),
            input_revisions=replace(inputs.input_revisions, snapshot_revision=2),
        )
        try:
            prepare_run(
                changed_source,
                unit_of_work=MemoryUnitOfWork(store),
                reader=MemoryReader(store),
                clock=FixedClock(),
            )
        except Exception as error:
            results["B-PREPARE-02"] = {"actual_exception": type(error).__name__}

        upstream = ConsumedOutput(
            upstream_attempt_id="upstream-old",
            value_ref="old-output",
            output_object_digest="sha256:old",
        )
        downstream = replace(_attempt(), state=AttemptState.COMPLETED, consumed_outputs=(upstream,))
        results["C-INVALIDATION-01"] = {
            "completed_downstream_invalidated": bool(
                invalidate_downstream_attempts(
                    (downstream,),
                    previous_plan_revision=PlanRevisionRef("plan", 1, "sha256:one"),
                    current_plan_revision=PlanRevisionRef("plan", 2, "sha256:two"),
                    affected_upstream_attempt_ids=("upstream-old",),
                )
            )
        }

        ids = frozenset({"case"})
        coverage = Coverage(ids, ids, ids, frozenset(), ids, ids)
        facts = DecisionFacts(
            tier=RunTier.FULL,
            coverage=coverage,
            assertion_basis_confirmed=ids,
            source_identity_state=SourceIdentityState.MATCHED,
            critical_paths_satisfied=True,
            required_evidence_valid=True,
            environment_evidence_complete=True,
            noncritical_gaps=(ReviewGap(ReviewGapCode.NONCRITICAL_UNKNOWN, "synthetic local gap"),),
        )
        decision = evaluate_review(facts)
        assert decision.evidence_grade is not None
        results["D-DECISION-01"] = {
            "outcome_with_all_required_passed": decision.business_outcome.value,
            "grade": decision.evidence_grade.value,
        }
        try:
            Coverage(ids, ids, frozenset(), ids, frozenset(), frozenset())
        except ValueError:
            results["D-COVERAGE-01"] = {"unverified_reuse_rejected": True}
        issue = IssueRecord(
            "issue",
            "project",
            1,
            "test",
            IssueStatus.CONFIRMED,
            IssueSeverity.P1,
            owner="owner",
            confirmed_by="human",
        )
        canonical = replace(issue, issue_id="canonical", severity=IssueSeverity.P2)
        duplicate = mark_duplicate(
            issue,
            canonical_issue=canonical,
            reason="same issue",
            evidence_refs=("evidence",),
            confirmed_by="human",
        )
        cyclic = mark_duplicate(
            canonical,
            canonical_issue=duplicate,
            reason="same issue",
            evidence_refs=("evidence",),
            confirmed_by="human",
        )
        results["D-ISSUE-01"] = {
            "cycle_returned_by_mutation": cyclic.canonical_issue_id == duplicate.issue_id
        }
        reopened = undo_disposition(duplicate)
        results["D-ISSUE-02"] = {
            "reopened_severity": reopened.severity,
            "blocking_ids": sorted(effective_blocking_issue_ids({"issue": reopened})),
        }
    return results


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
