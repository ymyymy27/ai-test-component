"""Real Python write/fresh JSON read through default API; human/source proofs are fixtures."""

import json
import sys
from dataclasses import replace
from threading import Event, Thread
from time import sleep

from aitest.application.execution.reuse_sources import CaseReuseSourceReader
from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.application.planning.substrate_adapter import PortsRecordReader
from aitest.application.ports import VerificationRequest
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.execution.runs import SideEffectClass
from aitest.infrastructure.adapters.execution.business_query import (
    HttpBusinessQueryReader,
    HttpBusinessQuerySpec,
)
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.adapters.execution.verification import BusinessVerificationAdapter
from aitest.infrastructure.file_store.execution_handles import FileExecutionHandleStore
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_default_step_execution import ActualCommandResolver, execution_command
from tests.unit.test_execution_authorization_origin import review, save
from tests.unit.test_http_transport_limits import server
from tests.unit.test_initial_run_registration import register
from tests.unit.test_saved_business_verification import command


def test_default_actual_business_read_is_saved_and_replayed_without_requery(
    authoritative, tmp_path
):
    core, inputs, source = authoritative
    initial = register(core, prepare(core, inputs).result)
    business_file = tmp_path / "business-order.json"

    class WriteOrder(ActualCommandResolver):
        def resolve(self, **kwargs):
            action = super().resolve(**kwargs)
            script = (
                "from pathlib import Path; "
                f"Path({str(business_file)!r}).write_text("
                '\'{"paid":true,"object_id":"order-1"}\',encoding=\'utf-8\'); '
                "print('operation-finished')"
            )
            return replace(
                action,
                attempt=replace(action.attempt, side_effect_class=SideEffectClass.IDEMPOTENT_WRITE),
                request=replace(
                    action.request,
                    side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
                    registered_entry=replace(
                        action.request.registered_entry, arguments=("-c", script)
                    ),
                ),
            )

    service = core.execution_authorizations
    service.action_resolver = WriteOrder(core.unit_of_work)
    parameters = service.prepare(
        project_id=inputs.project_id,
        run_id=initial.run_id,
        step_id=initial.steps[0].step_id,
        intent_id="write-order-intent",
        request_id="prepare-order",
    )
    _, action = service.resolver.read(inputs.project_id, parameters["execution_action_id"])
    actor, challenge = review(service, inputs.project_id, action, parameters)
    save(service, inputs.project_id, action, parameters, actor, challenge)
    port = CommandAdapter(
        spool_store=FileSpoolStore(core.workspace.root),
        handle_store=FileExecutionHandleStore(core.workspace.root),
    )
    port.register(CommandRegistration("public-python", sys.executable, source))
    core.step_execution.execution_port = port
    executed = core.api.dispatch(execution_command(inputs.project_id, action, parameters), RELAY)
    assert executed.error is None, executed.error
    assert executed.result["attempt"]["state"] == "completed"
    assert executed.result["attempt"]["exit_fact"]["real_exit_code"] == 0
    assert business_file.exists()
    facts = ExecutionFacts.model_validate(executed.result["execution_facts"])

    class QueryOrder:
        calls = 0

        def read_business_object(self, *, business_object_id, target_deployment_ref):
            assert core.unit_of_work.project is None
            assert (business_object_id, target_deployment_ref) == (
                "order-1",
                "fixture-business-deployment",
            )
            self.calls += 1
            # Fresh independent handle: never inspect or reuse command stdout.
            with business_file.open(encoding="utf-8") as stream:
                return json.load(stream)

    class ResolveOrder:
        def resolve(self, *, facts, step_id, attempt_id):
            assert facts.current_attempt_by_step[step_id] == attempt_id
            return VerificationRequest(
                attempt_id,
                "order-1",
                "read-saved-order",
                "immediate",
                "fixture-business-deployment",
                expected_facts={"paid": True, "object_id": "order-1"},
            )

    query = QueryOrder()
    core.business_verification.resolver = ResolveOrder()
    core.business_verification.verifier = BusinessVerificationAdapter(query)
    first_cmd = command(facts)
    first = core.api.dispatch(first_cmd, RELAY)
    assert first.error is None, first.error
    assert first.result["verification"]["observation"] == "matched"
    after = ExecutionFacts.model_validate(first.result["execution_facts"])
    assert after.attempts == facts.attempts and after.coverage == facts.coverage
    assert not after.run.result_ref and after.run.evidence_level is None
    business_file.write_text('{"paid":false,"object_id":"order-1"}', encoding="utf-8")
    replay = core.api.dispatch(
        first_cmd.model_copy(update={"request_id": "first-query-replay"}), RELAY
    )
    assert replay.error is None, replay.error
    assert replay.result == first.result and query.calls == 1
    second_cmd = command(after, request_id="second-query", intent_id="second-query-intent")
    second = core.api.dispatch(second_cmd, RELAY)
    assert second.error is None, second.error
    assert second.result["verification"]["observation"] == "mismatched" and query.calls == 2
    assert len(second.result["execution_facts"]["verifications"]) == 2
    # Both actual independent observations are readable through the whole-case
    # historic source path, without another query or changes to execution facts.
    source_facts = ExecutionFacts.model_validate(second.result["execution_facts"])
    selected_case = next(
        item.case_id for item in source_facts.steps if item.step_id == action.attempt.step_id
    )
    sequence = core.unit_of_work.current_commit_sequence()
    selected = CaseReuseSourceReader(
        core.unit_of_work.repo,
        PortsRecordReader(core.unit_of_work.repo),
        objects=FileObjectStore(core.workspace.root),
        spool=FileSpoolStore(core.workspace.root),
    ).read(
        project_id=inputs.project_id,
        run_id=source_facts.run_id,
        case_id=selected_case,
        reference=SnapshotContentRef.of(source_facts),
    )
    assert selected.facts == source_facts and query.calls == 2
    assert core.unit_of_work.current_commit_sequence() == sequence
    # The actual independent object becomes visible after the first missing
    # read; one real monotonic deadline and fresh file handles govern polling.
    first_missing = Event()
    business_file.unlink()

    class PollingOrder(QueryOrder):
        def read_business_object_before(self, *, deadline_monotonic, **kwargs):
            try:
                return self.read_business_object(**kwargs)
            except FileNotFoundError:
                if not first_missing.is_set():
                    first_missing.set()
                    publisher.start()
                return None

    class PollingResolver(ResolveOrder):
        def resolve(self, **kwargs):
            return replace(
                super().resolve(**kwargs),
                deadline_condition="poll_deadline_ms:2000",
                query_interval="poll_interval_ms:50",
            )

    def publish_later():
        sleep(0.06)
        temporary = business_file.with_suffix(".next")
        temporary.write_text('{"paid":true,"object_id":"order-1"}', encoding="utf-8")
        temporary.replace(business_file)

    publisher = Thread(target=publish_later)
    polling = PollingOrder()
    core.business_verification.resolver = PollingResolver()
    core.business_verification.verifier = BusinessVerificationAdapter(polling)
    polled_cmd = command(source_facts, request_id="polled-query", intent_id="polled-query-intent")
    polled = core.api.dispatch(polled_cmd, RELAY)
    publisher.join(3)
    assert not publisher.is_alive()
    assert polled.error is None, polled.error
    assert polled.result["verification"]["observation"] == "matched" and polling.calls >= 2
    polled_facts = ExecutionFacts.model_validate(polled.result["execution_facts"])

    # The production deadline reader also reaches the same saved core via an
    # actual independent GET, without using the command's output or its cache.
    def http_read_order(connection, stopped):
        body = business_file.read_bytes()
        connection.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )

    http_cmd = command(
        polled_facts, request_id="http-independent-query", intent_id="http-query-intent"
    )
    with server(http_read_order) as (url, http_calls):
        core.business_verification.verifier = BusinessVerificationAdapter(
            HttpBusinessQueryReader(
                HttpBusinessQuerySpec(
                    "fixture-business-deployment", url + "orders/{business_object_id}"
                ),
            )
        )
        http_result = core.api.dispatch(http_cmd, RELAY)
        assert http_result.error is None, http_result.error
        assert http_result.result["verification"]["observation"] == "matched"
        assert len(http_calls) == 1 and http_calls[0].startswith(b"GET /orders/order-1 ")
    polled_facts = ExecutionFacts.model_validate(http_result.result["execution_facts"])
    sequence = core.unit_of_work.current_commit_sequence()
    assert (
        CaseReuseSourceReader(
            core.unit_of_work.repo,
            PortsRecordReader(core.unit_of_work.repo),
            objects=FileObjectStore(core.workspace.root),
            spool=FileSpoolStore(core.workspace.root),
        )
        .read(
            project_id=inputs.project_id,
            run_id=polled_facts.run_id,
            case_id=selected_case,
            reference=SnapshotContentRef.of(polled_facts),
        )
        .facts
        == polled_facts
    )
    assert core.unit_of_work.current_commit_sequence() == sequence
    poll_count = polling.calls
    root = core.workspace.root
    core.lifetime_lock.release()
    reopened = assemble_workspace_core(root, instance_id="query-reopened-core")
    try:
        for index, (value, expected) in enumerate(
            (
                (first_cmd, first.result),
                (second_cmd, second.result),
                (polled_cmd, polled.result),
                (http_cmd, http_result.result),
            )
        ):
            result = reopened.api.dispatch(
                value.model_copy(update={"request_id": f"reopened-query-{index}"}), RELAY
            )
            assert result.error is None, result.error
            assert result.result == expected
        assert query.calls == 2
        assert polling.calls == poll_count
    finally:
        reopened.lifetime_lock.release()
