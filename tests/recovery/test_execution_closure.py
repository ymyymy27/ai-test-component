import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from aitest.application.evidence.publication import (
    EvidencePublicationContext,
    EvidencePublisher,
)
from aitest.application.execution.facts import (
    ExecutionFactsAssembler,
    ExecutionFactsAssembly,
)
from aitest.application.execution.runner import SerialExecutionItem, SerialRunner
from aitest.domain.evidence.evidence import CodeIdentity, EvidenceCaptureSource
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AuthorizationRef,
    ExecutionRequest,
    OutputStreamName,
    PlanRevisionRef,
    RegisteredEntryRef,
    Run,
    RunControlState,
    RunTier,
    SideEffectClass,
    Step,
    StepLevel,
    StepRevisionRef,
    StepState,
)
from aitest.domain.execution.sources import SourceBindingKind
from aitest.domain.project.context import IsolationMode
from aitest.infrastructure.adapters.execution.command import (
    CommandAdapter,
    CommandRegistration,
)
from aitest.infrastructure.file_store.checkpoints import FileCheckpointStore
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.support.fake_unit_of_work import FakeUnitOfWork


def _plan_revision() -> PlanRevisionRef:
    return PlanRevisionRef(revision_id="plan-1", revision_no=1, digest="sha256:plan-1")


def _step_revision() -> StepRevisionRef:
    return StepRevisionRef(
        step_revision_id="step-revision-1",
        revision_no=1,
        digest="sha256:step-revision-1",
    )


def _run() -> Run:
    return Run(
        run_id="run-1",
        project_id="project-1",
        origin_workspace_id="workspace-1",
        intent_id="intent-1",
        tier=RunTier.FULL,
        driver="planned",
        conclusion_ceiling="passable",
        plan_revision_ref=_plan_revision(),
        environment_ref="environment-1",
        environment_isolation_mode=IsolationMode.VENV,
        rules_revision="rules-1",
        control_state=RunControlState.RUNNING,
        required_scope=frozenset({"case-1"}),
        selected_scope=frozenset({"case-1"}),
        source_binding_digest="sha256:source-1",
    )


def _step() -> Step:
    return Step(
        step_id="step-1",
        run_id="run-1",
        ordinal=1,
        case_id="case-1",
        level=StepLevel.L2,
        step_revision_ref=_step_revision(),
        state=StepState.PENDING,
        dependency_edges=(),
    )


def _attempt() -> Attempt:
    return Attempt(
        attempt_id="attempt-1",
        run_id="run-1",
        step_id="step-1",
        attempt_index=1,
        intent_id="intent-1",
        resolved_input_digest="sha256:input-1",
        step_revision_ref=_step_revision(),
        source_binding_digest="sha256:source-1",
        side_effect_class=SideEffectClass.READ_ONLY,
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="command/1.0",
    )


def _request() -> ExecutionRequest:
    executable = str(Path(sys.executable).resolve())
    return ExecutionRequest(
        project_id="project-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        intent_id="intent-1",
        resolved_input_digest="sha256:input-1",
        registered_entry=RegisteredEntryRef(
            entry_id="python",
            adapter_kind=AdapterKind.COMMAND,
            entrypoint=executable,
            arguments=(
                "-c",
                "import sys; "
                "print('token=' + 'secret-' + 'value'); "
                "sys.stderr.write('stderr-line\\n')",
            ),
        ),
        materialized_snapshot_ref="snapshot-1",
        environment_ref="environment-1",
        source_binding_digest="sha256:source-1",
        authorization_ref=AuthorizationRef(
            authorization_id="authorization-1",
            intent_id="intent-1",
            step_id="step-1",
            resolved_input_digest="sha256:input-1",
            target_ref="target-1",
            credential_scope_ref="credential-scope-1",
            plan_revision_ref=_plan_revision(),
        ),
        side_effect_class=SideEffectClass.READ_ONLY,
    )


def test_execution_to_spool_evidence_and_execution_facts_closure(tmp_path: Path) -> None:
    spool_store = FileSpoolStore(tmp_path)
    object_store = FileObjectStore(tmp_path)
    checkpoint_store = FileCheckpointStore(tmp_path)
    adapter = CommandAdapter(
        lambda _scope: {"token": "secret-value"},
        spool_store=spool_store,
        stream_block_size=8,
    )
    adapter.register(
        CommandRegistration(
            entry_id="python",
            executable=sys.executable,
            cwd=Path.cwd(),
        )
    )
    runner = SerialRunner(
        adapter,
        spool_store,
        checkpoint_store=checkpoint_store,
        poll_interval_seconds=0.01,
    )
    serial_result = runner.run_serial(
        (
            SerialExecutionItem(
                step=_step(),
                attempt=_attempt(),
                request=_request(),
            ),
        )
    )
    assert serial_result.steps[0].state is StepState.COMPLETED

    checkpoint_records = checkpoint_store.scan()
    assert len(checkpoint_records) == 1
    assert checkpoint_records[0].attempt.attempt_id == "attempt-1"

    publisher = EvidencePublisher(spool_store, object_store)
    evidence = publisher.publish_attempt(
        EvidencePublicationContext(
            project_id="project-1",
            source_instance_id="run-1",
            run_id="run-1",
            step_id="step-1",
            attempt_id="attempt-1",
            code_identity=CodeIdentity(
                binding_kind=SourceBindingKind.GIT,
                workspace_ref="workspace-1",
                commit_id="abc123",
            ),
            capture_source=EvidenceCaptureSource.PLUGIN_RUNTIME,
        )
    )
    assert evidence
    assert any(item.redaction_summary_ref is not None for item in evidence)

    redaction_summaries = {
        f"redaction:attempt-1:{stream.value}": spool_store.read_redaction_summary(
            "attempt-1",
            stream,
        )
        for stream in (OutputStreamName.STDOUT, OutputStreamName.STDERR)
    }
    completed_run = replace(_run(), control_state=RunControlState.COMPLETED)
    facts = ExecutionFactsAssembler().assemble(
        ExecutionFactsAssembly(
            facts_id="facts-1",
            snapshot_commit_id="commit-1",
            snapshot_cursor=1,
            snapshot_revision=1,
            committed_at=datetime.now(UTC),
            run=completed_run,
            steps=serial_result.steps,
            attempts=serial_result.attempts,
            evidence_refs=evidence,
            redaction_summaries=redaction_summaries,
        )
    )

    unit_of_work = FakeUnitOfWork()
    unit_of_work.save_execution_facts(facts)
    loaded = unit_of_work.get_execution_facts("commit-1")

    assert loaded.run.control_state.value == "completed"
    assert loaded.attempts[0].state.value == "completed"
    assert len(loaded.attempts[0].output_cursors) == 2
    assert loaded.evidence_refs
    summaries = [
        block.redaction_summary
        for block in loaded.attempts[0].output_blocks
        if block.redaction_summary is not None
    ]
    assert len(summaries) == 2
    assert any(summary.replacement_count > 0 for summary in summaries)
