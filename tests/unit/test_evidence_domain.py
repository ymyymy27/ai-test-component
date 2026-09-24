from aitest.domain.evidence.evidence import (
    CodeIdentity,
    EvidenceCaptureSource,
    EvidenceIntegrity,
    EvidenceKind,
    EvidenceRef,
    ProjectionState,
    RedactionState,
    TruthClass,
)
from aitest.domain.execution.sources import SourceBindingKind


def test_evidence_provenance_is_separate_from_truth_and_projection() -> None:
    evidence = EvidenceRef(
        evidence_id="evidence-1",
        project_id="project-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        evidence_kind=EvidenceKind.COMMAND_OUTPUT,
        capture_source=EvidenceCaptureSource.PLUGIN_RUNTIME,
        object_digest="sha256:output",
        object_size=42,
        code_identity=CodeIdentity(
            binding_kind=SourceBindingKind.GIT,
            workspace_ref="ws-1",
            commit_id="abc123",
        ),
    )
    assert evidence.integrity is EvidenceIntegrity.UNKNOWN
    assert evidence.redaction_state is RedactionState.UNKNOWN
    assert evidence.projection_state is ProjectionState.INTERNAL
    assert TruthClass.REAL.value == "real"
