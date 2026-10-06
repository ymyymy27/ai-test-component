import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aitest.application.evidence.evidence_review import (
    EvidenceReviewService,
    VerificationRequest,
)
from aitest.application.execution.facts import (
    ExecutionFactsAssembler,
    ExecutionFactsAssembly,
)
from aitest.domain.evidence.evidence import (
    EvidenceCaptureSource,
    VerificationObservation,
)
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    AuthorizationRef,
    ExecutionRequest,
    FailureClass,
    PlanRevisionRef,
    RegisteredEntryRef,
    Run,
    RunControlState,
    RunTier,
    SideEffectClass,
    Step,
    StepLevel,
    StepRevisionRef,
    StepState,
)
from aitest.domain.execution.sources import SourceCheckType
from aitest.domain.project.context import IsolationMode
from aitest.infrastructure.adapters.execution.agent import (
    AgentAdapter,
    AgentEvaluation,
    AgentToolCall,
)
from aitest.infrastructure.adapters.execution.command import CommandRegistration
from aitest.infrastructure.adapters.execution.external_result import (
    ExternalResultAdapter,
    ExternalResultPayload,
)
from aitest.infrastructure.adapters.execution.http import (
    HttpAdapter,
    HttpAssertion,
    HttpAssertionOperator,
    HttpAssertionResult,
    HttpExchangeResult,
    HttpRequestSpec,
    http_assertion_verifications,
)
from aitest.infrastructure.adapters.execution.manual_evidence import (
    ManualEvidenceAdapter,
    ManualOperationType,
    ManualStep,
)
from aitest.infrastructure.adapters.execution.python_checks import PythonChecksAdapter
from aitest.infrastructure.adapters.execution.verification import (
    BusinessVerificationAdapter,
)


def _request(arguments: tuple[str, ...]) -> ExecutionRequest:
    executable = str(Path(sys.executable).resolve())
    return ExecutionRequest(
        project_id="project-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        intent_id="intent-1",
        resolved_input_digest="sha256:input-1",
        registered_entry=RegisteredEntryRef(
            entry_id="python",
            adapter_kind=AdapterKind.PYTHON_CHECKS,
            entrypoint=executable,
            arguments=arguments,
        ),
        materialized_snapshot_ref=str(Path.cwd()),
        environment_ref="environment-1",
        source_binding_digest="sha256:source-1",
        authorization_ref=AuthorizationRef(
            authorization_id="authorization-1",
            intent_id="intent-1",
            step_id="step-1",
            resolved_input_digest="sha256:input-1",
            target_ref="target-1",
            credential_scope_ref="scope-1",
            plan_revision_ref=PlanRevisionRef(
                revision_id="plan-1",
                revision_no=1,
                digest="sha256:plan-1",
            ),
        ),
        side_effect_class=SideEffectClass.READ_ONLY,
    )


def test_python_checks_adapter_runs_and_classifies_real_command() -> None:
    adapter = PythonChecksAdapter()
    adapter.register(
        CommandRegistration(
            entry_id="python",
            executable=sys.executable,
            cwd=Path.cwd(),
        )
    )

    result = adapter.run_check(
        _request(("-c", "print('ok')")),
        check_result_id="check-1",
        check_type=SourceCheckType.MINIMAL_START,
        scope="module",
        source_snapshot_ref="snapshot-1",
        environment_ref="environment-1",
        rules_revision="rules-1",
    )

    assert result.failure_class is FailureClass.PASSED
    assert result.adapter_kind is AdapterKind.PYTHON_CHECKS


def test_http_adapter_extracts_and_evaluates_json_assertions() -> None:
    spec = HttpRequestSpec(
        request_id="request-1",
        method="POST",
        url="https://example.invalid/orders",
        extract_paths=(("order_id", "data.order_id"),),
        assertions=(
            HttpAssertion(
                assertion_id="status",
                json_path="data.status",
                operator=HttpAssertionOperator.EQUALS,
                expected="paid",
            ),
            HttpAssertion(
                assertion_id="items",
                json_path="data.items",
                operator=HttpAssertionOperator.CONTAINS,
                expected=2,
            ),
        ),
    )
    exchange = HttpExchangeResult(
        request_id="request-1",
        method="POST",
        url=spec.url,
        status=200,
        body=b'{"data":{"order_id":"order-1","status":"paid","items":[1,2]}}',
    )

    enriched = HttpAdapter._enrich(exchange, spec)

    assert enriched.extracted == {"order_id": "order-1"}
    assert all(item.matched for item in enriched.assertion_results)
    assert enriched.request_log_ref == "http-request:request-1"


def test_http_assertions_convert_to_verification_facts() -> None:
    exchange = HttpExchangeResult(
        request_id="request-1",
        method="POST",
        url="https://example.invalid/orders",
        status=200,
        body=b'{"data":{"status":"paid"}}',
        request_log_ref="http-request:request-1",
    )
    spec = HttpRequestSpec(
        request_id="request-1",
        method="POST",
        url=exchange.url,
        assertions=(
            HttpAssertion(
                assertion_id="status",
                json_path="data.status",
                operator=HttpAssertionOperator.EQUALS,
                expected="paid",
            ),
        ),
    )

    enriched = HttpAdapter._enrich(exchange, spec)
    verifications = http_assertion_verifications(
        enriched,
        business_object_id="order-1",
        evidence_refs=("evidence-1",),
        covers_critical_chain_item_ids=("critical-1",),
    )

    assert len(verifications) == 1
    verification = verifications[0]
    assert verification.observation is VerificationObservation.MATCHED
    assert verification.actual_result_ref == "http-request:request-1"
    assert verification.evidence_refs == ("evidence-1",)
    assert verification.covers_critical_chain_item_ids == ("critical-1",)


def test_http_network_error_converts_to_query_error_verification() -> None:
    exchange = HttpExchangeResult(
        request_id="request-1",
        method="GET",
        url="https://example.invalid/orders",
        status=None,
        error_class="network",
        error_detail="connection refused",
        request_log_ref="http-request:request-1",
    )

    verifications = http_assertion_verifications(
        exchange,
        business_object_id="order-1",
        evidence_refs=("evidence-1",),
    )

    assert len(verifications) == 1
    assert verifications[0].observation is VerificationObservation.QUERY_ERROR
    assert verifications[0].gap_ids == ("http_network",)


def test_http_verification_reaches_execution_facts_contract() -> None:
    exchange = HttpExchangeResult(
        request_id="request-1",
        method="GET",
        url="https://example.invalid/orders",
        status=200,
        body=b'{"status":"paid"}',
        assertion_results=(
            HttpAssertionResult(assertion_id="status", matched=True, actual="paid"),
        ),
        request_log_ref="http-request:request-1",
    )
    verification = http_assertion_verifications(
        exchange,
        business_object_id="order-1",
        evidence_refs=("evidence-1",),
    )[0]
    plan = PlanRevisionRef("plan-1", 1, "sha256:plan-1")
    step_revision = StepRevisionRef("step-rev-1", 1, "sha256:step-1")
    run = Run(
        run_id="run-1",
        project_id="project-1",
        origin_workspace_id="workspace-1",
        intent_id="intent-1",
        tier=RunTier.FULL,
        driver="planned",
        conclusion_ceiling="passable",
        plan_revision_ref=plan,
        environment_ref="environment-1",
        environment_isolation_mode=IsolationMode.VENV,
        rules_revision="rules-1",
        control_state=RunControlState.COMPLETED,
        required_scope=frozenset({"case-1"}),
        selected_scope=frozenset({"case-1"}),
    )
    step = Step(
        step_id="step-1",
        run_id="run-1",
        ordinal=1,
        case_id="case-1",
        level=StepLevel.L2,
        step_revision_ref=step_revision,
        state=StepState.COMPLETED,
        current_attempt_id="attempt-1",
    )
    attempt = Attempt(
        attempt_id="attempt-1",
        run_id="run-1",
        step_id="step-1",
        attempt_index=1,
        resolved_input_digest="sha256:input-1",
        step_revision_ref=step_revision,
        source_binding_digest="sha256:source-1",
        side_effect_class=SideEffectClass.READ_ONLY,
        adapter_kind=AdapterKind.HTTP,
        adapter_version="1.0",
        state=AttemptState.COMPLETED,
    )
    facts = ExecutionFactsAssembler().assemble(
        ExecutionFactsAssembly(
            facts_id="facts-http",
            snapshot_commit_id="commit-http",
            snapshot_cursor=1,
            snapshot_revision=1,
            committed_at=datetime.now(UTC),
            run=run,
            steps=(step,),
            attempts=(attempt,),
            verifications=(verification,),
        )
    )

    assert facts.verifications[0].observation.value == "matched"
    assert facts.verifications[0].evidence_refs == ("evidence-1",)


def test_agent_adapter_rejects_text_only_evaluation() -> None:
    class EvidenceValidator:
        def exists(self, evidence_ref: str) -> bool:
            return evidence_ref == "evidence-1"

    adapter = AgentAdapter(evidence_validator=EvidenceValidator())
    adapter.record_tool_call(
        AgentToolCall(
            call_id="call-1",
            tool_name="query_order",
            arguments_digest="sha256:args-1",
            result_digest="sha256:result-1",
            success=True,
            evidence_refs=("evidence-1",),
        )
    )
    evaluation = AgentEvaluation(
        verification_id="verify-1",
        verification_of="query_order",
        business_object_id="order-1",
        observation=VerificationObservation.MATCHED,
        tool_call_ids=("call-1",),
    )

    result = adapter.evaluate(evaluation)

    assert result.business_object_id == "order-1"
    assert result.evidence_refs == ("evidence-1",)
    with pytest.raises(ValueError, match="unknown agent evidence refs"):
        adapter.evaluate(
            AgentEvaluation(
                verification_id="verify-unknown",
                verification_of="query_order",
                business_object_id="order-1",
                observation=VerificationObservation.MATCHED,
                evidence_refs=("missing-evidence",),
            )
        )
    with pytest.raises(ValueError, match="text self-report"):
        adapter.evaluate(
            AgentEvaluation(
                verification_id="verify-2",
                verification_of="query_order",
                business_object_id="order-1",
                observation=VerificationObservation.MATCHED,
            )
        )


def test_manual_adapter_preserves_operation_and_attachment_provenance() -> None:
    adapter = ManualEvidenceAdapter()
    record = adapter.record(
        ManualStep(
            step_id="step-1",
            operation_type=ManualOperationType.PAGINATION,
            sequence=1,
            target_ref="orders-page",
            observed_result="page 2 shown",
            business_object_id="order-1",
            attachment_refs=("evidence:page-2",),
        )
    )
    node = adapter.to_trace_node(record, run_id="run-1")

    assert record.capture_source is EvidenceCaptureSource.MANUAL
    assert node.navigation_path == "orders-page"
    assert node.actual_parent_node_id == "order-1"


def test_external_result_validates_schema_digest_and_idempotency() -> None:
    adapter = ExternalResultAdapter()
    payload = ExternalResultPayload(
        import_id="import-1",
        external_schema="order-result/1.0",
        source_instance_id="external-1",
        source_record_id="order-1",
        content={"status": "paid"},
        assertion_values={"status": "paid"},
    )

    first = adapter.validate(
        payload,
        expected_schema="order-result/1.0",
        expected_assertions={"status": "paid"},
        verification_id="verify-1",
    )
    duplicate = adapter.validate(
        payload,
        expected_schema="order-result/1.0",
        expected_assertions={"status": "paid"},
        verification_id="verify-2",
    )

    assert first.verification.observation is VerificationObservation.MATCHED
    assert duplicate.import_ref.idempotency_state == "duplicate"
    with pytest.raises(ValueError, match="conflicts"):
        adapter.validate(
            ExternalResultPayload(
                import_id="import-1",
                external_schema="order-result/1.0",
                source_instance_id="external-1",
                source_record_id="order-1",
                content={"status": "failed"},
                assertion_values={"status": "failed"},
            ),
            expected_schema="order-result/1.0",
            expected_assertions={"status": "paid"},
            verification_id="verify-3",
        )


def test_external_result_without_expected_assertions_is_not_matched() -> None:
    result = ExternalResultAdapter().validate(
        ExternalResultPayload(
            import_id="import-empty",
            external_schema="order-result/1.0",
            source_instance_id="external-1",
            source_record_id="order-1",
            content={"status": "paid"},
            assertion_values={"status": "paid"},
        ),
        expected_schema="order-result/1.0",
        expected_assertions={},
        verification_id="verify-empty",
    )

    assert result.verification.observation is VerificationObservation.NO_RESULT
    assert result.verification.gap_ids == ("expected_assertions_missing",)


class _OrderQuery:
    def __init__(self, value: dict[str, object]) -> None:
        self._value = value

    def read_business_object(
        self,
        *,
        business_object_id: str,
        target_deployment_ref: str,
    ) -> dict[str, object] | None:
        assert business_object_id == "order-1"
        assert target_deployment_ref == "deployment-1"
        return self._value


def test_business_verification_uses_independent_query_facts() -> None:
    request = VerificationRequest(
        verification_of="query_order",
        business_object_id="order-1",
        query_method="read_only_query",
        deadline_condition="immediate",
        target_deployment_ref="deployment-1",
        expected_facts={"status": "paid"},
    )
    service = EvidenceReviewService(
        BusinessVerificationAdapter(_OrderQuery({"status": "paid", "order_id": "order-1"}))
    )

    matched = service.review(request)
    mismatch_request = VerificationRequest(
        verification_of="query_order",
        business_object_id="order-1",
        query_method="read_only_query",
        deadline_condition="immediate",
        target_deployment_ref="deployment-1",
        expected_facts={"status": "failed"},
    )
    mismatch = service.review(mismatch_request)

    assert matched.observation is VerificationObservation.MATCHED
    assert mismatch.observation is VerificationObservation.MISMATCHED


def test_business_verification_without_expected_facts_is_not_matched() -> None:
    request = VerificationRequest(
        verification_of="query_order",
        business_object_id="order-1",
        query_method="read_only_query",
        deadline_condition="immediate",
        target_deployment_ref="deployment-1",
    )
    service = EvidenceReviewService(BusinessVerificationAdapter(_OrderQuery({"status": "paid"})))

    result = service.review(request)

    assert result.observation is VerificationObservation.NO_RESULT
    assert result.gap_ids == ("expected_facts_missing",)
