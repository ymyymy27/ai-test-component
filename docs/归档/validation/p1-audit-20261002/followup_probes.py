"""Recheck model request identity and Attempt invalidation with synthetic inputs.

Run: uv run --no-sync python -m docs.validation.p1-audit-20261002.followup_probes
No real provider, host, business system, or user workspace is accessed.
"""

from __future__ import annotations

import json
from dataclasses import replace

from aitest.application.execution.recovery import invalidate_downstream_attempts
from aitest.application.planning.model_ports import ModelCall, ModelCallResult, ModelCallStatus
from aitest.application.planning.substrate import RecordQuery
from aitest.domain.execution.runs import AttemptState, ConsumedCondition, PlanRevisionRef
from tests.recovery.test_execution_recovery import _attempt
from tests.support.memory_substrate import MemoryReader, MemoryStore, MemoryUnitOfWork
from tests.unit.test_model_orchestration import _request


class CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def call(self, request: ModelCall) -> ModelCallResult:
        self.calls += 1
        return ModelCallResult(status=ModelCallStatus.OK, draft_text="synthetic safe draft")


def run() -> dict[str, object]:
    store = MemoryStore()
    reader = MemoryReader(store)
    caller = CountingModel()
    first = _request(unit_of_work=MemoryUnitOfWork(store), reader=reader, caller=caller)
    second = _request(unit_of_work=MemoryUnitOfWork(store), reader=reader, caller=caller)
    assert first.request is not None and second.request is not None
    assert first.content is not None and second.content is not None
    requests = reader.query(
        RecordQuery(project_id=first.request.project_id, aggregate_kind="model_outbound_request")
    )
    drafts = reader.query(
        RecordQuery(project_id=first.request.project_id, aggregate_kind="generated_content")
    )
    observations: dict[str, object] = {
        "B-MODEL-04-repeat-intent": {
            "identical_arguments": True,
            "same_outbound_request_id": first.request.request_id == second.request.request_id,
            "external_call_count": caller.calls,
            "distinct_draft_ids": first.content.generated_content_id
            != second.content.generated_content_id,
            "latest_outbound_revision": max(record.revision for record in requests.items),
            "saved_draft_count": len(drafts.items),
            "boundary": "counting synthetic model and memory store; no real outbound call",
        }
    }

    plan = PlanRevisionRef("plan", 1, "sha256:one")
    next_plan = PlanRevisionRef("plan", 2, "sha256:two")
    middle = _attempt("middle", state=AttemptState.RUNNING, upstream_attempt_ids=("upstream",))
    same_plan = invalidate_downstream_attempts(
        (middle,),
        previous_plan_revision=plan,
        current_plan_revision=plan,
        affected_upstream_attempt_ids=("upstream",),
    )
    observations["C-INVALIDATION-02-same-plan"] = {
        "affected_upstream_reported": True,
        "same_plan_revision": True,
        "running_downstream_invalidated": bool(same_plan),
        "boundary": "pure rule with an explicitly reported upstream change",
    }

    leaf = _attempt("leaf", state=AttemptState.RUNNING, upstream_attempt_ids=("middle",))
    unrelated = _attempt("unrelated", upstream_attempt_ids=("other",))
    chain = invalidate_downstream_attempts(
        (middle, leaf, unrelated),
        previous_plan_revision=plan,
        current_plan_revision=next_plan,
        affected_upstream_attempt_ids=("upstream",),
    )
    control = replace(
        _attempt("control", upstream_attempt_ids=()),
        consumed_conditions=(ConsumedCondition("upstream", "condition", "sha256:condition"),),
    )
    control_changes = invalidate_downstream_attempts(
        (control,),
        previous_plan_revision=plan,
        current_plan_revision=next_plan,
        affected_upstream_attempt_ids=("upstream",),
    )
    observations["C-INVALIDATION-03-dependency-chain"] = {
        "invalidated_attempt_ids": [item.attempt.attempt_id for item in chain],
        "transitive_leaf_invalidated": any(item.attempt.attempt_id == "leaf" for item in chain),
        "unrelated_attempt_invalidated": any(
            item.attempt.attempt_id == "unrelated" for item in chain
        ),
        "direct_control_dependency_invalidated": bool(control_changes),
        "boundary": "pure rule; no product runner or persistence acceptance",
    }
    return observations


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
