"""Publish actual-process observations against the original authorized source scope."""

from dataclasses import replace
from pathlib import Path

from pydantic import TypeAdapter

from aitest.application.ports import EvidenceObjectStore
from aitest.domain.evidence.evidence import (
    CodeIdentity,
    EvidenceCaptureSource,
    EvidenceIntegrity,
    EvidenceKind,
    EvidenceRef,
    RedactionState,
)
from aitest.domain.execution.runs import Attempt, has_reliable_terminal_fact
from aitest.domain.execution.runtime_source import RuntimeSourceObservation
from aitest.domain.execution.sources import ExecutionSourceVerification, SourceVerificationState


def publish_runtime_source(
    *, attempt: Attempt, source: ExecutionSourceVerification, identity: CodeIdentity,
    objects: EvidenceObjectStore,
) -> tuple[ExecutionSourceVerification, EvidenceRef]:
    observation, plan = attempt.runtime_source, source.capture_plan
    handle = attempt.execution_handle_ref
    if observation is None or plan is None or handle is None or not source.source_instance_id:
        raise ValueError("actual source observation has no original authorized scope")
    if (observation.attempt_id != attempt.attempt_id
            or observation.process_start_identity != handle.process_start_identity
            or observation.process_id != handle.real_execution_id
            or observation.policy != plan.policy
            or Path(handle.workdir_ref).resolve()
            != Path(source.materialized_snapshot_ref).resolve()
            or not has_reliable_terminal_fact(attempt)):
        raise ValueError("actual source observation belongs to another execution")
    expected = {item.relative_path: item.content_digest for item in plan.files}
    gaps = list(observation.gaps)
    if not observation.complete:
        gaps.append("actual_source_capture_incomplete")
    if not observation.executing_process_id or not observation.executing_process_start_identity:
        gaps.append("actual_source_process_unverified")
    if (source.observed_entry_ref is None or source.observed_interpreter_ref is None
            or Path(observation.entry).resolve() != Path(source.observed_entry_ref).resolve()
            or Path(observation.interpreter).resolve()
            != Path(source.observed_interpreter_ref).resolve()):
        gaps.append("actual_source_entry_or_interpreter_mismatch")
    seen: set[str] = set()
    for file in observation.files:
        path = Path(file.relative_path)
        if (path.is_absolute() or ".." in path.parts or file.relative_path in seen
                or expected.get(file.relative_path) != file.content_digest):
            gaps.append("actual_source_file_mismatch")
        seen.add(file.relative_path)
    root = Path(source.materialized_snapshot_ref).resolve()
    entry = Path(observation.entry).resolve()
    if not entry.is_relative_to(root) or entry.relative_to(root).as_posix() not in seen:
        gaps.append("actual_source_entry_not_observed")
    # Admission failures must never be repaired by a later process observation.
    gaps.extend(source.gap_ids)
    if source.state is not SourceVerificationState.VERIFIED:
        gaps.append("source_admission_unverified")
    content = TypeAdapter(RuntimeSourceObservation).dump_json(observation)
    ref = objects.publish_bytes(source.project_id, content, media_type="application/json")
    if objects.read_bytes(ref) != content:
        raise ValueError("actual source evidence differs from its permanent material")
    evidence = EvidenceRef(
        evidence_id="runtime-source-evidence-" + attempt.attempt_id,
        project_id=source.project_id, source_instance_id=source.source_instance_id,
        run_id=attempt.run_id, step_id=attempt.step_id, attempt_id=attempt.attempt_id,
        evidence_kind=EvidenceKind.SOURCE_CHECK,
        capture_source=EvidenceCaptureSource.PLUGIN_RUNTIME,
        object_digest=ref.digest, object_size=ref.size, code_identity=identity,
        integrity=EvidenceIntegrity.COMPLETE,
        redaction_state=(RedactionState.REDACTED if "source_channel_redacted" in observation.gaps
                         else RedactionState.NOT_REQUIRED),
        media_type=ref.media_type, created_at=attempt.ended_at,
    )
    return replace(
        source,
        state=SourceVerificationState.UNVERIFIED if gaps else SourceVerificationState.VERIFIED,
        gap_ids=tuple(dict.fromkeys(gaps)), evidence_refs=(evidence.evidence_id,),
        observed_entry_ref=observation.entry, observed_interpreter_ref=observation.interpreter,
        observed_import_ref=observation.entry, raw_output_object=ref, verified_at=attempt.ended_at,
    ), evidence
