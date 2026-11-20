import os
import sys
from pathlib import Path

import pytest

from aitest.core.project_context import (
    _symbol_map,
    _test_commands,
    _test_dirs,
    _test_modules,
    package_name,
)
from aitest.core.project_context import (
    test_dependency_specs as declared_test_dependency_specs,
)
from aitest.errors import AiTestError
from aitest.models import Case, Plan, Run, RunCase, Scope
from aitest.runtime_commands import (
    _drop_unavailable_django_scripts,
    _merge_duplicate_command_cases,
    _validate_plan_cases,
)
from aitest.worker import (
    _case_dependencies_ready,
    _dependency_suggestion,
    _downgrade_script_failures,
    _environment_failure,
    _environment_fingerprint,
    _missing_command_tool,
    _missing_dependencies,
    _missing_module_from_output,
    _resolve_dependency_package,
    _run_env,
    _script_self_error,
)


def test_testing_directory_is_recognized(tmp_path: Path) -> None:
    (tmp_path / "testing").mkdir()
    assert "testing/" in _test_dirs(tmp_path)


def test_nested_test_directories_are_recognized(tmp_path: Path) -> None:
    nested = tmp_path / "pkg" / "admin" / "tests"
    nested.mkdir(parents=True)
    (nested / "test_views.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    vendored = tmp_path / "node_modules" / "pkg" / "tests"
    vendored.mkdir(parents=True)
    (vendored / "test_ignored.py").write_text(
        "def test_no():\n    assert False\n", encoding="utf-8"
    )

    dirs = _test_dirs(tmp_path)
    assert "pkg/admin/tests/" in dirs
    assert all("node_modules" not in item for item in dirs)
    assert _test_modules(tmp_path, dirs) == ["pkg.admin.tests.test_views"]


def test_runtests_entry_is_a_test_command(tmp_path: Path) -> None:
    (tmp_path / "runtests.py").write_text("print('runner')\n", encoding="utf-8")
    commands = _test_commands({}, {}, {}, ["django"], [], tmp_path)
    assert commands[0] == "python runtests.py"


def test_manage_py_is_a_django_test_command(tmp_path: Path) -> None:
    (tmp_path / "manage.py").write_text("print('manage')\n", encoding="utf-8")
    commands = _test_commands({}, {}, {}, ["django"], ["app/tests/"], tmp_path)
    assert "python manage.py test" in commands


def test_repeated_whole_suite_command_is_rejected(tmp_path: Path) -> None:
    cases = [
        Case(
            case_id=f"case_{index}",
            acceptance_item_id=f"item_{index}",
            layer="L2",
            expected="既有测试通过",
            verify_method="command:python runtests.py",
        )
        for index in (1, 2)
    ]
    with pytest.raises(AiTestError) as excinfo:
        _validate_plan_cases(cases, "business", {"root": str(tmp_path)})
    assert "业务用例不能使用整包测试命令" in str(excinfo.value)


def test_whole_suite_is_allowed_only_as_l1_smoke(tmp_path: Path) -> None:
    smoke_case = Case(
        case_id="case_smoke",
        acceptance_item_id="item_1",
        layer="L1",
        expected="整包测试通过",
        verify_method="command:python runtests.py",
    )
    _validate_plan_cases([smoke_case], "business", {"root": str(tmp_path)})


def test_duplicate_scoped_test_command_is_allowed(tmp_path: Path) -> None:
    cases = [
        Case(
            case_id=f"case_{index}",
            acceptance_item_id=f"item_{index}",
            layer="L2",
            expected="页面动作测试通过",
            verify_method="command:python runtests.py wagtail.tests",
        )
        for index in (1, 2)
    ]
    _validate_plan_cases(cases, "business", {"root": str(tmp_path)})


def test_django_testcase_script_is_rejected_without_pytest_django(tmp_path: Path) -> None:
    case = Case(
        case_id="case_django",
        acceptance_item_id="item_1",
        layer="L2",
        expected="页面创建动作通过",
        verify_method="script:test_page.py",
        script_name="test_page.py",
        script_content=(
            "import os\nimport django\n"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.settings')\n"
            "django.setup()\nfrom django.test import TestCase\n"
        ),
    )
    with pytest.raises(AiTestError) as excinfo:
        _validate_plan_cases(
            [case], "business", {"root": str(tmp_path), "framework": ["django"]}
        )
    assert "Django TestCase" in str(excinfo.value)


def test_django_scripts_are_dropped_and_bound_to_command(tmp_path: Path) -> None:
    command = Case(
        case_id="case_cmd",
        acceptance_item_id="item_1",
        layer="L2",
        expected="页面动作通过",
        verify_method="command:python runtests.py wagtail.admin.tests.pages",
    )
    script = Case(
        case_id="case_script",
        acceptance_item_id="item_2",
        layer="L2",
        expected="页面动作通过",
        verify_method="script:test_page.py",
        script_name="test_page.py",
        script_content="from django.test import TestCase\n",
    )
    kept, dropped = _drop_unavailable_django_scripts(
        [command, script], {"framework": ["django"]}
    )
    assert dropped == ["item_2"]
    assert [case.case_id for case in kept] == ["case_cmd"]
    assert "item_2" in kept[0].acceptance_item_ids


def test_django_import_command_without_settings_is_rejected(tmp_path: Path) -> None:
    case = Case(
        case_id="case_import",
        acceptance_item_id="item_1",
        layer="L2",
        expected="动作类可导入",
        verify_method='command:python -c "from app.actions import Create"',
    )
    with pytest.raises(AiTestError) as excinfo:
        _validate_plan_cases(
            [case], "business", {"root": str(tmp_path), "framework": ["django"]}
        )
    assert "DJANGO_SETTINGS_MODULE" in str(excinfo.value)


def test_missing_third_party_test_dependency_is_environment_not_failure(
    tmp_path: Path,
) -> None:
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "__init__.py").write_text("", encoding="utf-8")
    output = (
        b"Traceback (most recent call last):\n"
        b"ModuleNotFoundError: No module named 'factory'\n"
    )
    reason = _environment_failure(output, tmp_path)
    assert "环境缺少依赖：factory" in reason


def test_missing_project_module_stays_a_product_failure(tmp_path: Path) -> None:
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "__init__.py").write_text("", encoding="utf-8")
    output = b"ModuleNotFoundError: No module named 'shop.missing'\n"
    assert _environment_failure(output, tmp_path) == ""


def test_mixed_assertion_and_dependency_errors_stay_failures(tmp_path: Path) -> None:
    output = (
        b"ModuleNotFoundError: No module named 'factory'\n"
        b"FAIL: test_total (shop.tests.test_cart)\n"
    )
    assert _environment_failure(output, tmp_path) == ""


def test_missing_command_and_service_are_environment_failures(tmp_path: Path) -> None:
    assert "环境缺少命令" in _environment_failure(
        b"'pytest' is not recognized as an internal or external command", tmp_path
    )
    assert "环境服务未就绪" in _environment_failure(
        b"OperationalError: could not connect to server: Connection refused", tmp_path
    )


def test_duplicate_command_cases_merge_and_keep_items() -> None:
    cases = [
        Case(
            case_id="case_1",
            acceptance_item_id="item_1",
            layer="L2",
            expected="通过",
            verify_method="command:python runtests.py wagtail.tests",
        ),
        Case(
            case_id="case_2",
            acceptance_item_id="item_2",
            layer="L2",
            expected="通过",
            verify_method="command:python  runtests.py   wagtail.tests",
        ),
    ]
    merged, removed = _merge_duplicate_command_cases(cases)
    assert removed == 1
    assert len(merged) == 1
    assert merged[0].acceptance_item_id == "item_1"
    assert merged[0].acceptance_item_ids == ["item_2"]


def test_missing_leading_tool_is_detected() -> None:
    assert _missing_command_tool("command:npm test", {"PATH": ""}) == "npm"
    assert _missing_command_tool("command:echo ok", {"PATH": ""}) == ""
    assert _missing_command_tool("script:test.py", {"PATH": ""}) == ""


def test_environment_fingerprint_records_interpreter(tmp_path: Path) -> None:
    info = _environment_fingerprint(Path(sys.executable), tmp_path)
    assert info["python"] == sys.executable
    assert info["python_version"]
    assert info["cwd"] == str(tmp_path)


def test_missing_module_maps_to_known_package() -> None:
    output = b"ModuleNotFoundError: No module named 'factory.helpers'"
    assert _missing_module_from_output(output) == "factory"
    assert (
        _resolve_dependency_package(None, "proj", "factory", "command:runtests.py")
        == "factory-boy"
    )


def test_declared_test_dependency_is_preferred(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "sample"\n'
        "optional-dependencies = { testing = [\"factory-boy>=3.2\", \"pytest>=8\"] }\n",
        encoding="utf-8",
    )
    specs = declared_test_dependency_specs(tmp_path)
    assert "factory-boy>=3.2" in specs
    assert "pytest>=8" in specs
    assert package_name("factory-boy>=3.2") == "factory-boy"
    suggestion = _dependency_suggestion(
        None, "proj", tmp_path, "factory", "command:runtests.py"
    )
    assert suggestion["package"] == "factory-boy>=3.2"
    assert suggestion["source"] == "declared"


def test_script_self_error_only_for_generated_signature_errors() -> None:
    traceback = (
        b"..\\runs\\run_1\\generated\\test_x.py:7: TypeError: "
        b"__init__() missing 1 required positional argument: 'path'"
    )
    assert _script_self_error(traceback) is True
    assert _script_self_error(b"E AssertionError: assert 'value1' == 'value1 # c'") is False


def test_run_env_uses_host_interpreter_when_project_has_no_venv(tmp_path: Path) -> None:
    env = _run_env(tmp_path, None, "run_env_host")
    assert env["PATH"].split(os.pathsep)[0] == str(Path(sys.executable).parent)


def test_missing_dependencies_skips_installed_packages() -> None:
    assert _missing_dependencies(Path(sys.executable), ["pytest"], None) == []
    missing = _missing_dependencies(
        Path(sys.executable), ["definitely_not_installed_pkg"], None
    )
    assert missing == ["definitely_not_installed_pkg"]
    assert _missing_dependencies(Path(sys.executable), ["pytest>=1"], None) == []
    assert _missing_dependencies(Path(sys.executable), ["pytest>=999"], None) == [
        "pytest>=999"
    ]


def test_case_dependency_gate_is_per_case() -> None:
    cache: dict[tuple[str, ...], list[str] | None] = {}
    ready, missing = _case_dependencies_ready(
        Path(sys.executable), ["pytest"], {}, cache
    )
    assert ready is True and missing == []
    ready, missing = _case_dependencies_ready(
        Path(sys.executable), ["definitely_not_installed_pkg"], {}, cache
    )
    assert ready is False and missing == ["definitely_not_installed_pkg"]


def test_symbol_map_points_to_defining_module(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("from .core import exported\n", encoding="utf-8")
    (package / "core.py").write_text(
        "class Thing:\n    def __init__(self, path, data=None):\n        pass\n\n"
        "    def run(self):\n        pass\n\n"
        "def exported():\n    pass\n\ndef hidden():\n    pass\n",
        encoding="utf-8",
    )
    symbols = _symbol_map(tmp_path)
    assert symbols["pkg.core"]["exported"] == "()"
    assert symbols["pkg.core"]["hidden"] == "()"
    assert "Thing.__init__" in symbols["pkg.core"]
    assert "path" in symbols["pkg.core"]["Thing.__init__"]
    assert "Thing.run" in symbols["pkg.core"]
    assert "pkg" not in symbols or "exported" not in symbols["pkg"]


def test_script_failure_is_downgraded_only_when_command_covers_item() -> None:
    cases = [
        Case(
            case_id="cmd",
            acceptance_item_id="item1",
            layer="L1",
            expected="项目测试通过",
            verify_method="command:python -m pytest",
        ),
        Case(
            case_id="script_covered",
            acceptance_item_id="item1",
            layer="L2",
            expected="行为正确",
            verify_method="script:test_a.py",
        ),
        Case(
            case_id="script_uncovered",
            acceptance_item_id="item2",
            layer="L2",
            expected="行为正确",
            verify_method="script:test_b.py",
        ),
    ]
    plan = Plan(
        plan_id="plan_1",
        task_id="task_1",
        scope=Scope(acceptance_item_ids=["item1", "item2"]),
        applicable_count=2,
        cases=cases,
    )
    run = Run(
        run_id="run_1",
        plan_id="plan_1",
        project_id="proj_1",
        scope=plan.scope,
        cases=[
            RunCase(case_id="cmd", result="passed"),
            RunCase(case_id="script_covered", result="failed"),
            RunCase(case_id="script_uncovered", result="failed"),
        ],
    )
    _downgrade_script_failures(plan, run)
    results = {case.case_id: case.result for case in run.cases}
    assert results["script_covered"] == "error"
    assert results["script_uncovered"] == "failed"

