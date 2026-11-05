"""End-to-end HTTP workflow execution through the default core/commit path."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aitest.bootstrap import assemble_workspace_core
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
        core.lifetime_lock.release()
