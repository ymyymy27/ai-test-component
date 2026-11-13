from __future__ import annotations

from uuid import uuid4

from aitest.application.project.onboarding import OnboardingForm, wizard_commands
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.interfaces.local.native_confirmation import dispatch_native
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_default_start_source_chain import _chain_environment
from tests.unit.test_default_step_execution import execution_command
from tests.unit.test_execution_authorization_origin import review, save


def _selection() -> dict[str, object]:
    return {
        "schema_version": "aitest.test-selection/1.0",
        "goal": "验证订单",
        "selections": [{
            "acceptance_item_ids": ["AC-ORDER-01"],
            "scope": {"kind": "dir", "value": "tests"},
            "expected": "all_pass",
            "mock_scope": ["unknown"],
            "rationale": "运行订单测试",
        }],
    }


def _dispatch(core, command: Command):
    if command.action == "discover_tests":
        return core.api.dispatch(command, RELAY)
    return dispatch_native(
        core.api, command, prompt=lambda text: True, records=core.unit_of_work.repo,
    )


def test_pytest_scope_plan_execute_verify_real_core(tmp_path):
    source = tmp_path / "source"
    tests = source / "tests"
    tests.mkdir(parents=True)
    (tests / "test_order.py").write_text(
        "def test_create():\n    assert 1 == 1\n", encoding="utf-8"
    )
    core = assemble_workspace_core(
        tmp_path / "workspace",
        instance_id="pytest-chain-fixture",
        environment_resolver=_chain_environment(),
    )
    form = OnboardingForm(
        project_id="guided-" + str(uuid4()),
        workspace_id=core.workspace.workspace_id,
        source=str(source),
        entry="tests",
        name="订单检查",
        goal="验证订单",
        data_policy="仅本地测试",
        dependency_declaration="仅标准库",
        test_mode=True,
        run_tier="on_demand",
    )
    receipts: dict[str, object] = {}
    try:
        for stage in ("project", "discover"):
            for item in wizard_commands(form, stage, receipts):
                command = Command.model_validate(
                    {key: value for key, value in item.items() if key != "key"}
                    | {"request_id": uuid4().hex, "project_id": form.project_id}
                )
                result = _dispatch(core, command)
                assert result.error is None, (item["action"], result.error)
                receipts[item["key"]] = result.result
        selection_form = form.model_copy(update={
            "proposal": _selection(),
            "test_inventory": receipts["discover"],
        })
        for stage in ("commit", "prepare", "register"):
            for item in wizard_commands(selection_form, stage, receipts):
                command = Command.model_validate(
                    {key: value for key, value in item.items() if key != "key"}
                    | {"request_id": uuid4().hex, "project_id": form.project_id}
                )
                result = _dispatch(core, command)
                assert result.error is None, (item["action"], result.error)
                receipts[item["key"]] = result.result
        run_id = receipts["register"]["run_id"]
        view = core.api.dispatch(Command(
            action="read_view", project_id=form.project_id, request_id=uuid4().hex,
            parameters={"view": "run", "run_id": run_id},
        ), RELAY)
        assert view.error is None, view.error
        steps = view.result["view"]["steps"]
        assert len(steps) == 1
        step_id = steps[0]["step_id"]
        service = core.execution_authorizations
        parameters = service.prepare(
            project_id=form.project_id, run_id=run_id, step_id=step_id,
            intent_id="pytest-execute", request_id="pytest-resolve",
        )
        _, action = service.resolver.read(form.project_id, parameters["execution_action_id"])
        actor, challenge = review(service, form.project_id, action, parameters)
        save(service, form.project_id, action, parameters, actor, challenge)
        executed = core.api.dispatch(
            execution_command(form.project_id, action, parameters), RELAY
        )
        assert executed.error is None, executed.error
        facts = ExecutionFacts.model_validate(executed.result["execution_facts"])
        verified = core.api.dispatch(Command(
            action="verify_pending", project_id=form.project_id,
            request_id=uuid4().hex, intent_id="pytest-verify", expected_revision=0,
            target=step_id,
            parameters={
                "run_id": facts.run_id,
                "step_id": step_id,
                "attempt_id": facts.current_attempt_by_step[step_id],
                "base_snapshot_commit_id": facts.snapshot_commit_id,
            },
        ), RELAY)
        assert verified.error is None, verified.error
        assert verified.result["verification"]["observation"] == "matched", verified.result
        attempt_id = facts.current_attempt_by_step[step_id]
        sources = [
            item
            for item in verified.result["execution_facts"]["source_verifications"]
            if item["verification_id"] == "source-verification:" + attempt_id
        ]
        assert len(sources) == 1, sources
        # Module-mode pytest scope is a directory; its observed files must make
        # the actual entry count as observed instead of "entry not observed".
        gaps = sources[0]["gap_ids"]
        assert "source_entry_not_observed" not in gaps, gaps
        assert "actual_source_entry_not_observed" not in gaps, gaps
    finally:
        core.lifetime_lock.release()
