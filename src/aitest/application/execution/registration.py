"""Freeze an initial run from saved B authority before any C external effect."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import ExecutionFactsAssembler, ExecutionFactsAssembly
from aitest.application.execution.step_content import freeze_initial_step_contents
from aitest.application.planning.basis_validation import validate_prepared_material
from aitest.application.planning.substrate import RecordReader
from aitest.application.ports import RecordRepository, StageableWorkspaceUnitOfWork
from aitest.application.project.source_analysis import SourceAnalysisService
from aitest.contracts.execution_facts import (
    CoverageSummary,
    EvidenceGapFact,
    ExecutionFacts,
    FactCompleteness,
)
from aitest.contracts.prepared_run import PreparedRun, PreparedRunStatusFact
from aitest.domain.execution.runs import (
    InputRef,
    PlanRevisionRef,
    Run,
    RunTier,
    Step,
    StepLevel,
    StepRevisionRef,
)
from aitest.domain.project.context import IsolationMode


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


class InitialRunRegistration:
    """A registration is NOT_STARTED; it is not a claim that execution occurred."""

    def __init__(
        self,
        *,
        unit: StageableWorkspaceUnitOfWork,
        reader: RecordReader,
        records: RecordRepository,
        workspace_id: str,
        source_analysis: SourceAnalysisService,
    ) -> None:
        self.unit, self.reader = unit, reader
        self.workspace_id, self.source_analysis = workspace_id, source_analysis
        self.coordinator = ExecutionCommitCoordinator(unit, records=records)

    def register(
        self, *, project_id: str, prepared_run_id: str, request_id: str, intent_id: str
    ) -> ExecutionFacts:
        saved = self.reader.read(
            aggregate_kind="prepared_run", record_id=prepared_run_id, revision=1
        )
        prepared = PreparedRun.model_validate_json(json.dumps(dict(saved.payload)))
        if (prepared.project_id, prepared.workspace_id, prepared.prepared_run_id) != (
            project_id,
            self.workspace_id,
            prepared_run_id,
        ):
            raise ValueError("saved preparation project/workspace/identity cannot be verified")
        fingerprint = "sha256:" + _digest(dict(saved.payload))
        run_id = "run-" + _digest([project_id, intent_id])
        original = self.coordinator.recall_initial_run(
            run_id=run_id, project_id=project_id, fingerprint=fingerprint
        )
        if original is not None:
            return original
        self._validate(prepared)
        source = self.source_analysis.check(
            project_id=project_id,
            snapshot_id=prepared.snapshot.source_snapshot_id,
            revision=prepared.snapshot.record_revision,
        )
        changes = source.get("changes")
        if (
            not isinstance(changes, dict)
            or changes.get("state") != "unchanged"
            or source.get("binding_state") != "unchanged"
            or source.get("git_state") not in {"unchanged", "not_applicable"}
        ):
            raise ValueError("actual source is changed or unverified; prepare again")
        run, steps = _initial_domain(prepared, run_id, fingerprint)
        material = freeze_initial_step_contents(
            prepared=prepared, run=run, steps=steps, reader=self.reader
        )
        steps = tuple(item.step for item in material)
        facts = ExecutionFactsAssembler().assemble(
            ExecutionFactsAssembly(
                facts_id="initial-" + run_id,
                snapshot_commit_id="pending-initial-" + run_id,
                snapshot_cursor=0,
                snapshot_revision=1,
                committed_at=datetime.now(UTC),
                run=run,
                steps=steps,
                attempts=(),
                completeness=FactCompleteness.UNKNOWN,
                coverage=CoverageSummary(
                    mandatory_case_ids=tuple(sorted(run.required_scope)),
                    selected_case_ids=tuple(sorted(run.selected_scope)),
                    evidence_gap_count=1,
                    critical_gap_count=1,
                ),
                gaps=(
                    EvidenceGapFact(
                        gap_id="execution-source-unverified-" + run_id,
                        kind="source_verification",
                        subject_ref=run_id,
                        reason_code="execution_not_started",
                        safe_reason=(
                            "Actual interpreter, environment and business loading "
                            "have not been verified."
                        ),
                        critical=True,
                    ),
                ),
            )
        )
        self.unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            original = self.coordinator.recall_initial_run(
                run_id=run_id, project_id=project_id, fingerprint=fingerprint
            )
            if original is not None:
                self.unit.rollback(request_id)
                return original
            self._validate(prepared)
            if freeze_initial_step_contents(
                prepared=prepared, run=run, steps=steps, reader=self.reader
            ) != material:
                raise ValueError("frozen step content changed before initial publication")
            for item in material:
                self.unit.stage_record(
                    aggregate_kind="step_revision",
                    record_id=item.step.step_revision_ref.step_revision_id,
                    expected_revision=0,
                    payload=item.content.model_dump(mode="json"),
                )
            registered = self.coordinator.stage_initial_run(
                run=run,
                steps=steps,
                facts=facts,
                fingerprint=fingerprint,
                prepared_run_id=prepared_run_id,
                intent_id=intent_id,
            )
            self.unit.commit(request_id)
        except BaseException as error:
            try:
                self.unit.rollback(request_id)
            except Exception as cleanup_error:
                # A lost commit response may have already ended the transaction.
                # Preserve its original failure so a retry can recall the receipt.
                raise error from cleanup_error
            raise
        return registered

    def _validate(self, prepared: PreparedRun) -> None:
        if prepared.status is not PreparedRunStatusFact.PREPARED or prepared.blocking_reasons:
            raise ValueError("saved preparation is blocked or requires re-preparation")
        problems = validate_prepared_material(
            prepared, reader=self.reader, workspace_id=self.workspace_id
        )
        if problems:
            raise ValueError(
                "saved preparation basis cannot be verified: "
                + "; ".join(p.message for p in problems)
            )


def _initial_domain(
    prepared: PreparedRun, run_id: str, digest: str
) -> tuple[Run, tuple[Step, ...]]:
    run = Run(
        run_id=run_id,
        project_id=prepared.project_id,
        origin_workspace_id=prepared.workspace_id,
        intent_id=prepared.intent_id,
        tier=RunTier(prepared.run_tier.value),
        driver=prepared.initial_driver.value,
        conclusion_ceiling=prepared.conclusion_ceiling.value,
        plan_revision_ref=PlanRevisionRef(**prepared.plan_revision.model_dump()),
        environment_ref=f"{prepared.environment.environment_id}@{prepared.environment.revision}",
        environment_isolation_mode=IsolationMode(prepared.environment.isolation_mode.value),
        rules_revision="rules:"
        + _digest([r.model_dump(mode="json") for r in prepared.rule_versions]),
        required_scope=frozenset(prepared.frozen_required_case_ids),
        selected_scope=frozenset(prepared.selected_case_ids),
        frozen_input_refs=(
            InputRef(
                input_ref_id=prepared.prepared_run_id,
                value_ref=f"prepared_run:{prepared.prepared_run_id}@1",
                value_digest=digest,
                resolved=True,
            ),
        ),
        revision=1,
    )
    steps: list[Step] = []
    for case in prepared.frozen_cases:
        if case.case_id not in run.selected_scope:
            continue
        for item in case.steps:
            step_id = "step-" + _digest([run_id, case.case_id, item.step_id])
            steps.append(
                Step(
                    step_id=step_id,
                    run_id=run_id,
                    ordinal=len(steps) + 1,
                    case_id=case.case_id,
                    level=StepLevel(item.layer),
                    step_revision_ref=StepRevisionRef(
                        step_revision_id="step-revision-"
                        + _digest([run_id, prepared.prepared_run_id, case.case_id, item.step_id]),
                        revision_no=1,
                        digest="pending-step-content",
                    ),
                    input_refs=run.frozen_input_refs,
                )
            )
    if len({s.step_id for s in steps}) != len(steps):
        raise ValueError("frozen preparation contains duplicate case/step identities")
    return run, tuple(steps)
