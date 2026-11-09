"""Beginner input translation retains ordinary authorization and frozen input boundaries."""
from uuid import uuid4

import pytest

from aitest.application.planning.serialization import case_from_payload
from aitest.application.project.onboarding import (
    OnboardingForm,
    wizard_commands,
    wizard_review,
)
from aitest.contracts.commands import Command
from aitest.infrastructure.model_configuration import validate_model_configuration
from aitest.interfaces.tools.onboarding import setup


def _proposal_case(item, layer, case_id):
    return {
        "case_id": case_id,
        "template_item_id": item,
        "layer": layer,
        "objective": f"验证 {item}",
        "preconditions": ["已登记入口"],
        "inputs": ["输入"],
        "steps": ["运行入口"],
        "expected": "与独立预期一致",
        "verification_method": "独立比对",
        "links": {
            "acceptance_item_ids": [item],
            "module_ids": [],
            "critical_path_ids": [],
            "no_critical_path_reason": "纯函数无跨系统链路",
        },
        "assertion_basis_text": f"{item} 的断言依据",
        "independent_verification": "比较保存输出与冻结预期",
        "mock_scope": [],
        "importance": "P1",
    }


def _proposal():
    return {
        "schema_version": "aitest.plan-proposal/1.0",
        "template_id": "python-library",
        "template_version": "1.0.0",
        "scope": {
            "name": "算术检查",
            "objective": "检查正常、边界与异常结果",
            "required_case_ids": [],
            "excluded_case_ids": [],
            "exclusion_reasons": [],
            "dependency_closure_ids": [],
            "applicability_exclusions": [],
        },
        "cases": [
            _proposal_case("syntax", "L1", "case-syntax"),
            _proposal_case("normal", "L2", "case-normal"),
            _proposal_case("boundary", "L2", "case-boundary"),
            _proposal_case("error", "L2", "case-error"),
        ],
    }


def form(source="E:/sample"):
    return OnboardingForm(project_id="guided-"+str(uuid4()),workspace_id="workspace",
        source=source,entry="main.py",name="算术检查",goal="检查正常、边界与异常结果",
        data_policy="仅纯函数，无持久业务数据",dependency_declaration="仅标准库",
        checks=[{"objective":name,"inputs":"已声明的独立输入","expected":{"normal":5}}
                for name in ("加载","正常","边界","异常")])


def test_wizard_commands_are_ordinary_commands_without_authority():
    selection=form()
    project=wizard_commands(selection,"project",{})
    assert [item["action"] for item in project]==[
        "save_context","save_binding","save_environment","analyze_project"
    ]
    plan=wizard_commands(selection,"plan",{})
    assert len([item for item in plan if item["action"]=="confirm_basis"])==4
    for item in project+plan:
        Command.model_validate({key:value for key,value in item.items() if key!="key"}|
            {"request_id":"request-"+str(uuid4()),"project_id":selection.project_id})
        assert "user_confirmed" not in item["parameters"]
        assert "approval_challenge_id" not in item["parameters"]
    case=case_from_payload(plan[0]["parameters"]["case"])
    assert case.assertion_basis.state.value=="present_unconfirmed"
    assert wizard_commands(selection,"plan",{})==plan
    changed=selection.model_copy(update={"goal":"另一个目标"})
    assert wizard_commands(changed,"plan",{})[-2]["intent_id"]!=plan[-2]["intent_id"]


@pytest.mark.parametrize("entry",["../main.py","C:/main.py","/main.py","main.sh"])
def test_wizard_refuses_entry_outside_declared_python_scope(entry):
    with pytest.raises(ValueError):
        wizard_commands(form().model_copy(update={"entry":entry}),"project",{})


def test_wizard_never_invents_snapshot_or_confirmation_receipts():
    with pytest.raises(KeyError):
        wizard_commands(form(),"prepare",{})
    with pytest.raises(ValueError):
        wizard_commands(form().model_copy(update={"checks":[]}),"plan",{})


def test_model_public_config_is_validated_before_saving():
    assert validate_model_configuration({"schema_version":"aitest.model-provider/1",
        "endpoint":"https://api.deepseek.com","credential_reference":"safe-ref"})[0].startswith("https:")
    for endpoint in ("http://example.com","https://user:password@example.com","https://example.com?key=secret"):
        with pytest.raises(ValueError):
            validate_model_configuration({"schema_version":"aitest.model-provider/1",
                "endpoint":endpoint,"credential_reference":"safe-ref"})


def test_setup_rejects_source_overlap_before_creating_any_environment(tmp_path):
    source=tmp_path/"source"
    source.mkdir()
    project="guided-"+str(uuid4())
    with pytest.raises(ValueError):
        setup({"project_id":project,"source":str(source),"root":str(source/project)})
    assert not (source/project).exists()


def test_setup_creates_workspace_and_reuses_registered_environment(tmp_path, monkeypatch):
    import aitest.interfaces.tools.onboarding as module

    project = "guided-" + str(uuid4())
    source = tmp_path / "source"
    source.mkdir()
    root = tmp_path / project
    created = []

    def create(_self, path):
        created.append(path)
        path.mkdir(parents=True)

    monkeypatch.setattr(module.venv.EnvBuilder, "create", create)
    monkeypatch.setattr(module, "carrier_configuration", lambda **kwargs: {"carriers": []})
    data = {"project_id": project, "source": str(source), "root": str(root)}
    first = setup(data)
    assert (root / "workspace").is_dir()
    assert setup(data) == first
    assert created == [root / "python"]


def test_wizard_project_and_plan_use_real_core_contracts(tmp_path):
    from aitest.bootstrap import assemble_workspace_core
    from aitest.interfaces.local.native_confirmation import dispatch_native
    from tests.support.environment_resolution import FixtureEnvironmentResolver

    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text('print("{}")', encoding="utf-8")
    core = assemble_workspace_core(tmp_path / "workspace", instance_id="wizard-fixture",
                                   environment_resolver=FixtureEnvironmentResolver())
    selection = form(str(source)).model_copy(update={"workspace_id": core.workspace.workspace_id})
    receipts = {}
    try:
        for stage in ("project", "plan", "prepare", "register"):
            for item in wizard_commands(selection, stage, receipts):
                command = Command.model_validate(
                    {key: value for key, value in item.items() if key != "key"}
                    | {"request_id": uuid4().hex, "project_id": selection.project_id}
                )
                result = dispatch_native(core.api, command, prompt=lambda text: True,
                                         records=core.unit_of_work.repo)
                assert result.error is None, (item["action"], result.error)
                assert not result.result.get("blocked"), (item["action"], result.result)
                receipts[item["key"]] = result.result
        assert receipts["snapshot"]["snapshot_id"]
        assert receipts["register"]["run_id"]
        prepare = wizard_commands(selection, "prepare", receipts)
        assert prepare[0]["parameters"]["snapshot_id"] == receipts["snapshot"]["snapshot_id"]
    finally:
        core.lifetime_lock.release()


def test_v11_proposal_uses_one_plan_confirmation_and_publish_basis_receipts():
    payload = _proposal()
    payload["schema_version"] = "aitest.plan-proposal/1.1"
    payload["understanding_revision"] = 1
    payload["goal_intent_ids"] = ["intent-normal"]
    payload["coverage_claims"] = []
    selection = form().model_copy(update={"proposal": payload, "checks": []})
    commit = wizard_commands(selection, "commit", {})
    actions = [item["action"] for item in commit]
    assert "confirm_basis" not in actions
    publish = next(item for item in commit if item["action"] == "publish_plan")
    assert publish["parameters"]["plan_grant"]["understanding_revision"] == 1
    assert publish["parameters"]["plan_grant"]["environment_id"] == (
        "env-" + selection.project_id
    )

    basis = [
        {
            "confirmation_id": f"confirmation-{index}",
            "case_id": case["case_id"],
            "basis_revision": 1,
            "confirmed_at_commit": "7",
        }
        for index, case in enumerate(publish["parameters"]["cases"])
    ]
    prepare = wizard_commands(
        selection,
        "prepare",
        {
            "snapshot": {"snapshot_id": "snapshot-1"},
            "publish": {"basis_confirmations": basis},
        },
    )
    prepared = next(item for item in prepare if item["action"] == "prepare_run")
    assert prepared["parameters"]["confirmation_refs"] == basis


def test_wizard_review_uses_saved_understanding_for_coverage_and_contract():
    from aitest.application.project.understanding import (
        scan_sources,
        understanding_identity,
        understanding_to_payload,
    )
    from tests.unit.test_plan_proposal import _v11_payload

    selection = form()
    understanding = scan_sources(
        files={"app/lib.py": b"def create_order(amount):\n    return amount\n"},
        source_snapshot_id="snap-review",
        understanding_id=understanding_identity(selection.project_id, "project"),
        revision=2,
        scope="project",
    )
    payload = _v11_payload(understanding)
    selection = selection.model_copy(
        update={
            "proposal": payload,
            "checks": [],
            "understanding": understanding_to_payload(
                understanding,
                project_id=selection.project_id,
                workspace_id=selection.workspace_id,
            ),
        }
    )
    preview = wizard_review(selection)
    assert preview["coverage_preview"]
    assert preview["coverage_contract"]["understanding_revision"] == 2
    assert preview["understanding_ref"]["revision"] == 2


def test_wizard_review_returns_preview_and_commit_uses_proposal_cases():
    selection = form().model_copy(update={"proposal": _proposal(), "checks": []})
    preview = wizard_review(selection)
    assert preview["coverage"] == {
        "template_items": 4,
        "covered_items": 4,
        "required_items": 4,
    }
    assert [case["case_id"] for case in preview["cases"]] == [
        "case-boundary",
        "case-error",
        "case-normal",
        "case-syntax",
    ]
    commit = wizard_commands(selection, "commit", {})
    actions = [item["action"] for item in commit]
    assert actions.count("save_case") == 4
    assert actions.count("confirm_basis") == 4
    assert "publish_plan" in actions
    publish = next(item for item in commit if item["action"] == "publish_plan")
    assert publish["parameters"]["run_tier"] == "full"
    assert publish["parameters"]["initial_driver"] == "planned"
    assert len(publish["parameters"]["cases"]) == 4


def test_wizard_review_rejects_missing_template_item_without_writing():
    payload = _proposal()
    payload["cases"] = payload["cases"][:-1]
    selection = form().model_copy(update={"proposal": payload, "checks": []})
    with pytest.raises(Exception) as error:
        wizard_review(selection)
    assert "error" in str(error.value) or "template" in str(error.value)


def test_wizard_proposal_commit_prepare_register_use_real_core(tmp_path):
    from aitest.bootstrap import assemble_workspace_core
    from aitest.interfaces.local.native_confirmation import dispatch_native
    from tests.support.environment_resolution import FixtureEnvironmentResolver

    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text('print("{}")', encoding="utf-8")
    core = assemble_workspace_core(
        tmp_path / "workspace",
        instance_id="wizard-proposal-fixture",
        environment_resolver=FixtureEnvironmentResolver(),
    )
    selection = form(str(source)).model_copy(
        update={
            "workspace_id": core.workspace.workspace_id,
            "proposal": _proposal(),
            "checks": [],
        }
    )
    receipts = {}
    try:
        for stage in ("project", "commit", "prepare", "register"):
            for item in wizard_commands(selection, stage, receipts):
                command = Command.model_validate(
                    {key: value for key, value in item.items() if key != "key"}
                    | {"request_id": uuid4().hex, "project_id": selection.project_id}
                )
                result = dispatch_native(
                    core.api, command, prompt=lambda text: True,
                    records=core.unit_of_work.repo,
                )
                assert result.error is None, (item["action"], result.error)
                assert not result.result.get("blocked"), (item["action"], result.result)
                receipts[item["key"]] = result.result
        assert len([key for key in receipts if key.startswith("confirm-")]) == 4
        assert receipts["register"]["run_id"]
        assert receipts["prepare"]["run_tier"] == "full"
        assert receipts["prepare"]["initial_driver"] == "planned"
    finally:
        core.lifetime_lock.release()


def test_v11_project_understanding_drives_one_confirmation_end_to_end(tmp_path):
    from aitest.application.project.understanding import understanding_from_payload
    from aitest.bootstrap import assemble_workspace_core
    from aitest.interfaces.local.native_confirmation import dispatch_native
    from tests.support.environment_resolution import FixtureEnvironmentResolver

    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def divide(a, b):\n    return a / b\n",
        encoding="utf-8",
    )
    core = assemble_workspace_core(
        tmp_path / "workspace",
        instance_id="wizard-v11-fixture",
        environment_resolver=FixtureEnvironmentResolver(),
    )
    selection = form(str(source)).model_copy(
        update={"workspace_id": core.workspace.workspace_id}
    )
    receipts: dict[str, object] = {}
    try:
        for item in wizard_commands(selection, "project", {}):
            command = Command.model_validate(
                {key: value for key, value in item.items() if key != "key"}
                | {"request_id": uuid4().hex, "project_id": selection.project_id}
            )
            result = dispatch_native(
                core.api, command, prompt=lambda text: True,
                records=core.unit_of_work.repo,
            )
            assert result.error is None, (item["action"], result.error)
            receipts[item["key"]] = result.result

        understanding_payload = receipts["snapshot"]["understanding"]
        understanding = understanding_from_payload(
            understanding_payload,
            project_id=selection.project_id,
            workspace_id=selection.workspace_id,
        )
        payload = _proposal()
        payload["schema_version"] = "aitest.plan-proposal/1.1"
        payload["understanding_revision"] = understanding.revision
        payload["goal_intent_ids"] = [
            intent.intent_id for intent in understanding.test_intents
        ]
        payload["coverage_claims"] = [
            {
                "intent_id": intent.intent_id,
                "function_id": function_id,
                "dimension": dimension,
                "case_ids": ["case-normal"],
            }
            for intent in understanding.test_intents
            for function_id in sorted(intent.function_ids)
            for dimension in next(
                function.required_dimensions
                for function in understanding.catalog.functions
                if function.function_id == function_id
            )
        ]
        selection = selection.model_copy(
            update={"proposal": payload, "understanding": understanding_payload}
        )
        preview = wizard_review(selection)
        selection = selection.model_copy(
            update={
                "coverage_contract": preview["coverage_contract"],
                "understanding_ref": preview["understanding_ref"],
            }
        )
        for stage in ("commit", "prepare", "register"):
            for item in wizard_commands(selection, stage, receipts):
                command = Command.model_validate(
                    {key: value for key, value in item.items() if key != "key"}
                    | {"request_id": uuid4().hex, "project_id": selection.project_id}
                )
                result = dispatch_native(
                    core.api, command, prompt=lambda text: True,
                    records=core.unit_of_work.repo,
                )
                assert result.error is None, (item["action"], result.error.message)
                assert not result.result.get("blocked"), (item["action"], result.result)
                if item["action"] == "prepare_run":
                    assert result.result["status"] == "prepared", result.result.get(
                        "blocking_reasons"
                    )
                receipts[item["key"]] = result.result

        assert not [key for key in receipts if key.startswith("confirm-")]
        assert receipts["publish"]["grant_id"]
        assert len(receipts["publish"]["basis_confirmations"]) == 4
        plan = core.unit_of_work.repo.read(
            aggregate_kind="plan", record_id="plan-" + selection.project_id, revision=1
        ).payload
        assert plan["coverage_contract"]["understanding_revision"] == understanding.revision
        assert receipts["register"]["run_id"]
    finally:
        core.lifetime_lock.release()
