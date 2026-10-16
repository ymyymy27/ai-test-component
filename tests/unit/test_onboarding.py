"""Beginner input translation retains ordinary authorization and frozen input boundaries."""
from uuid import uuid4

import pytest

from aitest.application.planning.serialization import case_from_payload
from aitest.application.project.onboarding import OnboardingForm, wizard_commands
from aitest.contracts.commands import Command
from aitest.infrastructure.model_configuration import validate_model_configuration
from aitest.interfaces.tools.onboarding import setup


def form(source="E:/sample"):
    return OnboardingForm(project_id="guided-"+str(uuid4()),workspace_id="workspace",
        source=source,entry="main.py",name="算术检查",goal="检查正常、边界与异常结果",
        data_policy="仅纯函数，无持久业务数据",dependency_declaration="仅标准库",
        checks=[{"objective":name,"inputs":"已声明的独立输入","expected":{"normal":5}}
                for name in ("加载","正常","边界","异常")])


def test_wizard_commands_are_ordinary_commands_without_authority():
    selection=form()
    project=wizard_commands(selection,"project",{})
    assert [item["action"] for item in project]==["save_context","save_binding","save_environment"]
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
