"""Serial execution loop and dependency dispatch skeleton."""

import hashlib
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.recovery import (
    CaseReuseBasis,
    CaseReuseInvalidation,
    CheckpointStore,
    RecoveryAction,
    RecoveryRecord,
    RecoveryResult,
    invalidate_downstream_attempts,
    invalidate_reuse_bases,
    recover_attempt,
    restore_checkpoint_attempt,
)
from aitest.application.execution.start_identity import execution_start_fingerprint
from aitest.application.ports import ExecutionPort, SpoolStore
from aitest.domain.execution.output import require_saved_output_cursors
from aitest.domain.execution.runs import (
    Attempt,
    AttemptState,
    CaptureCompleteness,
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    ExecutionRequest,
    OutputBlockRef,
    OutputCursor,
    OutputStreamName,
    PlanRevisionRef,
    RecoveryCheckpoint,
    SpoolManifest,
    Step,
    StepState,
    StopRequestResult,
    authorization_action_basis,
    has_complete_capture,
    has_reliable_terminal_fact,
    has_verified_exit,
)

_TERMINAL_STEP_STATES = frozenset(
    {
        StepState.COMPLETED,
        StepState.CANCELLED,
        StepState.INVALIDATED,
        StepState.EXECUTION_ERROR,
    }
)
_BLOCKING_UPSTREAM_STATES = frozenset(
    {
        StepState.BLOCKED,
        StepState.CANCELLED,
        StepState.INVALIDATED,
        StepState.EXECUTION_ERROR,
    }
)


class _ExecutionObservationMismatch(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("execution observation cannot be verified: " + reason)


@dataclass(frozen=True, slots=True)
class DispatchPlan:
    ready_step_ids: tuple[str, ...] = ()
    blocked_step_ids: tuple[str, ...] = ()
    waiting_step_ids: tuple[str, ...] = ()
    terminal_step_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SerialExecutionItem:
    step: Step
    attempt: Attempt
    request: ExecutionRequest


@dataclass(frozen=True, slots=True)
class SerialExecutionResult:
    steps: tuple[Step, ...]
    attempts: tuple[Attempt, ...]


class StartValidationPort(Protocol):
    """Optional boundary for authorization/input/source checks owned by a caller."""

    def validate(self, attempt: Attempt, request: ExecutionRequest) -> None: ...


class SerialRunner:
    """Execute ready steps serially and persist sealed captured blocks."""

    def __init__(
        self,
        execution_port: ExecutionPort,
        spool_store: SpoolStore | None = None,
        *,
        checkpoint_store: CheckpointStore | None = None,
        poll_interval_seconds: float = 0.01,
        start_validator: StartValidationPort | None = None,
        commit_coordinator: ExecutionCommitCoordinator | None = None,
        reuse_bases: Sequence[CaseReuseBasis] = (),
        previous_attempt_ids_by_step: Mapping[str, Sequence[str]] | None = None,
        dispatch_allowed: Callable[[ExecutionRequest], bool] | None = None,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be non-negative")
        self._execution_port = execution_port
        self._spool_store = spool_store
        self._checkpoint_store = checkpoint_store
        self._poll_interval_seconds = poll_interval_seconds
        self._start_validator = start_validator
        self._commit_coordinator = commit_coordinator
        self._reuse_bases = tuple(reuse_bases)
        self._previous_attempt_ids_by_step = {
            step_id: tuple(attempt_ids)
            for step_id, attempt_ids in (previous_attempt_ids_by_step or {}).items()
        }
        self._reuse_invalidations: list[CaseReuseInvalidation] = []
        self._intent_claims: dict[tuple[str, str], Attempt] = {}
        self._intent_fingerprints: dict[tuple[str, str], str] = {}
        self._authorization_claims: dict[str, tuple[str, str]] = {}
        self._dispatch_allowed = dispatch_allowed

    def plan_dispatch(self, steps: Sequence[Step]) -> DispatchPlan:
        states = self._state_by_step_id(steps)
        ready: list[str] = []
        blocked: list[str] = []
        waiting: list[str] = []
        terminal: list[str] = []

        for step in steps:
            if step.state in _TERMINAL_STEP_STATES:
                terminal.append(step.step_id)
                continue
            if step.state is StepState.BLOCKED:
                blocked.append(step.step_id)
                continue
            if step.state not in {StepState.PENDING, StepState.READY}:
                waiting.append(step.step_id)
                continue

            dependencies = self._required_dependency_ids(step)
            dependency_states = [states.get(step_id) for step_id in dependencies]
            if any(state is None for state in dependency_states):
                waiting.append(step.step_id)
            elif any(state in _BLOCKING_UPSTREAM_STATES for state in dependency_states):
                blocked.append(step.step_id)
            elif all(state is StepState.COMPLETED for state in dependency_states):
                ready.append(step.step_id)
            else:
                waiting.append(step.step_id)

        return DispatchPlan(
            ready_step_ids=tuple(ready),
            blocked_step_ids=tuple(blocked),
            waiting_step_ids=tuple(waiting),
            terminal_step_ids=tuple(terminal),
        )

    def run_serial(
        self,
        items: Sequence[SerialExecutionItem],
        *,
        max_waves: int = 100,
        max_polls_per_slice: int = 100,
    ) -> SerialExecutionResult:
        if max_waves < 1:
            raise ValueError("max_waves must be positive")
        if type(max_polls_per_slice) is not int or not 1 <= max_polls_per_slice <= 10000:
            raise ValueError("slice poll budget must be an integer in 1..10000")
        steps = [item.step for item in items]
        self._state_by_step_id(steps)
        step_index = self._index_by_step_id(steps)
        items_by_step = self._items_by_step_id(items)
        attempts: dict[str, Attempt] = {}

        def advance(step_id: str) -> Attempt:
            item = items_by_step[step_id]
            attempt = self.execute_attempt(
                item.attempt, item.request, max_polls=max_polls_per_slice
            )
            attempts[attempt.attempt_id] = attempt
            index = step_index[step_id]
            steps[index] = replace(
                steps[index],
                state=self._step_state_for_attempt(attempt.state),
                current_attempt_id=attempt.attempt_id,
            )
            items_by_step[step_id] = replace(item, step=steps[index], attempt=attempt)
            return attempt

        # Resume the already published current attempt before admitting another
        # serial action. A running Step is not a reason to ignore its checkpoint.
        for step in steps:
            if step.state is StepState.RUNNING or (
                step.state is StepState.PENDING_VERIFICATION and step.current_attempt_id is not None
            ):
                item = items_by_step[step.step_id]
                if step.current_attempt_id != item.attempt.attempt_id:
                    raise ValueError("active serial step does not name its current attempt")
                prepared = self._prepare_attempt(item.attempt, item.request)
                fingerprint = _start_fingerprint(prepared, item.request)
                saved = (
                    self._commit_coordinator.find_start(
                        project_id=item.request.project_id,
                        intent_id=item.request.intent_id,
                        fingerprint=fingerprint,
                    )
                    if self._commit_coordinator is not None
                    else self._find_intent_claim(item.request.intent_id, item.request.project_id)
                )
                if saved is None or saved.attempt_id != item.attempt.attempt_id:
                    raise ValueError("active serial step has no verified saved start claim")
                items_by_step[step.step_id] = replace(item, attempt=saved)
                resumed = advance(step.step_id)
                if self._blocks_serial_progress(resumed):
                    return SerialExecutionResult(tuple(steps), tuple(attempts.values()))

        for _ in range(max_waves):
            plan = self.plan_dispatch(steps)
            for step_id in plan.blocked_step_ids:
                index = step_index[step_id]
                steps[index] = replace(steps[index], state=StepState.BLOCKED)
            if not plan.ready_step_ids:
                break
            for step_id in plan.ready_step_ids:
                item = items_by_step[step_id]
                if self._dispatch_allowed is not None and not self._dispatch_allowed(item.request):
                    return SerialExecutionResult(tuple(steps), tuple(attempts.values()))
                attempt = advance(step_id)
                if self._blocks_serial_progress(attempt):
                    return SerialExecutionResult(tuple(steps), tuple(attempts.values()))

        return SerialExecutionResult(steps=tuple(steps), attempts=tuple(attempts.values()))

    @staticmethod
    def _blocks_serial_progress(attempt: Attempt) -> bool:
        return (
            attempt.state
            in {
                AttemptState.INTENT_RECORDED,
                AttemptState.STARTING,
                AttemptState.RUNNING,
                AttemptState.STOP_REQUESTED,
                AttemptState.COLLECTING,
                AttemptState.UNKNOWN,
            }
            or attempt.state is AttemptState.PENDING_VERIFICATION
            and not has_verified_exit(attempt)
        )

    def execute_attempt(
        self,
        attempt: Attempt,
        request: ExecutionRequest,
        *,
        max_polls: int | None = None,
    ) -> Attempt:
        if max_polls is not None and max_polls < 1:
            raise ValueError("max_polls must be positive")
        prepared = self._prepare_attempt(attempt, request)
        current = self.start_attempt(prepared, request)
        if current.execution_handle_ref is None or current.state in {
            AttemptState.COMPLETED,
            AttemptState.CANCELLED,
            AttemptState.EXECUTION_ERROR,
        }:
            return current
        polls = 0
        while max_polls is None or polls < max_polls:
            polls += 1
            inspection = self.inspect_attempt(current)
            if inspection.state is ExecutionInspectionState.RUNNING:
                if polls % 100 == 0:
                    self._persist_checkpoint(
                        current,
                        stage="running",
                        project_id=request.project_id,
                    )
                if self._poll_interval_seconds:
                    time.sleep(self._poll_interval_seconds)
                continue
            try:
                collection = self.collect_attempt(current, current.output_cursors or None)
                completed = self._apply_collection(current, inspection, collection)
            except _ExecutionObservationMismatch as error:
                completed = replace(
                    current,
                    state=AttemptState.PENDING_VERIFICATION,
                    capture_completeness=CaptureCompleteness.GAP,
                    unknown_reason_ref=error.reason,
                )
            self._persist_checkpoint(
                completed,
                stage=completed.state.value,
                project_id=request.project_id,
            )
            self._intent_claims[(request.project_id, request.intent_id)] = completed
            return completed
        pending = replace(
            current,
            state=AttemptState.RUNNING,
            unknown_reason_ref=None,
        )
        self._persist_checkpoint(
            pending,
            stage="poll_limit_reached",
            project_id=request.project_id,
        )
        self._intent_claims[(request.project_id, request.intent_id)] = pending
        return pending

    def _prepare_attempt(self, attempt: Attempt, request: ExecutionRequest) -> Attempt:
        self._require_attempt_identity(attempt, request)
        if attempt.intent_id and attempt.intent_id != request.intent_id:
            raise ValueError("attempt intent_id does not match execution request")
        if attempt.resolved_input_digest != request.resolved_input_digest:
            raise ValueError("attempt resolved input digest does not match request")
        if attempt.source_binding_digest != request.source_binding_digest:
            raise ValueError("attempt source binding digest does not match request")
        if attempt.side_effect_class != request.side_effect_class:
            raise ValueError("attempt side effect class does not match request")
        if attempt.adapter_kind != request.registered_entry.adapter_kind:
            raise ValueError("attempt adapter kind does not match request")
        if attempt.timeout_ms is not None and attempt.timeout_ms != request.timeout_ms:
            raise ValueError("attempt timeout does not match request")
        if attempt.authorization_ref is not None and authorization_action_basis(
            attempt.authorization_ref
        ) != authorization_action_basis(request.authorization_ref):
            raise ValueError("attempt authorization does not match execution request")
        if (
            attempt.expected_plan_revision_ref is not None
            and request.expected_plan_revision_ref is not None
            and attempt.expected_plan_revision_ref != request.expected_plan_revision_ref
        ):
            raise ValueError("attempt expected plan revision does not match request")
        return replace(
            attempt,
            intent_id=request.intent_id,
            resolved_input_digest=request.resolved_input_digest,
            source_binding_digest=request.source_binding_digest,
            authorization_ref=request.authorization_ref,
            expected_plan_revision_ref=(
                request.expected_plan_revision_ref
                if request.expected_plan_revision_ref is not None
                else (
                    attempt.expected_plan_revision_ref
                    or request.authorization_ref.plan_revision_ref
                )
            ),
            timeout_ms=request.timeout_ms,
        )

    def _validate_start(self, attempt: Attempt, request: ExecutionRequest) -> None:
        authorization = request.authorization_ref
        if attempt.intent_id and attempt.intent_id != request.intent_id:
            raise ValueError("attempt intent_id does not match request")
        if authorization.intent_id != request.intent_id:
            raise ValueError("authorization intent_id does not match request")
        if authorization.step_id != request.step_id:
            raise ValueError("authorization step_id does not match request")
        if authorization.step_revision_ref is None:
            raise ValueError("authorization step revision is unverified; confirm the current step")
        if authorization.step_revision_ref != attempt.step_revision_ref:
            raise ValueError("authorization step revision does not match attempt")
        if authorization.resolved_input_digest != request.resolved_input_digest:
            raise ValueError("authorization input digest does not match request")
        if (
            authorization.consumed_by_attempt_id is not None
            and authorization.consumed_by_attempt_id != request.attempt_id
        ):
            raise ValueError("authorization is already consumed by another attempt")
        expected_plan_revision = (
            request.expected_plan_revision_ref or attempt.expected_plan_revision_ref
        )
        if (
            expected_plan_revision is not None
            and authorization.plan_revision_ref != expected_plan_revision
        ):
            raise ValueError("authorization plan revision does not match frozen plan")
        if (
            attempt.expected_plan_revision_ref is not None
            and request.expected_plan_revision_ref is not None
            and attempt.expected_plan_revision_ref != request.expected_plan_revision_ref
        ):
            raise ValueError("attempt expected plan revision does not match request")
        if attempt.resolved_input_digest != request.resolved_input_digest:
            raise ValueError("attempt resolved input digest does not match request")
        if attempt.source_binding_digest != request.source_binding_digest:
            raise ValueError("attempt source binding digest does not match request")
        if self._start_validator is not None:
            self._start_validator.validate(attempt, request)

    def _find_intent_claim(self, intent_id: str, project_id: str) -> Attempt | None:
        claims: list[Attempt] = []
        in_memory = self._intent_claims.get((project_id, intent_id))
        if in_memory is not None:
            claims.append(in_memory)
        if self._checkpoint_store is not None:
            for record in self._checkpoint_store.scan():
                if record.attempt.intent_id != intent_id:
                    continue
                if record.project_id is None:
                    raise ValueError("legacy execution intent project is unverified; do not replay")
                if record.project_id == project_id:
                    claims.append(record.attempt)
        unique = {claim.attempt_id: claim for claim in claims}
        if len(unique) > 1:
            raise ValueError("intent_id has multiple persisted attempts")
        return next(iter(unique.values()), None)

    def start_attempt(self, attempt: Attempt, request: ExecutionRequest) -> Attempt:
        prepared = self._prepare_attempt(attempt, request)
        if self._commit_coordinator is None:
            raise ValueError("new start or replay requires a persistent authorization coordinator")
        coordinator = self._commit_coordinator
        fingerprint = _start_fingerprint(prepared, request)
        prepared = replace(prepared, intent_digest=fingerprint)
        key = (request.project_id, request.intent_id)
        previous_fingerprint = self._intent_fingerprints.get(key)
        if previous_fingerprint is not None and previous_fingerprint != fingerprint:
            raise ValueError("execution intent conflicts with different input")
        existing = self._find_intent_claim(request.intent_id, request.project_id)
        authoritative = coordinator.find_start(
            project_id=request.project_id,
            intent_id=request.intent_id,
            fingerprint=fingerprint,
        )
        if authoritative is not None:
            existing = authoritative
        elif existing is not None:
            raise ValueError("checkpoint has no authoritative start claim; inspect it first")
        if existing is not None:
            self._prepare_attempt(existing, request)
            if existing.attempt_id != prepared.attempt_id:
                raise ValueError("intent_id is already claimed by another attempt")
            if existing.intent_digest and existing.intent_digest != fingerprint:
                raise ValueError("execution intent conflicts with different input")
            if existing.execution_handle_ref is None:
                return replace(
                    existing,
                    state=AttemptState.PENDING_VERIFICATION,
                    unknown_reason_ref="start_intent_without_confirmed_handle",
                )
            if not existing.intent_digest:
                raise ValueError("legacy execution fingerprint is unverified; inspect it first")
            return existing
        self._validate_start(prepared, request)
        if prepared.state is not AttemptState.INTENT_RECORDED or (
            prepared.execution_handle_ref is not None
        ):
            raise ValueError("new execution requires a fresh intent-recorded attempt")
        authorization_id = request.authorization_ref.authorization_id
        authorized_attempt = self._authorization_claims.get(authorization_id)
        if authorized_attempt is not None and authorized_attempt != (
            request.project_id,
            prepared.attempt_id,
        ):
            raise ValueError("authorization is already consumed by another attempt")
        existing = coordinator.claim_start(
            project_id=request.project_id,
            intent_id=request.intent_id,
            fingerprint=fingerprint,
            checkpoint=self._checkpoint_record(prepared, stage="intent_recorded"),
        )
        if existing is not None:
            if existing.execution_handle_ref is None:
                return replace(
                    existing,
                    state=AttemptState.PENDING_VERIFICATION,
                    unknown_reason_ref="start_intent_without_confirmed_handle",
                )
            return existing
        self._authorization_claims[authorization_id] = (request.project_id, prepared.attempt_id)
        self._revoke_reuse_for_new_attempt(prepared)
        self._intent_claims[key] = prepared
        self._intent_fingerprints[key] = fingerprint
        handle = self._execution_port.start(request)
        started = replace(
            prepared,
            state=AttemptState.RUNNING,
            execution_handle_ref=handle,
        )
        self._intent_claims[key] = started
        self._persist_checkpoint(started, stage="started", project_id=request.project_id)
        return started

    def inspect_attempt(self, attempt: Attempt) -> ExecutionInspectionResult:
        handle = self._require_handle(attempt)
        result = self._execution_port.inspect(handle)
        if result.handle_id != handle.handle_id or result.identity_matches is not True:
            return replace(
                result,
                handle_id=handle.handle_id,
                state=ExecutionInspectionState.UNKNOWN,
                identity_matches=False,
                stop_confirmed=False,
                unknown_reason=result.unknown_reason or "inspection_identity_unverified",
            )
        return result

    def collect_attempt(
        self,
        attempt: Attempt,
        cursors: tuple[OutputCursor, ...] | None = None,
    ) -> ExecutionCollectionResult:
        result = self._execution_port.collect(self._require_handle(attempt), cursors)
        _validate_collection_identity(attempt, result)
        return result

    def request_stop(self, attempt: Attempt) -> StopRequestResult:
        return self._execution_port.request_stop(self._require_handle(attempt))

    def observe_saved_attempt(
        self,
        attempt: Attempt,
        *,
        project_id: str,
        inspection: ExecutionInspectionResult | None = None,
    ) -> Attempt:
        """Advance one original observation; never start or replay tested business."""
        if self._commit_coordinator is None:
            raise ValueError("saved observation requires an authoritative coordinator")
        saved = self._commit_coordinator.find_start(
            project_id=project_id, intent_id=attempt.intent_id, fingerprint=attempt.intent_digest
        )
        if saved != attempt:
            raise ValueError("observation target differs from its original saved execution")
        if has_reliable_terminal_fact(saved) or saved.execution_handle_ref is None:
            return saved
        inspection = inspection or self.inspect_attempt(saved)
        if (
            inspection.handle_id != saved.execution_handle_ref.handle_id
            or inspection.identity_matches is not True
        ):
            inspection = replace(
                inspection,
                handle_id=saved.execution_handle_ref.handle_id,
                state=ExecutionInspectionState.UNKNOWN,
                identity_matches=False,
                stop_confirmed=False,
            )
        if inspection.state is ExecutionInspectionState.RUNNING:
            return saved
        try:
            collection = self.collect_attempt(saved, saved.output_cursors or None)
            updated = self._apply_collection(saved, inspection, collection)
        except _ExecutionObservationMismatch as error:
            updated = replace(
                saved,
                state=AttemptState.PENDING_VERIFICATION,
                capture_completeness=CaptureCompleteness.GAP,
                unknown_reason_ref=error.reason,
            )
        if saved.state is AttemptState.INVALIDATED:
            updated = replace(updated, state=AttemptState.INVALIDATED)
        if updated != saved:
            self._persist_checkpoint(updated, stage=updated.state.value, project_id=project_id)
        return updated

    def recover_pending(self) -> tuple[RecoveryResult, ...]:
        if self._checkpoint_store is None or self._spool_store is None:
            return ()
        results: list[RecoveryResult] = []
        for record in self._checkpoint_store.scan():
            if self._commit_coordinator is not None and record.project_id is None:
                raise ValueError("legacy checkpoint project must be verified before recovery")
            if self._commit_coordinator is not None:
                assert record.project_id is not None
                record = self._commit_coordinator.read_checkpoint(
                    project_id=record.project_id,
                    attempt_id=record.attempt.attempt_id,
                )
            recovered_attempt = restore_checkpoint_attempt(record.checkpoint, record.attempt)
            reliable_terminal = has_reliable_terminal_fact(recovered_attempt)
            inspection = None
            if not reliable_terminal and recovered_attempt.execution_handle_ref is not None:
                try:
                    inspection = self.inspect_attempt(recovered_attempt)
                except (KeyError, ValueError):
                    inspection = None
            result = recover_attempt(
                record.checkpoint,
                record.attempt,
                self._spool_store,
                inspection=inspection,
            )
            results.append(result)
            if (
                result.action is RecoveryAction.TERMINAL_PRESERVED
                and result.attempt == record.attempt
            ):
                # Re-reading reliable history is not a business mutation. Republishing
                # an unchanged checkpoint would clear its saved run result/grade.
                continue
            self._persist_checkpoint(
                result.attempt, stage=result.action.value, project_id=record.project_id
            )
        return tuple(results)

    def invalidate_dependencies(
        self,
        attempts: Sequence[Attempt],
        *,
        previous_plan_revision: PlanRevisionRef,
        current_plan_revision: PlanRevisionRef,
        affected_upstream_attempt_ids: Sequence[str],
    ) -> tuple[Attempt, ...]:
        invalidations = invalidate_downstream_attempts(
            attempts,
            previous_plan_revision=previous_plan_revision,
            current_plan_revision=current_plan_revision,
            affected_upstream_attempt_ids=affected_upstream_attempt_ids,
        )
        by_id = {item.attempt.attempt_id: item.attempt for item in invalidations}
        for attempt in by_id.values():
            self._persist_checkpoint(attempt, stage=attempt.state.value)
        return tuple(by_id.get(attempt.attempt_id, attempt) for attempt in attempts)

    def invalidate_reuse(
        self,
        bases: Sequence[CaseReuseBasis],
        *,
        affected_upstream_attempt_ids: Sequence[str],
    ) -> tuple[CaseReuseInvalidation, ...]:
        return invalidate_reuse_bases(
            bases,
            affected_upstream_attempt_ids=affected_upstream_attempt_ids,
        )

    @property
    def reuse_invalidations(self) -> tuple[CaseReuseInvalidation, ...]:
        return tuple(self._reuse_invalidations)

    def _revoke_reuse_for_new_attempt(self, attempt: Attempt) -> None:
        previous = self._previous_attempt_ids_by_step.get(attempt.step_id, ())
        if not previous or not self._reuse_bases:
            return
        invalidations = invalidate_reuse_bases(
            self._reuse_bases,
            affected_upstream_attempt_ids=previous,
        )
        known = {item.case_id for item in self._reuse_invalidations}
        self._reuse_invalidations.extend(
            item for item in invalidations if item.case_id not in known
        )

    def _persist_checkpoint(
        self,
        attempt: Attempt,
        *,
        stage: str,
        project_id: str | None = None,
    ) -> None:
        record = replace(self._checkpoint_record(attempt, stage=stage), project_id=project_id)
        # 权威事务先提交，单文件检查点只是恢复投影，不能先于失败的业务提交。
        if self._commit_coordinator is not None and project_id is not None:
            self._commit_coordinator.commit_checkpoint(project_id=project_id, checkpoint=record)
        if self._checkpoint_store is not None:
            self._checkpoint_store.persist(record)

    @staticmethod
    def _checkpoint_record(attempt: Attempt, *, stage: str) -> RecoveryRecord:
        checkpoint = RecoveryCheckpoint(
            run_id=attempt.run_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            last_committed_stage=stage,
            output_cursors=attempt.output_cursors,
            output_block_refs=attempt.output_block_refs,
            resolved_input_digest=attempt.resolved_input_digest,
            side_effect_class=attempt.side_effect_class,
            execution_handle_ref=attempt.execution_handle_ref,
        )
        return RecoveryRecord(checkpoint=checkpoint, attempt=attempt)

    def _apply_collection(
        self,
        attempt: Attempt,
        inspection: ExecutionInspectionResult,
        collection: ExecutionCollectionResult,
    ) -> Attempt:
        handle = self._require_handle(attempt)
        if inspection.handle_id != handle.handle_id or inspection.identity_matches is not True:
            raise _ExecutionObservationMismatch("inspection_identity_unverified")
        _validate_collection_identity(attempt, collection)
        output_blocks = self._merge_output_blocks(
            attempt.output_block_refs,
            collection.output_blocks,
        )
        self._require_saved_output_material(attempt, output_blocks)
        output_cursors = self._merge_output_cursors(
            attempt.output_cursors, collection.output_cursors
        )
        self._require_saved_output_cursors(attempt, attempt.output_cursors, output_blocks)
        if not collection.captured_blocks:
            self._require_saved_output_cursors(attempt, output_cursors, output_blocks)
        by_key = {(block.stream_name, block.block_index): block for block in output_blocks}
        for captured in collection.captured_blocks:
            prior = by_key.get((captured.stream_name, captured.block_index))
            if prior is not None and (
                prior.offset,
                prior.length,
                prior.digest,
                prior.complete,
                prior.capture_source,
                prior.redaction_summary_id,
            ) != (
                captured.offset,
                captured.length,
                captured.digest,
                captured.complete,
                captured.capture_source,
                captured.redaction_summary_id,
            ):
                raise _ExecutionObservationMismatch("output_block_conflict")
        if collection.captured_blocks:
            if self._spool_store is None:
                raise ValueError("captured blocks require a SpoolStore")
            manifest = self._spool_store.persist_blocks(collection.captured_blocks)
            if (manifest.run_id, manifest.step_id, manifest.attempt_id) != (
                attempt.run_id,
                attempt.step_id,
                attempt.attempt_id,
            ) or manifest.schema_version != "aitest.spool/1.0":
                raise _ExecutionObservationMismatch("spool_identity_unverified")
            _validate_collection_identity(
                attempt,
                replace(collection, output_blocks=manifest.blocks, output_cursors=manifest.cursors),
            )
            output_blocks = self._merge_output_blocks(output_blocks, manifest.blocks)
            output_cursors = self._merge_output_cursors(attempt.output_cursors, manifest.cursors)
            self._require_saved_output_material(attempt, output_blocks)
            self._require_saved_output_cursors(attempt, output_cursors, output_blocks)

        state = self._attempt_state_for(attempt, inspection, collection)
        result = replace(
            attempt,
            state=state,
            output_block_refs=output_blocks,
            output_cursors=output_cursors,
            structured_result_ref=collection.structured_result_ref,
            exit_fact_ref=collection.exit_fact_ref,
            capture_completeness=collection.capture_completeness,
            timed_out=bool(collection.exit_fact_ref and collection.exit_fact_ref.timed_out),
            error_ref=collection.error_ref,
            unknown_reason_ref=self._unknown_reason_for(inspection, collection, state),
        )
        if result.capture_completeness is CaptureCompleteness.COMPLETE:
            # Verify the reported final cursors as well as the saved ones. Persisting
            # captured bytes must not silently repair a false adapter completeness claim.
            reported_cursors = tuple(
                replace(cursor, durable=True)
                if collection.captured_blocks and type(cursor.durable) is bool
                else cursor
                for cursor in collection.output_cursors
            )
            # Memory capture reports non-durable positions. Only the actual spool
            # manifest above establishes durability after these bytes are saved.
            reason = None
            if not has_complete_capture(result) or not has_complete_capture(
                replace(result, output_cursors=reported_cursors)
            ):
                reason = "capture_totals_unverified"
            elif not self._complete_material_is_readable(result):
                reason = "capture_material_unverified"
            if reason is not None:
                result = replace(
                    result,
                    capture_completeness=CaptureCompleteness.GAP,
                    unknown_reason_ref=result.unknown_reason_ref or reason,
                )
        return result

    def _require_saved_output_material(
        self, attempt: Attempt, blocks: tuple[OutputBlockRef, ...]
    ) -> None:
        if not blocks:
            return
        if self._spool_store is None:
            raise _ExecutionObservationMismatch("output_material_reader_unavailable")
        try:
            manifest = self._spool_store.read_manifest(attempt.attempt_id)
            if not isinstance(manifest, SpoolManifest) or (
                manifest.run_id,
                manifest.step_id,
                manifest.attempt_id,
                manifest.schema_version,
            ) != (attempt.run_id, attempt.step_id, attempt.attempt_id, "aitest.spool/1.0"):
                raise ValueError("spool ownership differs from the original attempt")
            for block in blocks:
                if (
                    not isinstance(block.stream_name, OutputStreamName)
                    or any(
                        type(value) is not int
                        for value in (block.block_index, block.offset, block.length)
                    )
                    or type(block.complete) is not bool
                    or block not in manifest.blocks
                ):
                    raise ValueError("output reference differs from saved material")
                content = self._spool_store.read_block(block)
                if (
                    type(content) is not bytes
                    or len(content) != block.length
                    or "sha256:" + hashlib.sha256(content).hexdigest() != block.digest
                ):
                    raise ValueError("saved output content differs from its reference")
        except (OSError, ValueError) as error:
            raise _ExecutionObservationMismatch("output_material_unverified") from error

    @staticmethod
    def _require_saved_output_cursors(
        attempt: Attempt, cursors: tuple[OutputCursor, ...], blocks: tuple[OutputBlockRef, ...]
    ) -> None:
        try:
            require_saved_output_cursors(attempt.attempt_id, cursors, blocks)
        except ValueError as error:
            raise _ExecutionObservationMismatch("output_cursor_material_unverified") from error

    def _complete_material_is_readable(self, attempt: Attempt) -> bool:
        if self._spool_store is None:
            return not attempt.output_block_refs
        try:
            manifest = self._spool_store.read_manifest(attempt.attempt_id)
            if not isinstance(manifest, SpoolManifest) or (
                manifest.run_id,
                manifest.step_id,
                manifest.attempt_id,
                manifest.schema_version,
            ) != (attempt.run_id, attempt.step_id, attempt.attempt_id, "aitest.spool/1.0"):
                return False
            if set(manifest.blocks) != set(attempt.output_block_refs):
                return False
            if set(manifest.cursors) != set(attempt.output_cursors):
                return False
            for block in attempt.output_block_refs:
                content = self._spool_store.read_block(block)
                if (
                    type(content) is not bytes
                    or len(content) != block.length
                    or "sha256:" + hashlib.sha256(content).hexdigest() != block.digest
                ):
                    return False
        except FileNotFoundError:
            # An output-free memory execution need not create a spool manifest.
            return not attempt.output_block_refs
        except (OSError, ValueError):
            return False
        return True

    @staticmethod
    def _attempt_state_for(
        attempt: Attempt,
        inspection: ExecutionInspectionResult,
        collection: ExecutionCollectionResult,
    ) -> AttemptState:
        if collection.error_ref is not None:
            return AttemptState.EXECUTION_ERROR
        if inspection.state in {
            ExecutionInspectionState.LOST,
            ExecutionInspectionState.UNKNOWN,
        }:
            return AttemptState.PENDING_VERIFICATION
        if inspection.state is ExecutionInspectionState.STOPPED:
            return (
                AttemptState.CANCELLED
                if inspection.stop_confirmed is True
                and has_reliable_terminal_fact(
                    replace(
                        attempt,
                        state=AttemptState.CANCELLED,
                        exit_fact_ref=collection.exit_fact_ref,
                    )
                )
                else AttemptState.PENDING_VERIFICATION
            )
        if inspection.state is ExecutionInspectionState.EXITED:
            if collection.complete is not True or not has_reliable_terminal_fact(
                replace(
                    attempt,
                    state=AttemptState.COMPLETED,
                    exit_fact_ref=collection.exit_fact_ref,
                )
            ):
                return AttemptState.PENDING_VERIFICATION
            return AttemptState.COMPLETED
        return attempt.state

    @staticmethod
    def _unknown_reason_for(
        inspection: ExecutionInspectionResult,
        collection: ExecutionCollectionResult,
        state: AttemptState,
    ) -> str | None:
        if state is not AttemptState.PENDING_VERIFICATION:
            return None
        if inspection.unknown_reason is not None:
            return inspection.unknown_reason
        if inspection.state in {
            ExecutionInspectionState.LOST,
            ExecutionInspectionState.UNKNOWN,
        }:
            return "execution_state_unknown"
        if inspection.state is ExecutionInspectionState.STOPPED and not inspection.stop_confirmed:
            return "stop_confirmation_unavailable"
        if collection.exit_fact_ref is not None and collection.exit_fact_ref.timed_out:
            return "command_timeout"
        if collection.exit_fact_ref is None:
            return "exit_fact_unavailable"
        return "verification_inconclusive"

    @staticmethod
    def _merge_output_cursors(
        existing: tuple[OutputCursor, ...],
        incoming: tuple[OutputCursor, ...],
    ) -> tuple[OutputCursor, ...]:
        merged = {cursor.stream_name: cursor for cursor in existing}
        for cursor in incoming:
            prior = merged.get(cursor.stream_name)
            if prior is not None:
                if (
                    cursor.last_block_index < prior.last_block_index
                    and cursor.offset <= prior.offset
                ):
                    continue
                if (
                    cursor.last_block_index < prior.last_block_index
                    or cursor.offset < prior.offset
                    or cursor.last_block_index == prior.last_block_index
                    and (cursor.attempt_id, cursor.offset, cursor.last_committed_digest)
                    != (prior.attempt_id, prior.offset, prior.last_committed_digest)
                ):
                    raise _ExecutionObservationMismatch("output_cursor_conflict")
                if cursor.last_block_index == prior.last_block_index and prior.durable is True:
                    continue
            merged[cursor.stream_name] = cursor
        return tuple(merged[stream] for stream in sorted(merged, key=lambda item: item.value))

    @staticmethod
    def _merge_output_blocks(
        existing: tuple[OutputBlockRef, ...],
        incoming: tuple[OutputBlockRef, ...],
    ) -> tuple[OutputBlockRef, ...]:
        merged: dict[tuple[object, int], OutputBlockRef] = {}
        for block in (*existing, *incoming):
            key = (block.stream_name, block.block_index)
            if key in merged and merged[key] != block:
                raise _ExecutionObservationMismatch("output_block_conflict")
            merged[key] = block
        return tuple(merged.values())

    @staticmethod
    def _step_state_for_attempt(state: AttemptState) -> StepState:
        if state is AttemptState.COMPLETED:
            return StepState.COMPLETED
        if state is AttemptState.EXECUTION_ERROR:
            return StepState.EXECUTION_ERROR
        if state is AttemptState.CANCELLED:
            return StepState.CANCELLED
        if state is AttemptState.INVALIDATED:
            return StepState.INVALIDATED
        if state in {AttemptState.PENDING_VERIFICATION, AttemptState.UNKNOWN}:
            return StepState.PENDING_VERIFICATION
        return StepState.RUNNING

    @staticmethod
    def _index_by_step_id(steps: Sequence[Step]) -> dict[str, int]:
        return {step.step_id: index for index, step in enumerate(steps)}

    @staticmethod
    def _items_by_step_id(
        items: Sequence[SerialExecutionItem],
    ) -> dict[str, SerialExecutionItem]:
        by_step: dict[str, SerialExecutionItem] = {}
        for item in items:
            if (
                item.step.step_id != item.attempt.step_id
                or item.step.step_id != item.request.step_id
            ):
                raise ValueError("step, attempt and request step_id must match")
            if item.step.run_id != item.attempt.run_id or item.step.run_id != item.request.run_id:
                raise ValueError("step, attempt and request run_id must match")
            if item.step.step_id in by_step:
                raise ValueError(f"duplicate step_id: {item.step.step_id}")
            by_step[item.step.step_id] = item
        return by_step

    @staticmethod
    def _state_by_step_id(steps: Sequence[Step]) -> dict[str, StepState]:
        states: dict[str, StepState] = {}
        for step in steps:
            if step.step_id in states:
                raise ValueError(f"duplicate step_id: {step.step_id}")
            states[step.step_id] = step.state
        return states

    @staticmethod
    def _required_dependency_ids(step: Step) -> tuple[str, ...]:
        return tuple(
            edge.upstream_step_id
            for edge in step.dependency_edges
            if edge.downstream_step_id == step.step_id and edge.required
        )

    @staticmethod
    def _require_handle(attempt: Attempt) -> ExecutionHandle:
        if attempt.execution_handle_ref is None:
            raise ValueError("attempt has no execution handle")
        return attempt.execution_handle_ref

    @staticmethod
    def _require_attempt_identity(attempt: Attempt, request: ExecutionRequest) -> None:
        if (
            attempt.attempt_id != request.attempt_id
            or attempt.run_id != request.run_id
            or attempt.step_id != request.step_id
        ):
            raise ValueError("attempt and execution request identity must match")


def _validate_collection_identity(attempt: Attempt, result: ExecutionCollectionResult) -> None:
    handle = attempt.execution_handle_ref
    if (
        result.attempt_id != attempt.attempt_id
        or any(item.attempt_id != attempt.attempt_id for item in result.output_blocks)
        or any(item.attempt_id != attempt.attempt_id for item in result.output_cursors)
        or any(
            (item.run_id, item.step_id, item.attempt_id)
            != (attempt.run_id, attempt.step_id, attempt.attempt_id)
            for item in result.captured_blocks
        )
    ):
        raise _ExecutionObservationMismatch("collection_identity_unverified")
    exit_fact = result.exit_fact_ref
    if exit_fact is not None and (
        handle is None
        or exit_fact.attempt_id != attempt.attempt_id
        or exit_fact.process_start_identity != handle.process_start_identity
    ):
        raise _ExecutionObservationMismatch("exit_identity_unverified")


def _start_fingerprint(attempt: Attempt, request: ExecutionRequest) -> str:
    return execution_start_fingerprint(attempt, request)


__all__ = [
    "DispatchPlan",
    "SerialExecutionItem",
    "SerialExecutionResult",
    "SerialRunner",
    "StartValidationPort",
]
