from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

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


def _request(workdir: Path, attempt: str) -> ExecutionRequest:
    return ExecutionRequest(
        project_id="project-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id=attempt,
        intent_id="intent-1",
        resolved_input_digest="sha256:input-1",
        registered_entry=RegisteredEntryRef(
            entry_id="pytest",
            adapter_kind=AdapterKind.COMMAND,
            entrypoint=str(Path(sys.executable).resolve()),
            arguments=("-c", "open('pytest.xml','w').write('<testsuite/>')"),
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
        stream_block_size=4,
        artifact_sources=(("pytest.xml", "application/xml; profile=junit"),),
    )
    adapter.register(
        CommandRegistration(entry_id="pytest", executable=sys.executable, cwd=workdir)
    )
    return adapter


def _collect(adapter: CommandAdapter, handle):
    for _ in range(300):
        inspection = adapter.inspect(handle)
        if inspection.state is not ExecutionInspectionState.RUNNING:
            return adapter.collect(handle)
        time.sleep(0.01)
    raise AssertionError("command did not finish")


def test_command_artifact_is_persisted_and_readable(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    root = tmp_path / "workspace"
    adapter = _adapter(root, workdir)
    handle = adapter.start(_request(workdir, "attempt-artifact"))
    result = _collect(adapter, handle)
    assert result.complete is True
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.media_type == "application/xml; profile=junit"
    assert artifact.truncated is False
    assert adapter._spool_store.read_artifact(artifact) == b"<testsuite/>"
    assert adapter._spool_store.read_artifacts("attempt-artifact") == (artifact,)


def test_missing_artifact_is_not_invented(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    root = tmp_path / "workspace"
    adapter = _adapter(root, workdir)
    request = replace(
        _request(workdir, "attempt-missing"),
        registered_entry=RegisteredEntryRef(
            entry_id="pytest",
            adapter_kind=AdapterKind.COMMAND,
            entrypoint=str(Path(sys.executable).resolve()),
            arguments=("-c", "pass"),
        ),
    )
    result = _collect(adapter, adapter.start(request))
    assert result.artifacts == ()


def test_stale_artifact_is_not_reused(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "pytest.xml").write_text("<testsuite/>", encoding="utf-8")
    root = tmp_path / "workspace"
    adapter = _adapter(root, workdir)
    request = replace(
        _request(workdir, "attempt-stale"),
        registered_entry=RegisteredEntryRef(
            entry_id="pytest",
            adapter_kind=AdapterKind.COMMAND,
            entrypoint=str(Path(sys.executable).resolve()),
            arguments=("-c", "pass"),
        ),
    )
    result = _collect(adapter, adapter.start(request))
    assert result.artifacts == ()
