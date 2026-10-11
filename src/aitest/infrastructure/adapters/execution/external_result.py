"""External JSON recomputation; transient duplicate detection is not persistence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from aitest.domain.evidence.evidence import (
    ExternalImportRef,
    Verification,
    VerificationObservation,
)
from aitest.domain.execution.assertions import compare_expected_fields, freeze_json_value


@dataclass(frozen=True, slots=True)
class ExternalResultPayload:
    import_id: str
    external_schema: str
    source_instance_id: str
    source_record_id: str
    content: Mapping[str, object]
    assertion_values: Mapping[str, object]
    attachment_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "import_id",
            "external_schema",
            "source_instance_id",
            "source_record_id",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if any(not value.strip() for value in self.attachment_refs):
            raise ValueError("attachment refs must not be empty")


@dataclass(frozen=True, slots=True)
class ExternalValidationResult:
    import_ref: ExternalImportRef
    verification: Verification


class ExternalResultAdapter:
    """Compare actual direct JSON fields; applications own durable import identities."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def validate(
        self,
        payload: ExternalResultPayload,
        *,
        expected_schema: str,
        expected_assertions: Mapping[str, object],
        verification_id: str,
    ) -> ExternalValidationResult:
        if payload.external_schema != expected_schema:
            raise ValueError("external result schema does not match")
        try:
            expected = freeze_json_value(expected_assertions)
            content = freeze_json_value(payload.content)
            declared = freeze_json_value(payload.assertion_values)
        except (RecursionError, RuntimeError) as error:
            raise ValueError("external result JSON material cannot be frozen") from error
        if (
            not isinstance(expected, dict)
            or not isinstance(content, dict)
            or not isinstance(declared, dict)
        ):
            raise ValueError("external result comparison material requires JSON objects")
        digest = _content_digest(content)
        fingerprint = _content_digest(
            {
                "schema": payload.external_schema,
                "source_instance_id": payload.source_instance_id,
                "source_record_id": payload.source_record_id,
                "content": content,
                "assertion_values": declared,
                "attachment_refs": payload.attachment_refs,
                "expected_assertions": expected,
            }
        )
        previous = self._seen.get(payload.import_id)
        if previous is not None and previous != fingerprint:
            raise ValueError("external import id conflicts with different content")
        idempotency_state = "duplicate" if previous is not None else "new"

        gap_ids: tuple[str, ...]
        if not expected:
            observation = VerificationObservation.NO_RESULT
            gap_ids = ("expected_assertions_missing",)
        else:
            missing, mismatched = compare_expected_fields(content, expected)
            observation = (
                VerificationObservation.MATCHED
                if not missing and not mismatched
                else VerificationObservation.MISMATCHED
                if mismatched
                else VerificationObservation.NO_RESULT
            )
            gap_ids = tuple(f"assertion_missing:{key}" for key in missing) + tuple(
                f"assertion_mismatch:{key}" for key in mismatched
            )
        import_ref = ExternalImportRef(
            import_id=payload.import_id,
            external_schema=payload.external_schema,
            source_instance_id=payload.source_instance_id,
            source_record_id=payload.source_record_id,
            content_digest=digest,
            idempotency_state=idempotency_state,
            attachment_refs=payload.attachment_refs,
        )
        verification = Verification(
            verification_id=verification_id,
            verification_of=payload.external_schema,
            business_object_id=payload.source_record_id,
            query_method="external_recompute",
            observation=observation,
            actual_result_ref=digest,
            gap_ids=gap_ids,
        )
        result = ExternalValidationResult(
            import_ref=import_ref,
            verification=verification,
        )
        self._seen[payload.import_id] = fingerprint
        return result


def _content_digest(content: Mapping[str, object]) -> str:
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "ExternalResultAdapter",
    "ExternalResultPayload",
    "ExternalValidationResult",
]
