from pathlib import Path

from aitest.application.execution.source_checks import (
    SourceCheckRequest,
    SourceProbeObservation,
    SourceVerificationService,
)
from aitest.domain.execution.runs import FailureClass, PlanRevisionRef
from aitest.domain.execution.sources import (
    SourceCheckType,
    SourceVerificationState,
)


def _request(materialized_root: Path) -> SourceCheckRequest:
    return SourceCheckRequest(
        verification_id="verify-1",
        check_result_id="check-1",
        project_id="project-1",
        attempt_id="attempt-1",
        plan_revision_ref=PlanRevisionRef(
            revision_id="plan-1",
            revision_no=1,
            digest="sha256:plan-1",
        ),
        expected_source_binding_digest="sha256:source-1",
        materialized_snapshot_ref=str(materialized_root),
        check_type=SourceCheckType.LOAD,
        scope="module",
        source_snapshot_ref="snapshot-1",
        environment_ref="environment-1",
        rules_revision="rules-1",
        adapter_version="python-checks/1.0",
        observed_source_digest="sha256:source-1",
    )


def test_source_verification_accepts_only_bytes_inside_materialized_root(
    tmp_path: Path,
) -> None:
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    entry = materialized / "module.py"
    entry.write_text("value = 1\n", encoding="utf-8")
    service = SourceVerificationService()

    verification = service.verify(
        _request(materialized),
        SourceProbeObservation(
            observed_source_digest="sha256:source-1",
            observed_entry_ref=str(entry),
            observed_import_ref=str(entry),
        ),
    )

    assert verification.state is SourceVerificationState.VERIFIED
    assert verification.gap_ids == ()


def test_source_verification_rejects_original_repository_load(
    tmp_path: Path,
) -> None:
    materialized = tmp_path / "materialized"
    original = tmp_path / "original"
    materialized.mkdir()
    original.mkdir()
    original_entry = original / "module.py"
    original_entry.write_text("value = 1\n", encoding="utf-8")

    verification = SourceVerificationService().verify(
        _request(materialized),
        SourceProbeObservation(
            observed_source_digest="sha256:source-1",
            observed_entry_ref=str(original_entry),
            observed_import_ref=str(original_entry),
        ),
    )

    assert verification.state is SourceVerificationState.MISMATCH
    assert "entry_outside_materialized_source" in verification.gap_ids
    assert "import_outside_materialized_source" in verification.gap_ids


def test_source_check_preserves_environment_blocked_fact(tmp_path: Path) -> None:
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    outcome = SourceVerificationService().check(
        _request(materialized),
        SourceProbeObservation(
            observed_source_digest="sha256:source-1",
            failure_class=FailureClass.ENVIRONMENT_UNREACHABLE,
        ),
    )

    assert outcome.verification.state is SourceVerificationState.BLOCKED
    assert outcome.check_result.failure_class is FailureClass.ENVIRONMENT_UNREACHABLE
