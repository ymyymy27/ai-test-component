"""Read a saved JUnit artifact and turn it into a pytest assertion result."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from aitest.application.ports import (
    CapturedBusinessVerification,
    EvidenceObjectStore,
    VerificationRequest,
)
from aitest.contracts.execution_facts import (
    EvidenceIntegrityFact,
    EvidenceKindFact,
    ExecutionFacts,
)
from aitest.domain.evidence.evidence import (
    StoredObjectRef,
    Verification,
    VerificationObservation,
)
from aitest.domain.evidence.verification import compare_business_fields
from aitest.domain.execution.junit import parse_junit

PYTEST_BASIS_VERSION = "aitest.pytest-basis/1"
PYTEST_METHOD = "pytest-junit"


def is_pytest_request(request: VerificationRequest, project_id: str) -> bool:
    return request.query_method == f"{project_id}:{PYTEST_METHOD}"


def saved_artifact_bytes(
    facts: ExecutionFacts,
    attempt_id: str,
    objects: EvidenceObjectStore,
) -> tuple[bytes, tuple[str, ...]]:
    matches = [attempt for attempt in facts.attempts if attempt.attempt_id == attempt_id]
    if len(matches) != 1:
        raise ValueError("pytest requires exactly one saved attempt")
    attempt = matches[0]
    if (
        not attempt.is_current
        or attempt.capture_completeness.value != "complete"
        or attempt.exit_fact is None
        or attempt.exit_fact.capture_completeness.value != "complete"
        or facts.current_attempt_by_step.get(attempt.step_id) != attempt_id
    ):
        raise ValueError("pytest requires a complete current attempt")
    candidates = [
        evidence
        for evidence in facts.evidence_refs
        if evidence.attempt_id == attempt_id
        and evidence.evidence_kind is EvidenceKindFact.ARTIFACT
        and evidence.integrity is EvidenceIntegrityFact.COMPLETE
        and not evidence.gap_ids
        and (evidence.media_type or "").startswith("application/xml")
    ]
    if len(candidates) != 1:
        raise ValueError("pytest JUnit artifact is missing or ambiguous")
    evidence = candidates[0]
    content = objects.read_bytes(
        StoredObjectRef(
            evidence.project_id,
            evidence.object_digest,
            evidence.object_size,
            evidence.media_type or "application/xml",
            f"objects/{evidence.project_id}/{evidence.object_digest.removeprefix('sha256:')}",
        )
    )
    if (
        len(content) != evidence.object_size
        or "sha256:" + hashlib.sha256(content).hexdigest() != evidence.object_digest
    ):
        raise ValueError("pytest JUnit artifact bytes differ from its evidence")
    return content, (evidence.evidence_id,)


def capture_pytest_result(
    request: VerificationRequest,
    facts: ExecutionFacts,
    objects: EvidenceObjectStore,
) -> CapturedBusinessVerification:
    attempt_id = request.verification_of
    if (
        not is_pytest_request(request, facts.project_id)
        or request.business_object_id != attempt_id
        or request.target_deployment_ref != f"attempt:{attempt_id}"
        or request.query_interval != "configured"
        or request.deadline_condition != "immediate"
    ):
        raise ValueError("pytest verification scope differs")
    content, refs = saved_artifact_bytes(facts, attempt_id, objects)
    expected_count = request.expected_facts.get("expected_test_count")
    expected_outcome = request.expected_facts.get("expected_outcome", "all_pass")
    if expected_count is not None and type(expected_count) is not int:
        raise ValueError("pytest expected test count must be an integer")
    parsed = parse_junit(
        content,
        source_files=frozenset(),
        expected_test_count=expected_count,
        exit_code=next(
            (
                attempt.exit_fact.real_exit_code
                for attempt in facts.attempts
                if attempt.attempt_id == attempt_id and attempt.exit_fact is not None
            ),
            None,
        ),
    )
    gaps: list[str] = list(parsed.issues)
    if parsed.collection_errors:
        gaps.append("pytest_collection_error")
    if parsed.all_skipped:
        gaps.append("pytest_all_skipped")
    if parsed.exit_conflict:
        gaps.append("pytest_exit_junit_conflict")
    if not parsed.count_matches:
        gaps.append("test_count_incomplete")
    if gaps:
        observation = VerificationObservation.NO_RESULT
    elif parsed.failed or parsed.errors or parsed.collection_errors:
        observation = VerificationObservation.MISMATCHED
    elif expected_outcome == "all_pass" and parsed.total > 0 and parsed.passed == parsed.total:
        observation = VerificationObservation.MATCHED
    else:
        observation = VerificationObservation.NO_RESULT
    actual_fields: Mapping[str, object] | None = None
    if observation is not VerificationObservation.NO_RESULT:
        actual_fields = {
            "expected_test_count": expected_count if parsed.count_matches else parsed.total,
            "expected_outcome": (
                "all_pass" if observation is VerificationObservation.MATCHED else "failed"
            ),
            "total": parsed.total,
            "passed": parsed.passed,
            "failed": parsed.failed,
            "errors": parsed.errors,
            "skipped": parsed.skipped,
            "tests": [
                {
                    "node_id": item.node_id,
                    "outcome": item.outcome,
                    "category": item.category,
                    "failure_frame": (
                        {
                            "path": item.failure_frame.snapshot_path or item.failure_frame.path,
                            "line": item.failure_frame.line,
                            "function": item.failure_frame.function,
                        }
                        if item.failure_frame is not None
                        else None
                    ),
                    "suspect_source_frame": (
                        {
                            "path": item.suspect_source_frame.snapshot_path
                            or item.suspect_source_frame.path,
                            "line": item.suspect_source_frame.line,
                            "function": item.suspect_source_frame.function,
                        }
                        if item.suspect_source_frame is not None
                        else None
                    ),
                    "location_unknown": item.location_unknown,
                    "failure_type": item.failure_type,
                    "message": item.message,
                }
                for item in parsed.tests
            ],
        }
        # The material validator re-derives observation and gaps from the
        # actual fields; claim exactly what that comparison yields so a failed
        # run is recorded as an expected-outcome mismatch, not a conflict.
        observed, comparison_gaps = compare_business_fields(
            actual_fields, request.expected_facts
        )
        if observed is not observation:
            raise ValueError("pytest parsed result disagrees with its expected basis")
        gaps = list(comparison_gaps)
    return CapturedBusinessVerification(
        Verification(
            verification_id=f"verification:{attempt_id}:pytest",
            verification_of=attempt_id,
            business_object_id=attempt_id,
            query_method=request.query_method,
            observation=observation,
            query_interval=request.query_interval,
            deadline_condition=request.deadline_condition,
            target_deployment_ref=request.target_deployment_ref,
            evidence_refs=refs,
            gap_ids=tuple(dict.fromkeys(gaps)),
        ),
        actual_fields,
    )
