"""Independent boundary probes; temporary workspaces, synthetic data, no network.

Run with: uv run python -m docs.validation.p1-e2e-audit-20261003.probes
Observed defects are asserted as observations, not product acceptance successes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from aitest.application.execution.recovery import recover_attempt
from aitest.application.execution.runner import SerialRunner
from aitest.application.planning.model_orchestration import request_model_draft
from aitest.application.project.context import detect_context_gaps, move_binding
from aitest.application.project.persistence import load_delivery
from aitest.application.project.serialization import delivery_to_payload, project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.execution.runs import (
    AttemptState,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    OutputStreamName,
    RecoveryCheckpoint,
)
from aitest.domain.planning.model_outbound import MaterialKind, ModelTaskType
from aitest.domain.project.context import (
    BindingForm,
    Delivery,
    DriveKind,
    EnvironmentRef,
    LocalProject,
    LocalProjectBinding,
    Module,
    ModuleDependencyGraph,
)
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.file_store.checkpoints import FileCheckpointStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.interfaces.local.api import EntryKind, Session
from tests.recovery.test_execution_recovery import _attempt, _handle
from tests.support.memory_model import (
    MemoryCredentialResolver,
    MemoryModelCaller,
    MemoryProjector,
)
from tests.support.memory_substrate import FixedClock
from tests.unit.test_model_orchestration import _policy, _world
from tests.unit.test_planning_persistence_real_store import _start
from tests.unit.test_serial_runner import _attempt as command_attempt
from tests.unit.test_serial_runner import _request as command_request


def project_gaps() -> dict[str, object]:
    module = Module("m", "p", "module", "pure function", "f(x)")
    project = LocalProject("p", "w", "project", "check f", "1", modules=(module,))
    graph = ModuleDependencyGraph("p", modules=(module,))
    environment = EnvironmentRef("env", 1, "Python 3.13", "stdlib")
    gaps = detect_context_gaps(project=project, graph=graph, environment=environment)
    assert [g.kind for g in gaps] == ["missing_dependency_registration"]
    return {"single_module": True, "blocking_gaps": [g.kind for g in gaps if g.blocking]}


def moved_binding() -> dict[str, object]:
    original = LocalProjectBinding(
        "b", 1, "p", "C:/old", BindingForm.PLAIN, manifest_digest="sha256:old", confirmed=True
    )
    result = move_binding(original, canonical_path="C:/new", drive_kind=DriveKind.FIXED)
    assert result.binding is not None and result.binding.confirmed
    return {
        "new_revision": result.binding.binding_revision,
        "new_path": result.binding.canonical_path,
        "confirmation_inherited": result.binding.confirmed,
        "digest_inherited": result.binding.manifest_digest,
    }


def project_policy() -> dict[str, object]:
    unit, reader = _world()
    policy = _policy()
    caller = MemoryModelCaller()
    result = request_model_draft(
        project_id="other-project",
        policy=policy,
        task_type=ModelTaskType.CONTEXT_SUMMARY,
        selected_material={MaterialKind.PROJECT_CONTEXT: "Synthetic other project content"},
        unit_of_work=unit,
        reader=reader,
        projector=MemoryProjector(),
        credentials=MemoryCredentialResolver(),
        caller=caller,
        clock=FixedClock(),
        source_revision=1,
        base_manual_revision=0,
    )
    assert result.is_draft_ready and result.request is not None
    return {
        "policy_project": policy.project_id,
        "request_project": result.request.project_id,
        "accepted": result.is_draft_ready,
        "boundary": "real application orchestration with memory external ports; no network",
    }


def command_and_delivery(root: Path) -> dict[str, object]:
    assembly = assemble_workspace_core(root, instance_id="audit-core")
    api = assembly.api
    session = Session("audit-ui", EntryKind.HUMAN_UI)

    def send(action: str, project: str, params: dict[str, object], number: int, expected: int = 0):
        return api.dispatch(
            Command(
                request_id=f"req-{number}",
                action=action,
                project_id=project,
                intent_id=f"intent-{number}",
                expected_revision=expected,
                parameters=params,
            ),
            session,
        )

    project = LocalProject(
        "payload-project", assembly.workspace.workspace_id, "project", "test", "1"
    )
    saved = send("save_context", "envelope-project", {"project": project_to_payload(project)}, 1)
    assert saved.error is None
    owner = assembly.unit_of_work.repo.read(
        aggregate_kind="project", record_id="payload-project", revision=1
    ).payload["project_id"]
    delivery = Delivery(
        "delivery", "unregistered-task", "v1", "run", verified_in_scope=("all-required",)
    )
    accepted = send(
        "save_delivery", "payload-project", {"delivery": delivery_to_payload(delivery)}, 2
    )
    assert accepted.error is None
    stack = _start(root)
    foreign = load_delivery(
        stack.reader, project_id="foreign-project", delivery_id="delivery", revision=1
    )
    assert foreign.is_verified
    return {
        "project_envelope_mismatch_accepted": (saved.error is None),
        "stored_project": owner,
        "self_supplied_verified_scope_accepted": (accepted.error is None),
        "verified_scope": list(foreign.verified_in_scope),
        "foreign_project_delivery_read_accepted": True,
        "task_registered": False,
        "execution_records": 0,
        "boundary": "default LocalAPI and real file-backed storage",
    }


def stale_publication(root: Path) -> dict[str, object]:
    assembly = assemble_workspace_core(root, instance_id="publication-core")
    session = Session("audit-ui", EntryKind.HUMAN_UI)
    project = LocalProject("p", assembly.workspace.workspace_id, "project", "test", "1")
    saved = assembly.api.dispatch(
        Command(
            request_id="save-project",
            action="save_context",
            project_id="p",
            intent_id="save-project-intent",
            expected_revision=0,
            parameters={"project": project_to_payload(project)},
        ),
        session,
    )
    assert saved.error is None
    responses = []
    for n, text in enumerate(("original", "new edit", "stale edit"), 1):
        draft = {
            "rule_id": "rule",
            "revision": n,
            "scope": "module",
            "text": text,
            "source": "human",
            "confirmed": True,
        }
        responses.append(
            assembly.api.dispatch(
                Command(
                    request_id=f"publish-{n}",
                    action="publish_rules",
                    project_id="p",
                    intent_id=f"publish-intent-{n}",
                    expected_revision=0,
                    parameters={"draft": draft},
                ),
                session,
            )
        )
    assert all((r.error is None) and r.result["published"] for r in responses)
    history = [
        assembly.unit_of_work.repo.read(
            aggregate_kind="rule_version", record_id="rule", revision=n
        ).payload["text"]
        for n in (1, 2, 3)
    ]
    return {
        "same_stale_expected_revision": 0,
        "published": [r.result["published"] for r in responses],
        "stored_text_history": history,
        "boundary": "default human session, real immutable store; caller expected revision ignored",
    }


class LostExecution:
    def inspect(self, handle):
        return ExecutionInspectionResult(
            handle_id=handle.handle_id,
            state=ExecutionInspectionState.LOST,
            process_reachable=False,
            identity_matches=False,
            unknown_reason="new_adapter_has_no_handle",
        )


def terminal_recovery(root: Path) -> dict[str, object]:
    root.mkdir(parents=True)
    spool = FileSpoolStore(root)
    checkpoints = FileCheckpointStore(root)
    adapter = CommandAdapter(spool_store=spool, stream_block_size=1)
    adapter.register(CommandRegistration("entry-1", sys.executable, root))
    request = command_request()
    request = replace(
        request,
        registered_entry=replace(
            request.registered_entry,
            entrypoint=sys.executable,
            arguments=("-c", 'print("saved-output")'),
        ),
        timeout_ms=10000,
    )
    attempt = SerialRunner(adapter, spool, checkpoint_store=checkpoints).execute_attempt(
        command_attempt(), request
    )
    assert attempt.state is AttemptState.COMPLETED and attempt.exit_fact_ref is not None
    results = SerialRunner(LostExecution(), spool, checkpoint_store=checkpoints).recover_pending()
    assert len(results) == 1 and results[0].attempt.state is AttemptState.INVALIDATED
    return {
        "before": "completed",
        "after": results[0].attempt.state.value,
        "action": results[0].action.value,
        "stored_after": checkpoints.scan()[0].attempt.state.value,
        "real_completed_exit_fact_saved": True,
        "boundary": "real child, spool/checkpoint; new core inspection reports lost; no replay",
    }


def live_salvage(root: Path) -> dict[str, object]:
    store = FileSpoolStore(root)
    writer = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
        block_size=1024,
    )
    observation: dict[str, object] = {}
    try:
        writer.append(b"live-unsealed")
        before = store.read_manifest("attempt-1")
        attempt = replace(_attempt(), execution_handle_ref=_handle())
        checkpoint = RecoveryCheckpoint(
            run_id=attempt.run_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            last_committed_stage="running",
            output_cursors=(),
            output_block_refs=(),
            resolved_input_digest=attempt.resolved_input_digest,
            side_effect_class=attempt.side_effect_class,
            execution_handle_ref=_handle(),
        )
        result = recover_attempt(
            checkpoint,
            attempt,
            store,
            inspection=ExecutionInspectionResult(
                handle_id="handle-1",
                state=ExecutionInspectionState.RUNNING,
                process_reachable=True,
                identity_matches=True,
            ),
        )
        after = store.read_manifest("attempt-1")
        assert len(before.blocks) == 0 and len(after.blocks) == 1 and not after.blocks[0].complete
        observation.update(
            {
                "live_process_reachable": True,
                "action": result.action.value,
                "blocks_before": len(before.blocks),
                "blocks_after": len(after.blocks),
                "published_partial_recovery_block_while_writer_open": True,
            }
        )
    finally:
        try:
            writer.close()
        except ValueError as error:
            observation["writer_close_error"] = str(error)
    assert observation.get("writer_close_error") == "spool block conflicts with existing metadata"
    return observation


def main() -> None:
    observations = {
        "single_module": project_gaps(),
        "binding_move": moved_binding(),
        "policy_project": project_policy(),
    }
    with tempfile.TemporaryDirectory(prefix="aitest-e2e-audit-") as directory:
        root = Path(directory)
        for name, probe in [
            ("command_delivery", command_and_delivery),
            ("stale_publication", stale_publication),
            ("terminal_recovery", terminal_recovery),
            ("live_salvage", live_salvage),
        ]:
            observations[name] = probe(root / name)
    result = {
        "baseline": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
        "observations": observations,
        "scope": "contract probes, not real Trae acceptance",
    }
    target = Path(__file__).with_name("observations.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
