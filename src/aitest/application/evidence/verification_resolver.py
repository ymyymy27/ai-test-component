"""Resolve explicit structured expectations from the exact saved step basis."""

import json
from collections.abc import Callable
from dataclasses import asdict, replace

from aitest.application.evidence.dynamic_object import saved_object_id, saved_stdout_json
from aitest.application.evidence.evidence_review import freeze_verification_request
from aitest.application.evidence.pure_function import PURE_BASIS_VERSION, PURE_METHOD
from aitest.application.evidence.query_materials import BusinessVerificationBlocked
from aitest.application.execution.authorization import ExecutionAuthorizationService
from aitest.application.execution.step_content import StepContentReader
from aitest.application.ports import EvidenceObjectStore, RecordRepository, VerificationRequest
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
        objects: EvidenceObjectStore | None = None,
    ):
        self.authorizations = authorizations
        self.contents = StepContentReader(records)
        self.validate_target = validate_target
        self.objects = objects

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
            text = case.assertion_basis.text
            if len(text.encode("utf-8")) > 128 * 1024:
                raise ValueError("independent query basis exceeds budget")
            data = decode_json(text)
            evidence_refs: tuple[str, ...] = ()
            if isinstance(data, dict) and data.get("schema_version") == PURE_BASIS_VERSION:
                if (set(data) != {"schema_version", "expected_facts"}
                        or not isinstance(data["expected_facts"], dict)
                        or not data["expected_facts"] or self.objects is None):
                    raise ValueError("pure function basis requires independent expected fields")
                actual, evidence_refs = saved_stdout_json(
                    facts, step_id, attempt_id, self.objects,
                )
                if not isinstance(actual, dict):
                    raise ValueError("pure function output requires a JSON object")
                return freeze_verification_request(VerificationRequest(
                    verification_of=attempt_id, business_object_id=attempt_id,
                    query_method=f"{facts.project_id}:{PURE_METHOD}",
                    target_deployment_ref=f"attempt:{attempt_id}",
                    deadline_condition="immediate", evidence_refs=evidence_refs,
                    expected_facts=data["expected_facts"],
                ))
            if (isinstance(data, dict)
                    and data.get("schema_version") == "aitest.business-query-basis/2"):
                if set(data) != (_FIELDS - {"business_object_id"}) | {"business_object_source"}:
                    raise ValueError("dynamic query basis has unexpected fields")
                if self.objects is None:
                    raise ValueError("saved query identity reader unavailable")
                identity, evidence_refs = saved_object_id(
                    data.pop("business_object_source"), facts, step_id, attempt_id, self.objects,
                )
                data.update(schema_version=BASIS_VERSION, business_object_id=identity)
                text = json.dumps(data)
            request = declared_request(
                text, project_id=facts.project_id, attempt_id=attempt_id
            )
            request = replace(request, evidence_refs=evidence_refs)
            if request.query_method == f"{facts.project_id}:{PURE_METHOD}":
                raise ValueError("pure function method requires its dedicated basis")
            if self.validate_target is not None:
                self.validate_target(request)
            return request
        except (ValueError, TypeError, OSError, KeyError) as error:
            # 具体原因必须回指：否则「缺少执行输出证据」「依据字段不合法」
            # 「纯函数方法与其专用依据冲突」等会压成同一句话，调用方无从修复。
            raise BusinessVerificationBlocked(
                "exact case basis or trusted independent query registration is invalid: "
                f"{error}"
            ) from error
