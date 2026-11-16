from __future__ import annotations

import sys
import time
from pathlib import Path

from aitest.domain.execution.junit import parse_junit
from aitest.domain.execution.runs import (
    AdapterKind,
    AuthorizationRef,
    ExecutionInspectionState,
    ExecutionRequest,
    PlanRevisionRef,
    RegisteredEntryRef,
    SideEffectClass,
)
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.file_store.spool import FileSpoolStore


def _request(workdir: Path, attempt: str, scope: str) -> ExecutionRequest:
    return ExecutionRequest(
        project_id="project-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id=attempt,
        intent_id="intent-1",
        resolved_input_digest="sha256:input-1",
        registered_entry=RegisteredEntryRef(
            entry_id="python-pytest",
            adapter_kind=AdapterKind.COMMAND,
            entrypoint=str(Path(sys.executable).resolve()),
            arguments=(
                "-m",
                "pytest",
                "--import-mode=importlib",
                "-p",
                "no:cacheprovider",
                "--junitxml=pytest.xml",
                "--tb=long",
                scope,
            ),
        ),
        materialized_snapshot_ref=str(workdir),
        environment_ref="environment-1",
        source_binding_digest="sha256:source-1",
        authorization_ref=AuthorizationRef(
            authorization_id="authorization-1",
            intent_id="intent-1",
            step_id="step-1",
            resolved_input_digest="sha256:input-1",
            target_ref="target-1",
            credential_scope_ref="scope-1",
            plan_revision_ref=PlanRevisionRef("plan-1", 1, "sha256:plan-1"),
        ),
        side_effect_class=SideEffectClass.READ_ONLY,
    )


def _adapter(root: Path, workdir: Path) -> CommandAdapter:
    adapter = CommandAdapter(
        spool_store=FileSpoolStore(root),
        stream_block_size=1024,
        artifact_sources=(("pytest.xml", "application/xml; profile=junit"),),
    )
    adapter.register(
        CommandRegistration(entry_id="python-pytest", executable=sys.executable, cwd=workdir)
    )
    return adapter


def _collect(adapter: CommandAdapter, handle):
    for _ in range(600):
        inspection = adapter.inspect(handle)
        if inspection.state is not ExecutionInspectionState.RUNNING:
            return adapter.collect(handle)
        time.sleep(0.01)
    raise AssertionError("pytest did not finish")


def _write_packages(root: Path) -> None:
    for package in ("pkgA", "pkgB"):
        tests = root / package / "tests"
        tests.mkdir(parents=True)
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (tests / "test_order.py").write_text(
            "def test_same_name():\n    assert 1 == 1\n", encoding="utf-8"
        )


def test_same_named_packages_execute_and_junit_is_evidence(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _write_packages(workdir)
    root = tmp_path / "workspace"
    adapter = _adapter(root, workdir)
    result = _collect(adapter, adapter.start(_request(workdir, "attempt-a", "pkgA/tests")))
    assert result.complete is True
    assert len(result.artifacts) == 1
    content = adapter._spool_store.read_artifact(result.artifacts[0])
    parsed = parse_junit(
        content,
        source_files=frozenset({"pkgA/tests/test_order.py"}),
        expected_test_count=1,
        exit_code=0,
    )
    assert parsed.total == 1
    assert parsed.passed == 1
    assert parsed.tests[0].node_id == "pkgA/tests/test_order.py::test_same_name"


def test_failure_frame_is_localized_without_inventing_source(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    tests = workdir / "pkg" / "tests"
    tests.mkdir(parents=True)
    (workdir / "pkg" / "order.py").write_text(
        "def total():\n    return 1\n", encoding="utf-8"
    )
    (tests / "test_order.py").write_text(
        "from pkg.order import total\n\n"
        "def test_total():\n    assert total() == 2\n",
        encoding="utf-8",
    )
    root = tmp_path / "workspace"
    adapter = _adapter(root, workdir)
    result = _collect(adapter, adapter.start(_request(workdir, "attempt-b", "pkg/tests")))
    content = adapter._spool_store.read_artifact(result.artifacts[0])
    parsed = parse_junit(
        content,
        source_files=frozenset({"pkg/tests/test_order.py", "pkg/order.py"}),
        expected_test_count=1,
        exit_code=1,
    )
    assert parsed.failed == 1
    failed = parsed.tests[0]
    assert failed.failure_frame is not None
    assert failed.failure_frame.snapshot_path == "pkg/tests/test_order.py"
    # The JUnit failure text has no product-code frame; do not invent one.
    assert failed.suspect_source_frame is None
    # The test-code frame is still a real location.
    assert failed.location_unknown is False


def test_node_scope_executes_exactly_one_test(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _write_packages(workdir)
    root = tmp_path / "workspace"
    adapter = _adapter(root, workdir)
    result = _collect(
        adapter,
        adapter.start(
            _request(
                workdir,
                "attempt-node",
                "pkgA/tests/test_order.py::test_same_name",
            )
        ),
    )
    assert result.complete is True
    content = adapter._spool_store.read_artifact(result.artifacts[0])
    parsed = parse_junit(
        content,
        source_files=frozenset({"pkgA/tests/test_order.py"}),
        expected_test_count=1,
        exit_code=0,
    )
    assert parsed.total == 1
    assert parsed.tests[0].node_id == "pkgA/tests/test_order.py::test_same_name"


def test_unittest_style_tests_run_through_the_pytest_scope(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    tests = workdir / "tests"
    tests.mkdir(parents=True)
    (tests / "test_order.py").write_text(
        "import unittest\n\n"
        "class TestOrder(unittest.TestCase):\n"
        "    def test_create(self):\n"
        "        self.assertEqual(1, 1)\n",
        encoding="utf-8",
    )
    root = tmp_path / "workspace"
    adapter = _adapter(root, workdir)
    result = _collect(adapter, adapter.start(_request(workdir, "attempt-unittest", "tests")))
    assert result.complete is True
    content = adapter._spool_store.read_artifact(result.artifacts[0])
    parsed = parse_junit(
        content,
        source_files=frozenset({"tests/test_order.py"}),
        expected_test_count=1,
        exit_code=0,
    )
    assert parsed.total == 1
    assert parsed.passed == 1
    assert parsed.tests[0].node_id == "tests.test_order.TestOrder::test_create"
