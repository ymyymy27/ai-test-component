import os
import sys
from pathlib import Path

import pytest

from aitest.ai_features import _conclusion_free
from aitest.core.expectations import expectation_matches, publish_problems
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
from aitest.models import AcceptanceItem, Case, Plan, Run, RunCase, Scope
from aitest.runners.base import RunnerResult
from aitest.runtime_commands import (
    _annotate_case_coverage,
    _broad_test_command,
    _downgrade_broad_business_cases,
    _drop_unavailable_django_scripts,
    _merge_duplicate_command_cases,
    _plan_dependencies,
    _prefix_component_command,
    _shared_command_warnings,
    _split_multi_item_cases,
    _validate_dependencies,
    _validate_plan_cases,
)
from aitest.worker import (
    _case_dependencies_ready,
    _dependency_suggestion,
    _downgrade_script_failures,
    _environment_failure,
    _environment_fingerprint,
    _is_dev_server_command,
    _is_e2e_command,
    _missing_command_tool,
    _missing_dependencies,
    _missing_module_from_output,
    _missing_modules_from_output,
    _resolve_dependency_package,
    _run_env,
    _run_python_script,
    _script_self_error,
    _service_block_reason,
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


def test_directory_prefixed_whole_suite_commands_are_recognized() -> None:
    for method in (
        "command:cd frontend && bun run test",
        "command:cd frontend && npm run test",
        "command:cd backend && uv run pytest",
        "pytest:python -m pytest -q",
        "command:cd backend && python -m pytest",
    ):
        assert _broad_test_command(method), method
    assert (
        _broad_test_command(
            "command:cd frontend && bunx playwright test tests/login.spec.ts"
        )
        == ""
    )


def test_shared_file_command_warns_about_weak_item_mapping() -> None:
    cases = [
        Case(
            case_id=f"case_{index}",
            acceptance_item_id=f"item_{index}",
            layer="L2",
            expected="文件通过",
            verify_method="command:python -m pytest tests/test_users.py",
        )
        for index in (1, 2)
    ]
    warnings = _shared_command_warnings(cases)
    assert warnings
    assert "文件级通过" in warnings[0]


def test_broad_business_case_downgrades_to_manual() -> None:
    case = Case(
        case_id="case_1",
        acceptance_item_id="item_1",
        layer="L2",
        expected="前端单元测试通过",
        verify_method="command:bun run test",
    )
    assert _downgrade_broad_business_cases([case]) == ["bun run test"]
    assert case.verify_method == "manual"
    assert any("bun run test" in step for step in case.steps)


def test_whole_suite_is_allowed_only_as_l1_smoke(tmp_path: Path) -> None:
    smoke_case = Case(
        case_id="case_smoke",
        acceptance_item_id="item_1",
        layer="L1",
        expected="整包测试通过",
        verify_method="command:python runtests.py",
    )
    _validate_plan_cases([smoke_case], "business", {"root": str(tmp_path)})


def test_business_case_cannot_bind_multiple_items(tmp_path: Path) -> None:
    case = Case(
        case_id="case_multi",
        acceptance_item_id="item_1",
        acceptance_item_ids=["item_2"],
        layer="L2",
        expected="文件通过",
        verify_method="command:python -m pytest tests/test_users.py",
    )
    with pytest.raises(AiTestError) as excinfo:
        _validate_plan_cases([case], "business", {"root": str(tmp_path)})
    assert "绑定了多个验收项" in str(excinfo.value)


def test_multi_item_case_splits_with_exact_item_sources() -> None:
    items = [
        AcceptanceItem(item_id="item_1", text="用户注册"),
        AcceptanceItem(item_id="item_2", text="用户列表查询"),
    ]
    case = Case(
        case_id="case_1",
        acceptance_item_id="item_1",
        acceptance_item_ids=["item_2"],
        layer="L2",
        expected="文件通过",
        expectation_source="用户注册、用户列表查询",
        verify_method="command:python -m pytest tests/test_users.py",
    )
    cases, split = _split_multi_item_cases([case], items)
    assert split == 1
    assert len(cases) == 2
    assert [c.expectation_source for c in cases] == ["用户注册", "用户列表查询"]
    assert all(c.acceptance_item_ids == [] for c in cases)


def test_coverage_kind_distinguishes_direct_file_shared_and_missing() -> None:
    context = {
        "test_functions": {
            "backend/tests/test_users.py": ["test_one", "test_two"],
        }
    }
    direct = Case(
        case_id="case_direct",
        acceptance_item_id="item_1",
        layer="L2",
        expected="通过",
        verify_method="command:python -m pytest tests/test_users.py::test_one",
    )
    file_only = Case(
        case_id="case_file",
        acceptance_item_id="item_2",
        layer="L2",
        expected="通过",
        verify_method="command:python -m pytest tests/test_users.py",
    )
    missing = Case(
        case_id="case_missing",
        acceptance_item_id="item_3",
        layer="L2",
        expected="通过",
        verify_method="command:python -m pytest tests/test_users.py::nope",
    )
    shared = Case(
        case_id="case_shared",
        acceptance_item_id="item_4",
        layer="L2",
        expected="通过",
        verify_method="command:python -m pytest tests/test_users.py::test_two",
    )
    shared_other = Case(
        case_id="case_shared_other",
        acceptance_item_id="item_5",
        layer="L2",
        expected="通过",
        verify_method="command:python -m pytest tests/test_users.py::test_two",
    )
    notes = _annotate_case_coverage(
        [direct, file_only, missing, shared, shared_other], context
    )
    assert direct.coverage_kind == "direct"
    assert file_only.coverage_kind == "file"
    assert missing.coverage_kind == "unverified"
    assert shared.coverage_kind == "shared"
    assert "文件级覆盖" in notes["case_file"]
    assert "共用测试节点" in notes["case_shared"]

    playwright = Case(
        case_id="case_playwright",
        acceptance_item_id="item_6",
        layer="L2",
        expected="通过",
        verify_method=(
            'command:cd frontend && bunx playwright test tests/login.spec.ts '
            '--grep "Log In button"'
        ),
    )
    _annotate_case_coverage(
        [playwright],
        {"playwright_tests": {"frontend/tests/login.spec.ts": ["Log In button"]}},
    )
    assert playwright.coverage_kind == "direct"
    assert playwright.test_nodes == [
        "tests/login.spec.ts::Log In button"
    ]

    bun = Case(
        case_id="case_bun",
        acceptance_item_id="item_7",
        layer="L2",
        expected="通过",
        verify_method="command:cd frontend && bun test unit/cn.test.ts",
    )
    _annotate_case_coverage(
        [bun],
        {
            "bun_tests": {
                "frontend/unit/cn.test.ts": ["cn merges utility class names"]
            }
        },
    )
    assert bun.coverage_kind == "direct"
    assert bun.test_nodes == [
        "unit/cn.test.ts::cn merges utility class names"
    ]


def test_plan_dependencies_skip_node_only_packages() -> None:
    cases = [
        Case(
            case_id="case_1",
            acceptance_item_id="item_1",
            layer="L2",
            expected="通过",
            verify_method="command:cd frontend && bun run build",
            dependencies=["@playwright/test", "bun", "pytest>=8", "playwright"],
        )
    ]
    assert _plan_dependencies(cases) == ["playwright", "pytest>=8"]


def test_business_file_level_pytest_requires_function_when_known(tmp_path: Path) -> None:
    case = Case(
        case_id="case_file",
        acceptance_item_id="item_1",
        layer="L2",
        expected="通过",
        verify_method="command:python -m pytest backend/tests/test_users.py",
    )
    with pytest.raises(AiTestError) as excinfo:
        _validate_plan_cases(
            [case],
            "business",
            {
                "root": str(tmp_path),
                "test_functions": {
                    "backend/tests/test_users.py": ["test_one"]
                },
            },
        )
    assert "具体测试函数" in str(excinfo.value)


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
    assert "Playwright 浏览器未安装" in _environment_failure(
        b"Error: browserType.launch: Executable doesn't exist at chromium_headless_shell",
        tmp_path,
    )


def test_duplicate_command_cases_for_different_items_are_not_merged() -> None:
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
    assert removed == 0
    assert len(merged) == 2
    assert [case.acceptance_item_id for case in merged] == ["item_1", "item_2"]


def test_alembic_command_runs_from_owning_subproject(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    assert (
        _prefix_component_command("command:alembic upgrade head", tmp_path)
        == "command:cd backend && alembic upgrade head"
    )
    assert (
        _prefix_component_command("command:alembic -c custom.ini upgrade head", tmp_path)
        == "command:alembic -c custom.ini upgrade head"
    )


def test_e2e_and_dev_server_commands_are_classified() -> None:
    assert _is_e2e_command("command:cd frontend && bunx playwright test tests/a.spec.ts")
    assert _is_e2e_command("playwright:tests/login.spec.ts")
    assert _is_dev_server_command("command:cd frontend && bun run dev")
    assert _is_dev_server_command("command:npm run dev")
    assert not _is_dev_server_command("command:npm run build")
    assert "端到端" in _service_block_reason(
        "command:bunx playwright test", [], True, {}
    )
    assert "开发服务" in _service_block_reason("command:bun run dev", [], True, {})
    assert (
        _service_block_reason("command:bunx playwright test", [], True, {
            "PLAYWRIGHT_BASE_URL": "http://localhost:5173"
        })
        == ""
    )


def test_scoped_npm_dependency_is_valid() -> None:
    case = Case(
        case_id="case_scoped",
        acceptance_item_id="item_1",
        layer="L2",
        expected="通过",
        verify_method="command:cd frontend && bun run build",
        dependencies=["@playwright/test", "pydantic>=2,<3"],
    )
    _validate_dependencies(case)


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



def test_script_with_only_test_functions_never_passes_without_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "generated_check.py"
    script.write_text("def test_ok():\n    assert False\n", encoding="utf-8")
    calls: list[str] = []

    def fake_run(command: str, **_: object) -> RunnerResult:
        calls.append(command)
        return RunnerResult(status="passed", exit_code=5)

    monkeypatch.setattr("aitest.worker.run_command", fake_run)
    result = _run_python_script(Path(sys.executable), script, tmp_path, {}, None)
    assert result.status == "error"
    assert "未执行任何断言" in result.error
    assert all("pytest" in command for command in calls)


def test_mixed_failure_and_missing_dependency_stays_a_failure(tmp_path: Path) -> None:
    output = (
        b"FAILED tests/test_a.py::test_ok - AssertionError\n"
        b"ModuleNotFoundError: No module named 'missing_dep'\n"
    )
    assert _environment_failure(output, tmp_path) == ""


def test_expectation_item_id_matches_only_a_whole_token() -> None:
    from aitest.models import AcceptanceItem

    item = AcceptanceItem(
        item_id="item_1",
        text="删除后重新读取批次仍然可读",
        observable=True,
        status="active",
        source="用户",
        module_id=None,
    )
    assert expectation_matches("item_1", item) is True
    assert expectation_matches("item_10", item) is False
    assert expectation_matches("item_1x", item) is False


def test_blank_preconditions_do_not_pass_publish_gate() -> None:
    case = Case(
        case_id="case_blank",
        layer="L2",
        verify_method="command:pytest",
        expected="删除后重新读取批次仍然可读",
        expectation_source="删除后重新读取批次仍然可读",
        preconditions=["  "],
        acceptance_item_ids=["item_1"],
    )
    problems = publish_problems([case])
    assert any("前置条件" in problem for problem in problems)


def test_ai_summary_rejects_english_conclusion_words() -> None:
    assert _conclusion_free("用例 03 返回 200；日志已保留") is True
    assert _conclusion_free("All tests passed") is False
    assert _conclusion_free("LGTM") is False
    assert _conclusion_free("ready for release") is False


def test_multiple_missing_modules_are_all_collected() -> None:
    output = (
        b"ModuleNotFoundError: No module named 'alpha'\n"
        b"ModuleNotFoundError: No module named 'beta.core'\n"
        b"ModuleNotFoundError: No module named 'alpha'\n"
    )
    assert _missing_modules_from_output(output) == ["alpha", "beta"]
