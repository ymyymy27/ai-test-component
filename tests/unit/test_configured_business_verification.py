"""Exact saved basis and real HTTP; authorizations/environment remain explicit fixtures."""

import json
from contextlib import nullcontext
from dataclasses import replace

import pytest

from aitest.application.evidence.verification_resolver import BASIS_VERSION, declared_request
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.infrastructure.business_query_configuration import (
    CONFIG_VERSION,
    HostConfiguredBusinessQueries,
)
from tests.unit.test_http_business_query import body_reply
from tests.unit.test_http_transport_limits import server


def basis(**changes):
    data = dict(schema_version=BASIS_VERSION, business_object_id="order-1",
                query_method="project-ticket:read-order", target_deployment_ref="deployment-1",
                expected_facts={"paid": True}, query_interval="configured",
                deadline_condition="immediate")
    data.update(changes)
    return json.dumps(data)


def config(path, url):
    path.write_text(json.dumps({"schema_version": CONFIG_VERSION, "queries": [{
        "query_method": "project-ticket:read-order", "target_deployment_ref": "deployment-1",
        "url_template": url + "orders/{business_object_id}", "object_id_field": "object_id",
    }]}), encoding="utf-8")
    return path


@pytest.mark.parametrize("changes", [
    {"expected_facts": {}}, {"expected_facts": []}, {"extra": True},
    {"schema_version": "unknown"}, {"query_method": "another-project:read-order"},
    {"query_method": "project-ticket:"}, {"deadline_condition": "tomorrow"},
    {"query_method": "project-ticket:other-project:read"},
    {"business_object_id": 1},
])
def test_invalid_or_cross_project_basis_refused(changes):
    with pytest.raises(ValueError):
        declared_request(basis(**changes), project_id="project-ticket", attempt_id="attempt-1")


@pytest.mark.parametrize("text", [
    "read back the object", '{"schema_version":1,"schema_version":2}',
])
def test_free_text_and_duplicate_json_not_guessed(text):
    with pytest.raises(ValueError):
        declared_request(text, project_id="project-ticket", attempt_id="attempt-1")


@pytest.mark.parametrize("body,status,observation", [
    (b'{"object_id":"order-1","paid":true}', 200, "matched"),
    (b'{"object_id":"order-1","paid":false}', 200, "mismatched"),
    (b'{}', 404, "no_result"),
])
def test_registered_fresh_read_and_frozen_configuration(tmp_path, body, status, observation):
    with server(body_reply(body, status)) as (url, calls):
        path = config(tmp_path / "queries.json", url)
        reader = HostConfiguredBusinessQueries(path)
        path.write_text("invalid now", encoding="utf-8")
        request = declared_request(basis(), project_id="project-ticket", attempt_id="attempt-1")
        result = reader.capture(request)
        assert result.verification.observation.value == observation
        assert len(calls) == 1 and calls[0].startswith(b"GET /orders/order-1 ")
        with pytest.raises(ValueError, match="not registered"):
            reader.capture(replace(request, target_deployment_ref="another-deployment"))
        assert len(calls) == 1
        assert not HostConfiguredBusinessQueries(path).valid


@pytest.mark.parametrize("raw", [b"{}", b"x" * (256 * 1024 + 1), b'{"queries":[],"queries":[]}'],
                         ids=["wrong-shape", "oversize", "duplicate-key"])
def test_bad_registry_blocks_without_partial_registration(tmp_path, raw):
    path = tmp_path / "queries.json"
    path.write_bytes(raw)
    registry = HostConfiguredBusinessQueries(path)
    assert not registry.valid and not registry.entries


def test_http_registry_cannot_claim_the_pure_function_method(tmp_path):
    path = config(tmp_path / "queries.json", "http://127.0.0.1:1/")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["queries"][0]["query_method"] = "project-ticket:pure-function-stdout"
    path.write_text(json.dumps(data), encoding="utf-8")
    registry = HostConfiguredBusinessQueries(path)
    assert not registry.valid and not registry.entries


def test_default_resolver_reads_frozen_case_and_rejects_stale_attempt(tmp_path, monkeypatch):
    from tests.unit.test_authoritative_preparation import _authoritative, prepare
    from tests.unit.test_initial_run_registration import register

    path = config(tmp_path / "queries.json", "http://127.0.0.1:1/")
    monkeypatch.setenv("AITEST_BUSINESS_QUERIES", str(path))
    with _authoritative(tmp_path, basis_text=basis()) as (core, inputs, _):
        initial = register(core, prepare(core, inputs).result)
        resolver = core.business_verification.resolver
        assert resolver is not None and core.business_verification.verifier is not None
        step = initial.steps[0]
        # Resolver only: current-attempt map is synthetic; no verification is saved by this test.
        facts = initial.model_copy(update={"current_attempt_by_step": {step.step_id: "attempt-1"}})
        request = resolver.resolve(facts=facts, step_id=step.step_id, attempt_id="attempt-1")
        assert request.expected_facts == {"paid": True}
        assert request.query_method == "project-ticket:read-order"
        with pytest.raises(ValueError, match="current attempt"):
            resolver.resolve(facts=facts, step_id=step.step_id, attempt_id="old-attempt")
        stale = facts.model_copy(update={"steps": (step.model_copy(update={
            "step_revision_ref": step.step_revision_ref.model_copy(update={"digest": "stale"})
        }), *facts.steps[1:])})
        assert isinstance(stale, ExecutionFacts)
        with pytest.raises(ValueError, match="basis changed"):
            resolver.resolve(facts=stale, step_id=step.step_id, attempt_id="attempt-1")


def test_published_plan_actual_execution_query_report_and_restart(
    tmp_path, monkeypatch, *, dynamic=False, tls_context=None,
    credential_reference=None, pure=False, complete_library=False,
):
    from aitest.bootstrap import assemble_workspace_core
    from aitest.contracts.commands import Command
    from tests.unit.test_authoritative_preparation import _authoritative
    from tests.unit.test_default_basis_confirmation import confirm
    from tests.unit.test_default_execution_authorization import RELAY
    from tests.unit.test_default_start_source_chain import _chain_environment
    from tests.unit.test_default_step_execution import execution_command
    from tests.unit.test_execution_authorization_origin import review, save
    from tests.unit.test_initial_run_registration import register
    from tests.unit.test_saved_business_verification import command

    business_file = tmp_path / "order.json"
    source = (
        "from pathlib import Path\n"
        f"Path({str(business_file)!r}).write_text("
        "'{\"object_id\":\"order-1\",\"paid\":true}', encoding='utf-8')\n"
    )
    declaration = basis()
    if dynamic:
        source = (
            "import json, uuid\nfrom pathlib import Path\n"
            "data = {'object_id': str(uuid.uuid4()), 'paid': True}\n"
            f"Path({str(business_file)!r}).write_text(json.dumps(data), encoding='utf-8')\n"
            "print(json.dumps({'created': {'id': data['object_id']}}))\n"
        )
        data = json.loads(declaration)
        del data["business_object_id"]
        data.update(schema_version="aitest.business-query-basis/2",
                    business_object_source={"stream": "stdout", "json_path": ["created", "id"]})
        declaration = json.dumps(data)

    if pure:
        source = ("import json\ndef add(a, b): return a + b\n"
                  "print(json.dumps({\"result\": add(2, 3)}))\n")
        declaration = json.dumps({"schema_version": "aitest.pure-function-basis/1",
                                  "expected_facts": {"result": 5}})
    if complete_library:
        source = (
            "import json\n"
            "def add(a, b):\n"
            "    if type(a) is not int or type(b) is not int: "
            "raise TypeError('integers required')\n"
            "    return a+b\n"
            "try: add(None, 1)\n"
            "except TypeError as error: failure=type(error).__name__\n"
            "print(json.dumps({'normal':add(2,3), 'zero':add(0,0), 'negative':add(-1,1),\n"
            " 'large':add(2**64,1), 'error':failure}))\n"
        )
        declaration = json.dumps({
            "schema_version": "aitest.pure-function-basis/1", "expected_facts": {
            "normal": 5, "zero": 0, "negative": 0, "large": 18446744073709551617,
            "error": "TypeError",
        }})
    templates = [{"template_id": "python-library" if complete_library else "ticket-workflow",
                  "version": "1.0.0"}]

    def library_cases(cases):
        from aitest.domain.planning.plans import CaseLayer

        return tuple(replace(
            cases[0], case_id=f"library-{name}",
            layer=CaseLayer.L1 if name == "syntax" else CaseLayer.L2,
            objective=f"Python library {name}: frozen normal, boundary and exception expectations",
            preconditions=("registered Python interpreter and frozen main.py",),
            inputs=("(2,3), (0,0), (-1,1), (2**64,1), (None,1)",),
            steps=("execute declared library check suite",),
            expected="frozen integers and TypeError match exactly",
            verification_method="pure-function-stdout",
            independent_verification="independent frozen expected_facts comparison",
            links=replace(cases[0].links, critical_path_ids=frozenset(),
                          no_critical_path_reason="local arithmetic library has no external chain"),
        ) for name in ("syntax", "normal", "boundary", "error"))

    def reply(connection, stopped):
        body_reply(business_file.read_bytes())(connection, stopped)

    def function_case(case):
        return replace(case, objective="evaluate add(2, 3) against independent expected value",
                       preconditions=("the registered Python carrier is available",),
                       inputs=("2", "3"),
                       steps=("evaluate add(2, 3)", "repeat add(2, 3) independently"),
                       expected="result equals 5", verification_method="pure-function-stdout",
                       independent_verification="compare saved output with frozen expected_facts")

    with (nullcontext(("http://127.0.0.1:1/", [])) if pure
          else server(reply, tls_context)) as (url, calls):
        if tls_context is not None:
            url = ("https" + url[4:]).replace("127.0.0.1", "localhost")
        path = config(tmp_path / "queries.json", url)
        if credential_reference:
            configured = json.loads(path.read_text(encoding="utf-8"))
            configured["queries"][0]["credential_reference"] = credential_reference
            path.write_text(json.dumps(configured), encoding="utf-8")
        if pure:
            monkeypatch.delenv("AITEST_BUSINESS_QUERIES", raising=False)
        else:
            monkeypatch.setenv("AITEST_BUSINESS_QUERIES", str(path))
        with _authoritative(tmp_path, basis_text=declaration, main_source=source,
                            case_transform=function_case if pure else None,
                            template_versions=templates,
                            case_set_transform=library_cases if complete_library else None,
                            environment_resolver=_chain_environment()) as (core, inputs, _):
            confirmed = confirm(core, inputs)
            assert confirmed.error is None, confirmed.error
            confirmations = [confirmed.result]
            if pure:
                for index, entry in enumerate(inputs.assertion_bases[1:], 1):
                    other = confirm(
                        core, replace(inputs, assertion_bases=(entry,)),
                        intent=f"pure-basis-{index}", request=f"pure-basis-request-{index}",
                    )
                    assert other.error is None, other.error
                    confirmations.append(other.result)
            parameters = {
                "mode": "from_saved", "project_revision": inputs.input_revisions.project_revision,
                "binding_id": inputs.binding_id, "binding_revision": inputs.binding_revision,
                "plan_id": inputs.plan_revision.revision_id,
                "plan_revision": inputs.plan_revision.revision_no,
                "snapshot_id": inputs.snapshot.source_snapshot_id,
                "snapshot_revision": inputs.snapshot.record_revision,
                "environment_id": inputs.environment.environment_id,
                "environment_revision": inputs.environment.revision,
                "client_id": "workflow-test", "prepare_request_id": "workflow-prepare",
                "execution_source": inputs.execution_source.model_copy(update={
                    "registered_entry": "python-main", "entry_arguments": ("main.py",),
                    "allowed_env_keys": (), "secret_refs": (),
                }).model_dump(mode="json"),
                "selected_case_ids": list(inputs.selected_case_ids),
                "confirmation_refs": [{key: saved[key] for key in (
                    "confirmation_id", "case_id", "basis_revision", "confirmed_at_commit",
                )} for saved in confirmations],
            }
            preparation_command = Command(
                action="prepare_run", project_id=inputs.project_id, request_id="prepare-workflow",
                intent_id="prepare-workflow-intent", expected_revision=0, parameters=parameters,
            )
            before = core.unit_of_work.current_commit_sequence()
            invalid = core.api.dispatch(preparation_command.model_copy(update={
                "parameters": {**parameters, "selected_case_ids": ["outside-plan"]},
                "request_id": "invalid-selection",
            }), RELAY)
            assert invalid.error is not None and invalid.error.code == "B_INVALID_PARAMETER"
            assert core.unit_of_work.current_commit_sequence() == before
            prepared = core.api.dispatch(preparation_command, RELAY)
            assert prepared.error is None and prepared.result["status"] == "prepared"
            selected_basis = next(item for item in prepared.result["assertion_bases"]
                                  if item["case_id"] == confirmed.result["case_id"])
            assert selected_basis["assertion_basis_state"] == "confirmed"
            again = core.api.dispatch(preparation_command.model_copy(update={
                "request_id": "same-preparation-replay",
            }), RELAY)
            assert again.error is None and again.result == prepared.result
            from tests.support.controlled_publication import controlled_publication_save

            original_plan = core.unit_of_work.repo.read(
                aggregate_kind="plan", record_id=parameters["plan_id"], revision=1,
            ).payload
            alternative = controlled_publication_save(
                core, "publish_plan", project=inputs.project_id,
                request="alternative-plan", intent="alternative-plan-intent",
                parameters={
                    "plan_id": "alternative-plan", "revision": 1,
                    "scope": dict(core.unit_of_work.repo.read(
                        aggregate_kind="acceptance_scope", record_id=original_plan["scope_id"],
                        revision=original_plan["scope_revision"],
                    ).payload),
                    "cases": [dict(core.unit_of_work.repo.read(
                        aggregate_kind="case", record_id=ref["case_id"], revision=ref["revision"],
                    ).payload) for ref in original_plan["case_revisions"]],
                    "template_versions": templates,
                },
            )
            assert alternative.error is None and alternative.result["published"]
            switched = core.api.dispatch(preparation_command.model_copy(update={
                "request_id": "switch-plan-same-prepare",
                "parameters": {**parameters, "plan_id": "alternative-plan"},
            }), RELAY)
            assert switched.error is None and switched.result["status"] == "blocked"
            assert "needs_reprepare" in str(switched.result)
            initial = register(core, prepared.result)
            service = core.execution_authorizations
            parameters = service.prepare(
                project_id=inputs.project_id, run_id=initial.run_id,
                step_id=initial.steps[0].step_id, intent_id="actual-order", request_id="resolve",
            )
            _, action = service.resolver.read(inputs.project_id, parameters["execution_action_id"])
            actor, challenge = review(service, inputs.project_id, action, parameters)
            save(service, inputs.project_id, action, parameters, actor, challenge)
            executed = core.api.dispatch(
                execution_command(inputs.project_id, action, parameters), RELAY
            )
            assert executed.error is None, executed.error
            assert executed.result["attempt"]["exit_fact"]["real_exit_code"] == 0
            assert business_file.exists() is (not pure)
            facts = ExecutionFacts.model_validate(executed.result["execution_facts"])
            query_command = command(facts)
            verified = core.api.dispatch(query_command, RELAY)
            assert verified.error is None, verified.error
            assert verified.result["verification"]["observation"] == "matched"
            assert len(calls) == (0 if pure else 1)
            if dynamic:
                identity = json.loads(business_file.read_text(encoding="utf-8"))["object_id"]
                assert verified.result["verification"]["business_object_id"] == identity
                assert calls[0].startswith(f"GET /orders/{identity} ".encode())
            if pure:
                for index, step in enumerate(initial.steps[1:], 1):
                    parameters = service.prepare(
                        project_id=inputs.project_id, run_id=initial.run_id,
                        step_id=step.step_id, intent_id=f"pure-step-{index}",
                        request_id=f"pure-prepare-{index}",
                    )
                    _, action = service.resolver.read(
                        inputs.project_id, parameters["execution_action_id"],
                    )
                    actor, challenge = review(service, inputs.project_id, action, parameters)
                    assert actor.interaction is not None
                    actor = replace(actor, interaction=replace(
                        actor.interaction, interaction_id=f"pure-gesture-{index}",
                    ))
                    save(service, inputs.project_id, action, parameters, actor, challenge,
                         request=f"pure-authorization-request-{index}")
                    executed = core.api.dispatch(execution_command(
                        inputs.project_id, action, parameters, request=f"pure-execute-{index}",
                    ), RELAY)
                    assert executed.error is None, executed.error
                    facts = ExecutionFacts.model_validate(executed.result["execution_facts"])
                    query_command = Command(
                        action="verify_pending", project_id=inputs.project_id,
                        request_id=f"pure-query-request-{index}", intent_id=f"pure-query-{index}",
                        expected_revision=0, target=step.step_id,
                        parameters={"run_id": facts.run_id, "step_id": step.step_id,
                                    "attempt_id": facts.current_attempt_by_step[step.step_id],
                                    "base_snapshot_commit_id": facts.snapshot_commit_id},
                    )
                    verified = core.api.dispatch(query_command, RELAY)
                    assert verified.error is None, verified.error
                    assert verified.result["verification"]["observation"] == "matched"
            report_command = Command(
                action="create_report", project_id=inputs.project_id,
                request_id="report-request", intent_id="report-intent", expected_revision=0,
                parameters={"report_id": "order-report", "run_id": facts.run_id, "issue_ids": []},
            )
            reported = core.api.dispatch(report_command, RELAY)
            assert reported.error is None, reported.error
            report = core.unit_of_work.repo.read(
                aggregate_kind="report", record_id="order-report", revision=1,
            ).payload
            assert report["business_outcome"] == ("passed" if complete_library else "incomplete")
            assert report["evidence_refs"]
            if pure:
                from tests.unit.test_delivery_report_verification import submit_report_delivery

                assert len(facts.current_attempt_by_step) == 4
                assert all(item.state.value == "verified" and not item.gap_ids
                           for item in facts.source_verifications), facts.source_verifications
                assert len(facts.source_check_results) == 4
                submit_report_delivery(core, inputs, mapped=True, complete=complete_library)
            exported = core.api.dispatch(Command(
                action="export_report", project_id=inputs.project_id, request_id="export-request",
                intent_id="export-intent", expected_revision=0,
                parameters={"report_id": "order-report", "kind": "evidence_bundle",
                            "review_ids": []},
            ), RELAY)
            assert exported.error is None, exported.error
            export_record = core.unit_of_work.repo.read(
                aggregate_kind="report_export", record_id=exported.result["record_id"], revision=1,
            ).payload
            from aitest.domain.evidence.evidence import StoredObjectRef
            from aitest.infrastructure.file_store.objects import FileObjectStore

            artifact_ref = StoredObjectRef(**export_record["object_ref"])
            artifact = FileObjectStore(core.workspace.root).read_bytes(artifact_ref)
            assert artifact.startswith(b"PK")
            root = core.workspace.root
        reopened = assemble_workspace_core(root, instance_id="workflow-reopened")
        try:
            replay = reopened.api.dispatch(
                query_command.model_copy(update={"request_id": "query-replay"}), RELAY
            )
            assert replay.error is None and replay.result == verified.result
            assert len(calls) == (0 if pure else 1)
            saved_report = reopened.unit_of_work.repo.read(
                aggregate_kind="report", record_id="order-report", revision=1,
            ).payload
            assert saved_report == report
            assert FileObjectStore(root).read_bytes(artifact_ref) == artifact
        finally:
            reopened.lifetime_lock.release()
