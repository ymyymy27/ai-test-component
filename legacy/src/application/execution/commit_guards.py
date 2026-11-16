"""提交前置校验与 ID/摘要助手（纯函数）。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, cast

from pydantic import TypeAdapter

from aitest.application.execution.facts import (
    execution_payload_digest,
    validate_execution_facts,
    validate_frozen_run_basis,
    validate_frozen_step_basis,
)
from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork
from aitest.contracts.execution_facts import (
    ExecutionFacts,
    SourceCheckFact,
)
from aitest.domain.evidence.evidence import (
    EvidenceRef,
)
from aitest.domain.execution.runs import (
    AuthorizationRef,
    RecoveryRecord,
)
from aitest.domain.execution.sources import (
    SourceCheckResult,
)

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


def _validate_original_grant_proof(proof: object, authorization: AuthorizationRef) -> None:
    """Reject unreadable consumer receipts before publishing an external start."""
    if not isinstance(proof, Mapping) or set(proof) != {
        "grant_id",
        "grant_revision",
        "grant_digest",
        "state_revision",
    }:
        raise ValueError("original authorization proof has missing or unknown fields")
    digest = proof["grant_digest"]
    if (
        proof["grant_id"] != authorization.authorization_id
        or type(proof["grant_revision"]) is not int
        or proof["grant_revision"] != 1
        or type(proof["state_revision"]) is not int
        or proof["state_revision"] != 2
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or len(digest) != 71
        or any(char not in "0123456789abcdef" for char in digest[7:])
    ):
        raise ValueError("original authorization proof has invalid identity, revision or digest")


def _require_record_envelope(record: object, kind: str, identity: str, revision: int) -> None:
    if type(getattr(record, "revision", None)) is not int or (
        getattr(record, "aggregate_kind", None),
        getattr(record, "record_id", None),
        getattr(record, "revision", None),
    ) != (kind, identity, revision):
        raise ValueError("saved execution record envelope cannot be verified")


def _json_payload(adapter: TypeAdapter[Any], value: Any) -> dict[str, object]:
    payload = adapter.dump_python(value, mode="json")
    if not isinstance(payload, dict):
        raise TypeError("serialized C artifact must be a JSON object")
    return cast(dict[str, object], payload)


def _intent_record_id(project_id: str, intent_id: str) -> str:
    identity = json.dumps([project_id, intent_id], ensure_ascii=False, separators=(",", ":"))
    return "execution-intent:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _authorization_record_id(authorization_id: str) -> str:
    # Authorization identities are workspace-wide; a foreign project cannot rebind them.
    return "execution-authorization:" + hashlib.sha256(authorization_id.encode("utf-8")).hexdigest()


def _run_pointer_id(project_id: str, run_id: str) -> str:
    identity = json.dumps([project_id, run_id], ensure_ascii=False, separators=(",", ":"))
    return "execution-facts-current:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _payload_digest(payload: Mapping[str, object]) -> str:
    return execution_payload_digest(payload)


def _admission_source_check_fact(result: SourceCheckResult, attempt_id: str) -> SourceCheckFact:
    return SourceCheckFact(
        check_result_id=f"admission-{result.check_type.value}:{attempt_id}",
        attempt_id=attempt_id,
        check_type=result.check_type.value,
        scope=result.scope,
        source_snapshot_ref=result.source_snapshot_ref,
        environment_ref=result.environment_ref,
        rules_revision=result.rules_revision,
        adapter_version=result.adapter_version,
        failure_class=result.failure_class.value,
        raw_output_evidence_ref=result.raw_output_evidence_ref,
        evidence_refs=result.evidence_refs,
    )


def _validate_current_facts(facts: ExecutionFacts) -> None:
    validate_execution_facts(facts)


def _validate_frozen_step_basis(previous: ExecutionFacts, facts: ExecutionFacts) -> None:
    validate_frozen_step_basis(previous, facts)


def _validate_frozen_run_basis(previous: ExecutionFacts, facts: ExecutionFacts) -> None:
    validate_frozen_run_basis(previous, facts)


def _validate_publication_current(previous: ExecutionFacts, facts: ExecutionFacts) -> None:
    if any(
        facts.current_attempt_by_step.get(step_id) != attempt_id
        or step_id not in facts.current_attempt_by_step
        for step_id, attempt_id in previous.current_attempt_by_step.items()
    ):
        raise ValueError(
            "publication cannot replace or remove a current attempt; claim a new attempt"
        )
