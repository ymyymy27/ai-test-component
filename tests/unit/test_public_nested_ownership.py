"""Public B writers must reject a conflicting declared owner before any write."""

import pytest

from aitest.application.planning.serialization import acceptance_scope_to_payload, case_to_payload
from aitest.bootstrap import assemble_workspace_core
from tests.support.prepared_run_factory import build_scenario
from tests.unit.test_default_source_analysis import dispatch


@pytest.mark.parametrize(
    "action",
    ["save_case", "save_acceptance", "save_environment", "publish_plan_case", "publish_plan_scope"],
)
def test_conflicting_nested_project_cannot_be_relabelled_as_command_project(tmp_path, action):
    core = assemble_workspace_core(tmp_path, instance_id="nested-owner-core")
    scenario = build_scenario("plain")
    case = case_to_payload(scenario.cases[0], project_id="another-project")
    scope = acceptance_scope_to_payload(scenario.plan.scope, project_id="another-project")
    if action == "save_case":
        parameters = {"case": case}
    elif action == "save_acceptance":
        parameters = {"acceptance_scope": scope}
    elif action == "save_environment":
        parameters = {
            "environment": {
                "project_id": "another-project",
                "environment_id": "env",
                "revision": 1,
                "interpreter_requirement": "Python 3.13",
                "dependency_declaration": "pyproject.toml",
                "isolation_mode": "venv",
            }
        }
    else:
        cases = [case_to_payload(c, project_id=scenario.project.project_id) for c in scenario.cases]
        scope = acceptance_scope_to_payload(
            scenario.plan.scope, project_id=scenario.project.project_id
        )
        if action == "publish_plan_case":
            cases[0]["project_id"] = "another-project"
        else:
            scope["project_id"] = "another-project"
        parameters = {"plan_id": "plan", "revision": 1, "scope": scope, "cases": cases}
        action = "publish_plan"
    seq = core.unit_of_work.current_commit_sequence()
    try:
        response = dispatch(
            core, action, project=scenario.project.project_id, parameters=parameters
        )
        assert response.error is not None
        assert response.error.code == "B_INVALID_PARAMETER"
        assert core.unit_of_work.current_commit_sequence() == seq
    finally:
        core.lifetime_lock.release()
