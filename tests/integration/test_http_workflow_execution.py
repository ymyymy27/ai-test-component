"""End-to-end HTTP workflow execution through the default core/commit path."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import AttemptStateFact
from aitest.domain.execution.runs import AdapterKind, RegisteredEntryRef
from tests.unit.test_authoritative_preparation import _authoritative, prepare
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_execution_authorization_origin import FixtureActionResolver, review, save
from tests.unit.test_initial_run_registration import register


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/orders/order-1":
            self._send({"data": {"id": "order-1", "status": "new"}})
        else:
            self._send({"error": "not found"}, status=404)

    def _send(self, payload: object, *, status: int = 200) -> None:
        content = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args: object) -> None:
        return


class _HttpActionResolver(FixtureActionResolver):
    def resolve(self, **kwargs):
        action = super().resolve(**kwargs)
        return replace(
            action,
            attempt=replace(
                action.attempt,
                adapter_kind=AdapterKind.HTTP,
                adapter_version="http-workflow/1.0",
            ),
            request=replace(
                action.request,
                registered_entry=RegisteredEntryRef(
                    entry_id="http-workflow",
                    adapter_kind=AdapterKind.HTTP,
                    entrypoint="http://127.0.0.1",
                ),
            ),
        )


@pytest.fixture
def server():
    instance = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_address[1]}"
    finally:
        instance.shutdown()
        thread.join(timeout=5)


def test_http_workflow_runs_as_one_non_process_attempt_and_saves_exchange_evidence(
    tmp_path, server: str
) -> None:
    spec = {
        "schema_version": "aitest.http-workflow/1.0",
        "requests": [
            {
                "request_id": "read",
                "method": "GET",
                "url": server + "/orders/order-1",
                "assertions": [
                    {
                        "assertion_id": "status",
                        "json_path": "data.status",
                        "operator": "equals",
                        "expected": "new",
                    }
                ],
            }
        ],
    }

    def transform(case):
        return replace(case, inputs=(json.dumps(spec),))

    with _authoritative(tmp_path, case_transform=transform) as (core, inputs, _):
        prepared = prepare(core, inputs)
        assert prepared.error is None, prepared.error
        facts = register(core, prepared.result)
        service = core.execution_authorizations
        service.action_resolver = _HttpActionResolver(core.unit_of_work)
        parameters = service.prepare(
            project_id=inputs.project_id,
            run_id=facts.run_id,
            step_id=facts.steps[0].step_id,
            intent_id="http-execution-intent",
            request_id="http-resolve-action",
        )
        _, action = service.resolver.read(inputs.project_id, parameters["execution_action_id"])
        actor, challenge = review(service, inputs.project_id, action, parameters)
        save(service, inputs.project_id, action, parameters, actor, challenge)

        response = core.api.dispatch(
            Command(
                action="execute_step",
                project_id=inputs.project_id,
                request_id="http-execute",
                intent_id=action.request.intent_id,
                expected_revision=0,
                target=action.request.step_id,
                parameters={
                    "execution_action_id": parameters["execution_action_id"],
                    "record_revision": 1,
                },
            ),
            RELAY,
        )
        assert response.error is None, response.error
        current = core.execution_coordinator.read_current_facts(
            project_id=inputs.project_id, run_id=facts.run_id
        )
        attempt = next(item for item in current.attempts if item.step_id == action.request.step_id)
        assert attempt.adapter_kind.value == "http"
        assert attempt.state is AttemptStateFact.COMPLETED
        assert attempt.exit_fact is None
        http_evidence = [
            item for item in current.evidence_refs if item.evidence_kind.value == "http_response"
        ]
        assert len(http_evidence) == 1
        assert http_evidence[0].attempt_id == attempt.attempt_id
        assert http_evidence[0].integrity.value == "complete"
        trace = next(
            item for item in current.trace_nodes
            if item.trace_node_id == f"trace:{attempt.attempt_id}:read"
        )
        assert trace.node_type.value == "request"
        assert trace.state.value == "succeeded"
        assert trace.truth_class.value == "real"
        assert trace.attempt_id == attempt.attempt_id

        imported = core.api.dispatch(
            Command(
                action="import_external_result",
                project_id=inputs.project_id,
                request_id="import-mock",
                intent_id="import-mock-intent",
                expected_revision=0,
                target="external-result",
                parameters={
                    "document": {
                        "schema_version": "aitest.external-result/1.0",
                        "source_instance_id": "external-mock-tool",
                        "source_record_id": "mock-result-1",
                        "layer": "L3",
                        "source_identity": None,
                        "run_id": attempt.run_id,
                        "step_id": attempt.step_id,
                        "attempt_id": attempt.attempt_id,
                        "actual_output": {"paid": True},
                        "assertion_values": {"paid": True},
                        "mock_declarations": [
                            {
                                "mock_declaration_id": "mock-payment",
                                "declaration_source": "development_declaration",
                                "replaced_object_ref": "module:payment",
                                "replaced_behavior_ref": "charge",
                                "scope": "payment",
                            }
                        ],
                        "attachments": [],
                    }
                },
            ),
            RELAY,
        )
        assert imported.error is None, imported.error
        after = core.execution_coordinator.read_current_facts(
            project_id=inputs.project_id, run_id=facts.run_id
        )
        mock = next(
            item for item in after.mock_declarations
            if item.mock_declaration_id == "mock-payment"
        )
        assert mock.verification_state.value == "unverified"
        assert mock.declaration_source.value == "development_declaration"
        assert mock.scope == "payment"

        agent = core.api.dispatch(
            Command(
                action="record_agent_evidence",
                project_id=inputs.project_id,
                request_id="record-agent",
                intent_id="record-agent-intent",
                expected_revision=0,
                target="agent-evidence",
                parameters={
                    "document": {
                        "schema_version": "aitest.agent-evidence/1.0",
                        "run_id": attempt.run_id,
                        "step_id": attempt.step_id,
                        "attempt_id": attempt.attempt_id,
                        "tool_calls": [
                            {
                                "call_id": "call-1",
                                "tool_name": "http.get",
                                "arguments_digest": "sha256:args",
                                "result_digest": "sha256:result",
                                "success": True,
                                "evidence_refs": [http_evidence[0].evidence_id],
                            }
                        ],
                        "evaluations": [
                            {
                                "business_object_id": "order-1",
                                "observation": "matched",
                                "tool_call_ids": ["call-1"],
                            }
                        ],
                    }
                },
            ),
            RELAY,
        )
        assert agent.error is None, agent.error
        final = core.execution_coordinator.read_current_facts(
            project_id=inputs.project_id, run_id=facts.run_id
        )
        verification = next(
            item for item in final.verifications
            if item.verification_of == attempt.attempt_id
        )
        assert verification.observation.value == "no_result"
        assert "agent_independent_verification_missing" in verification.gap_ids
        agent_trace = next(
            item for item in final.trace_nodes
            if item.trace_node_id == f"trace:{attempt.attempt_id}:call-1"
        )
        assert agent_trace.node_type.value == "external_call"
        assert agent_trace.state.value == "succeeded"
        assert agent_trace.truth_class.value == "unknown"
        core.lifetime_lock.release()


def test_http_workflow_transport_failure_keeps_a_partial_capture(
    tmp_path,
) -> None:
    spec = {
        "schema_version": "aitest.http-workflow/1.0",
        "requests": [
            {
                "request_id": "read",
                "method": "GET",
                "url": "http://127.0.0.1:1/orders/order-1",
                "assertions": [
                    {
                        "assertion_id": "status",
                        "json_path": "data.status",
                        "operator": "equals",
                        "expected": "new",
                    }
                ],
            }
        ],
    }

    def transform(case):
        return replace(case, inputs=(json.dumps(spec),))

    with _authoritative(tmp_path, case_transform=transform) as (core, inputs, _):
        prepared = prepare(core, inputs)
        assert prepared.error is None, prepared.error
        facts = register(core, prepared.result)
        service = core.execution_authorizations
        service.action_resolver = _HttpActionResolver(core.unit_of_work)
        parameters = service.prepare(
            project_id=inputs.project_id,
            run_id=facts.run_id,
            step_id=facts.steps[0].step_id,
            intent_id="http-gap-execution-intent",
            request_id="http-gap-resolve-action",
        )
        _, action = service.resolver.read(
            inputs.project_id, parameters["execution_action_id"]
        )
        actor, challenge = review(service, inputs.project_id, action, parameters)
        save(
            service,
            inputs.project_id,
            action,
            parameters,
            actor,
            challenge,
        )

        response = core.api.dispatch(
            Command(
                action="execute_step",
                project_id=inputs.project_id,
                request_id="http-gap-execute",
                intent_id=action.request.intent_id,
                expected_revision=0,
                target=action.request.step_id,
                parameters={
                    "execution_action_id": parameters["execution_action_id"],
                    "record_revision": 1,
                },
            ),
            RELAY,
        )
        assert response.error is None, response.error
        current = core.execution_coordinator.read_current_facts(
            project_id=inputs.project_id, run_id=facts.run_id
        )
        attempt = next(
            item for item in current.attempts
            if item.step_id == action.request.step_id
        )
        step = next(
            item for item in current.steps
            if item.step_id == action.request.step_id
        )
        assert attempt.state == AttemptStateFact.COMPLETED
        assert attempt.capture_completeness.value == "partial"
        assert step.state.value == "pending_verification"
        assert attempt.structured_result_ref is not None
        assert any(
            item.reason_code == "http_request_failed" for item in current.gaps
        )
        assert attempt.attempt_id not in current.coverage.executed_attempt_ids
        captured = [
            item for item in current.evidence_refs
            if item.attempt_id == attempt.attempt_id
        ]
        assert captured
        assert captured[0].integrity.value == "partial"
        core.lifetime_lock.release()
