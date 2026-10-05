"""Subsequent revisions compare effective case content, never just the initial plan."""

from dataclasses import replace

from aitest.application.planning.run_mode import runtime_facts_from_execution_facts
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.planning.plans import CaseRevisionRef
from aitest.domain.planning.runtime_revision import (
    CaseRuntimeChange,
    RuntimeRevisionRequest,
    evaluate_runtime_revision,
)
from tests.unit.test_runtime_revision import _case, _failure_in_progress, _plan


def test_second_revision_uses_the_effective_case_version():
    current = _case(revision=2)
    facts = replace(
        runtime_facts_from_execution_facts(ExecutionFacts.model_validate(_failure_in_progress())),
        runtime_revision_count=1,
        effective_case_revisions=(
            CaseRevisionRef(current.case_id, current.revision, "sha256:effective-case-2"),
        ),
    )
    request = RuntimeRevisionRequest(
        base_plan_revision_id=facts.plan_revision_id,
        base_plan_revision_no=facts.plan_revision_no,
        base_plan_revision_digest=facts.plan_revision_digest,
        observed_snapshot_cursor=facts.snapshot_cursor,
        case_changes=(CaseRuntimeChange(replace(current, revision=3)),),
        reason="next effective case revision",
        operator_ref="controlled-operator",
    )
    decision = evaluate_runtime_revision(
        plan=_plan(),
        cases=(current,),
        confirmations=(),
        request=request,
        facts=facts,
    )
    assert decision.accepted, decision.refusals
