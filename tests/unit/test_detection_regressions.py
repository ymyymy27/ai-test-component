import os
import sys
from pathlib import Path

from aitest.core.project_context import _symbol_map, _test_dirs
from aitest.models import Case, Plan, Run, RunCase, Scope
from aitest.worker import (
    _case_dependencies_ready,
    _downgrade_script_failures,
    _missing_dependencies,
    _run_env,
    _script_self_error,
)


def test_testing_directory_is_recognized(tmp_path: Path) -> None:
    (tmp_path / "testing").mkdir()
    assert "testing/" in _test_dirs(tmp_path)


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

