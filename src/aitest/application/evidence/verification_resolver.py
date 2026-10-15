"""Resolve explicit structured expectations from the exact saved step basis."""

from collections.abc import Callable
from dataclasses import asdict

from aitest.application.evidence.evidence_review import freeze_verification_request
from aitest.application.evidence.query_materials import BusinessVerificationBlocked
from aitest.application.execution.authorization import ExecutionAuthorizationService
from aitest.application.execution.step_content import StepContentReader
from aitest.application.ports import RecordRepository, VerificationRequest
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.evidence.polling import query_polling_policy
from aitest.domain.json_material import decode_json

BASIS_VERSION = "aitest.business-query-basis/1"
_FIELDS = {
    "schema_version", "business_object_id", "query_method", "target_deployment_ref",
    "expected_facts", "query_interval", "deadline_condition",
}


def declared_request(text: str, *, project_id: str, attempt_id: str) -> VerificationRequest:
    if len(text.encode("utf-8")) > 128 * 1024:
        raise ValueError("independent query basis exceeds its budget")
    data = decode_json(text)
    if (
        not isinstance(data, dict) or set(data) != _FIELDS
        or data["schema_version"] != BASIS_VERSION
        or not isinstance(data["expected_facts"], dict) or not data["expected_facts"]
        or any(not isinstance(data[key], str) or not data[key].strip()
               for key in _FIELDS - {"expected_facts"})
        or not data["query_method"].startswith(project_id + ":")
        or data["query_method"].count(":") != 1
        or data["query_method"] == project_id + ":"
    ):
        raise ValueError("exact case basis lacks a structured independent query declaration")
    query_polling_policy(data["query_interval"], data["deadline_condition"])
    return freeze_verification_request(VerificationRequest(
        verification_of=attempt_id,
        **{key: value for key, value in data.items() if key != "schema_version"},
    ))


class SavedBusinessVerificationResolver:
    def __init__(
        self, authorizations: ExecutionAuthorizationService, records: RecordRepository,
        validate_target: Callable[[VerificationRequest], None] | None = None,
    ):
        self.authorizations = authorizations
        self.contents = StepContentReader(records)
        self.validate_target = validate_target

    def resolve(
        self, *, facts: ExecutionFacts, step_id: str, attempt_id: str
    ) -> VerificationRequest:
        if facts.current_attempt_by_step.get(step_id) != attempt_id:
            raise BusinessVerificationBlocked("independent query requires the current attempt")
        run, step, _, _ = self.authorizations._context(facts.project_id, facts.run_id, step_id)
        fact = next((item for item in facts.steps if item.step_id == step_id), None)
        if fact is None or fact.step_revision_ref.model_dump() != asdict(step.step_revision_ref):
            raise BusinessVerificationBlocked("independent query step basis changed")
        case = self.contents.read(run=run, step=step).checked_case()
        try:
            request = declared_request(
                case.assertion_basis.text, project_id=facts.project_id, attempt_id=attempt_id
            )
            if self.validate_target is not None:
                self.validate_target(request)
            return request
        except (ValueError, TypeError) as error:
            raise BusinessVerificationBlocked(
                "exact case basis or trusted independent query registration is invalid"
            ) from error
