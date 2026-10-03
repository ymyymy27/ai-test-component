import sys
from pathlib import Path

import pytest

from aitest.application.evidence.evidence_review import (
    EvidenceReviewService,
    VerificationRequest,
)
from aitest.domain.evidence.evidence import (
    EvidenceCaptureSource,
    VerificationObservation,
)
from aitest.domain.execution.runs import (
    AdapterKind,
    AuthorizationRef,
    ExecutionRequest,
    FailureClass,
    PlanRevisionRef,
    RegisteredEntryRef,
    SideEffectClass,
)
from aitest.domain.execution.sources import SourceCheckType
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
    HttpExchangeResult,
    HttpRequestSpec,
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


def test_agent_adapter_rejects_text_only_evaluation() -> None:
    adapter = AgentAdapter()
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
        BusinessVerificationAdapter(
            _OrderQuery({"status": "paid", "order_id": "order-1"})
        )
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
