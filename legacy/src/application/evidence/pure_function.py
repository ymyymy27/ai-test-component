"""Compare saved function output against separately frozen case expectations."""

from aitest.application.evidence.dynamic_object import saved_stdout_json
from aitest.application.ports import (
    CapturedBusinessVerification,
    EvidenceObjectStore,
    VerificationRequest,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.evidence.evidence import Verification
from aitest.domain.evidence.verification import compare_business_fields

PURE_BASIS_VERSION = "aitest.pure-function-basis/1"
PURE_METHOD = "pure-function-stdout"


def is_pure_request(request: VerificationRequest, project_id: str) -> bool:
    return request.query_method == f"{project_id}:{PURE_METHOD}"


def capture_pure_output(
    request: VerificationRequest, facts: ExecutionFacts, step_id: str,
    objects: EvidenceObjectStore,
) -> CapturedBusinessVerification:
    attempt_id = request.verification_of
    if (not is_pure_request(request, facts.project_id)
            or request.business_object_id != attempt_id
            or request.target_deployment_ref != f"attempt:{attempt_id}"
            or request.query_interval != "configured" or request.deadline_condition != "immediate"):
        raise ValueError("pure function verification scope differs")
    actual, refs = saved_stdout_json(facts, step_id, attempt_id, objects)
    if not isinstance(actual, dict) or refs != request.evidence_refs:
        raise ValueError("pure function output must be an exact saved JSON object")
    observation, gaps = compare_business_fields(actual, request.expected_facts)
    return CapturedBusinessVerification(Verification(
        verification_id=f"verification:{attempt_id}:{attempt_id}",
        verification_of=attempt_id, business_object_id=attempt_id,
        query_method=request.query_method, observation=observation,
        query_interval=request.query_interval, deadline_condition=request.deadline_condition,
        target_deployment_ref=request.target_deployment_ref,
        evidence_refs=refs, gap_ids=gaps,
    ), actual)
