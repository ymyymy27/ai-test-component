from pathlib import Path

from aitest.application.execution.commit import (
    ExecutionCommitBatch,
    ExecutionCommitCoordinator,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.evidence.evidence import (
    CodeIdentity,
    EvidenceCaptureSource,
    EvidenceKind,
    EvidenceRef,
)
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    RecoveryCheckpoint,
    RecoveryRecord,
    SideEffectClass,
    StepRevisionRef,
)
from aitest.domain.execution.sources import SourceBindingKind


class _RecordingUnitOfWork:
    def __init__(self) -> None:
        self.staged: list[tuple[str, str, dict[str, object]]] = []
        self.commits = 0

    def stage_record(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: dict[str, object],
    ) -> str:
        self.staged.append((aggregate_kind, record_id, payload))
        return f"{aggregate_kind}:{record_id}:{expected_revision}"

    def commit(self) -> str:
        self.commits += 1
        return "commit-1"


def test_c_artifacts_are_staged_in_one_a_unit_of_work(tmp_path: Path) -> None:
    facts = ExecutionFacts.model_validate_json(
        (
            Path(__file__).parents[1]
            / "contracts"
            / "fixtures"
            / "execution_facts"
            / "success.json"
        ).read_text(encoding="utf-8")
    )
    attempt = Attempt(
        attempt_id="attempt-1",
        run_id="run-1",
        step_id="step-1",
        attempt_index=1,
        resolved_input_digest="sha256:input-1",
        step_revision_ref=StepRevisionRef(
            step_revision_id="step-revision-1",
            revision_no=1,
            digest="sha256:step-revision-1",
        ),
        source_binding_digest="sha256:source-1",
        side_effect_class=SideEffectClass.READ_ONLY,
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="1.0",
        state=AttemptState.COMPLETED,
    )
    checkpoint = RecoveryRecord(
        checkpoint=RecoveryCheckpoint(
            run_id="run-1",
            step_id="step-1",
            attempt_id="attempt-1",
            last_committed_stage="completed",
        ),
        attempt=attempt,
    )
    evidence = EvidenceRef(
        evidence_id="evidence-1",
        project_id="project-1",
        source_instance_id="run-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        evidence_kind=EvidenceKind.COMMAND_OUTPUT,
        capture_source=EvidenceCaptureSource.PLUGIN_RUNTIME,
        object_digest="sha256:evidence-1",
        object_size=1,
        code_identity=CodeIdentity(
            binding_kind=SourceBindingKind.GIT,
            workspace_ref="workspace-1",
            commit_id="abc123",
        ),
    )
    unit = _RecordingUnitOfWork()

    result = ExecutionCommitCoordinator(unit).stage_and_commit(
        ExecutionCommitBatch(
            checkpoint=checkpoint,
            evidence_refs=(evidence,),
            facts=facts,
        )
    )

    assert [item[0] for item in unit.staged] == [
        "execution_checkpoint",
        "evidence_ref",
        "execution_facts",
    ]
    assert unit.commits == 1
    assert result.committed == "commit-1"
