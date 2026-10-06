"""One fingerprint for the original executable request and its Attempt basis."""

import hashlib
import json

from pydantic import TypeAdapter

from aitest.domain.execution.runs import Attempt, ExecutionRequest


def execution_start_fingerprint(attempt: Attempt, request: ExecutionRequest) -> str:
    request_payload = TypeAdapter(ExecutionRequest).dump_python(request, mode="json")
    request_payload["authorization_ref"].pop("consumed_by_attempt_id", None)
    attempt_payload = TypeAdapter(Attempt).dump_python(attempt, mode="json")
    payload = {
        "fingerprint_version": "aitest.execution-start/2.0",
        "request": request_payload,
        "attempt_basis": {
            name: attempt_payload[name]
            for name in (
                "attempt_index",
                "step_revision_ref",
                "consumed_outputs",
                "consumed_conditions",
                "adapter_version",
                "business_idempotency_key_ref",
                "expected_plan_revision_ref",
            )
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
