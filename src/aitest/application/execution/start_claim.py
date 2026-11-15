"""启动认领：授权消费、检查点与来源物化。"""
# mypy: disable-error-code="misc,attr-defined"


from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import TypeAdapter

from aitest.application.execution.authorization_index import read_authorization_index
from aitest.application.execution.commit_guards import (
    _admission_source_check_fact,
    _authorization_record_id,
    _intent_record_id,
    _json_payload,
    _validate_original_grant_proof,
)
from aitest.application.execution.current import project_current_update
from aitest.application.execution.facts import (
    _source_verification_fact,
    project_attempt_fact,
)
from aitest.application.execution.facts import (
    validate_attempt_projection as _validate_attempt_projection,
)
from aitest.application.ports import (
    ExecutionSourceProof,
    RecordRepository,
)
from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork
from aitest.contracts.execution_facts import (
    ExecutionFacts,
    RunControlStateFact,
    StepStateFact,
)
from aitest.domain.evidence.evidence import (
    CodeIdentity,
    EvidenceCaptureSource,
    EvidenceIntegrity,
    EvidenceKind,
    EvidenceRef,
    RedactionState,
)
from aitest.domain.execution.dependencies import (
    AttemptInvalidation,
    invalidate_downstream_attempts,
)
from aitest.domain.execution.runs import (
    Attempt,
    AttemptState,
    PlanRevisionRef,
    RecoveryRecord,
    has_complete_capture,
    has_reliable_terminal_fact,
    has_verified_exit,
)
from aitest.domain.execution.sources import (
    ExecutionSourceVerification,
    SourceVerificationState,
)

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


if TYPE_CHECKING:
    from aitest.application.execution.commit import ExecutionCommitCoordinator


class StartClaimMixin:
    def claim_start(
        self: ExecutionCommitCoordinator,
        *,
        project_id: str,
        intent_id: str,
        fingerprint: str,
        checkpoint: RecoveryRecord,
        request_id: str | None = None,
    ) -> Attempt | None:
        """在同一短事务中认领意图和检查点；外部启动只允许新认领者执行。"""
        previous = self.find_start(
            project_id=project_id, intent_id=intent_id, fingerprint=fingerprint
        )
        if previous is not None:
            return previous
        if self._execution_authorizations is None:
            raise ValueError("new execution requires original authorization proof")
        # Source/environment checks are outside the start transaction. A concurrent
        # original publication is recovered, never converted into another execution.
        try:
            self._execution_authorizations.validate_new(
                project_id=project_id, attempt=checkpoint.attempt
            )
        except (OSError, ValueError, KeyError, TypeError):
            previous = self.find_start(
                project_id=project_id, intent_id=intent_id, fingerprint=fingerprint
            )
            if previous is None:
                raise
            return previous
        source_reader = getattr(self._execution_authorizations, "source_at_start", None)
        source, source_identity = (
            cast(ExecutionSourceProof, self._execution_authorizations).source_at_start(
                project_id=project_id, attempt=checkpoint.attempt
            )
            if callable(source_reader)
            else (None, None)
        )
        source_evidence = None
        if source is not None:
            observed_current, _ = self._prepare_current_start(project_id, checkpoint.attempt)
            if observed_current is None:
                raise ValueError("source observation requires its registered current run")
            source, source_evidence = self._source_material(
                source, observed_current, checkpoint.attempt, source_identity
            )
        begin = getattr(self._uow, "begin", None)
        if not callable(begin):
            raise ValueError("execution commit requires a begin-capable unit of work")
        begin(request_id or f"execution-{uuid4().hex}", project_id)
        try:
            previous = self.find_start(
                project_id=project_id,
                intent_id=intent_id,
                fingerprint=fingerprint,
            )
            if previous is not None:
                self._uow.rollback()
                return previous
            if self._revision("execution_checkpoint", checkpoint.attempt.attempt_id):
                raise ValueError(
                    "attempt already exists without this verified intent; inspect it first"
                )
            current, invalidations = self._prepare_current_start(project_id, checkpoint.attempt)
            if current is None or current.run.control_state not in {
                RunControlStateFact.NOT_STARTED,
                RunControlStateFact.RUNNING,
            }:
                raise ValueError("new execution requires an admitted current run control state")
            attempt = checkpoint.attempt
            if (
                checkpoint.project_id not in (None, project_id)
                or checkpoint.checkpoint.run_id != attempt.run_id
                or checkpoint.checkpoint.step_id != attempt.step_id
                or checkpoint.checkpoint.attempt_id != attempt.attempt_id
                or attempt.intent_id != intent_id
                or attempt.intent_digest != fingerprint
                or attempt.state is not AttemptState.INTENT_RECORDED
                or attempt.execution_handle_ref is not None
                or checkpoint.checkpoint.resolved_input_digest != attempt.resolved_input_digest
                or checkpoint.checkpoint.side_effect_class != attempt.side_effect_class
            ):
                raise ValueError("start checkpoint does not match the claimed execution basis")
            authorization = attempt.authorization_ref
            if authorization is None or (
                authorization.step_revision_ref != attempt.step_revision_ref
                or authorization.intent_id != intent_id
                or authorization.step_id != attempt.step_id
                or authorization.resolved_input_digest != attempt.resolved_input_digest
                or authorization.consumed_by_attempt_id not in (None, attempt.attempt_id)
                or authorization.plan_revision_ref != attempt.expected_plan_revision_ref
            ):
                raise ValueError("start authorization step revision cannot be verified")
            claim_id = _authorization_record_id(authorization.authorization_id)
            if self._revision("execution_authorization", claim_id):
                raise ValueError("authorization is already consumed; inspect the original attempt")
            superseded = {item.attempt.attempt_id for item in invalidations}
            previous_attempt_id = current.current_attempt_by_step[attempt.step_id]
            if previous_attempt_id is not None:
                superseded.add(previous_attempt_id)
            original_grant_proof = self._execution_authorizations.stage_occupation(
                project_id=project_id,
                attempt=attempt,
                superseded_attempt_ids=tuple(sorted(superseded)),
                changed_step_ids=tuple(
                    sorted(
                        {attempt.step_id}
                        | {
                            item.attempt.step_id
                            for item in invalidations
                            if current.current_attempt_by_step.get(item.attempt.step_id)
                            == item.attempt.attempt_id
                        }
                    )
                ),
            )
            _validate_original_grant_proof(original_grant_proof, authorization)
            self._uow.stage_record(
                aggregate_kind="execution_authorization",
                record_id=claim_id,
                expected_revision=self._revision("execution_authorization", claim_id),
                payload={
                    "schema_version": "aitest.execution-authorization-claim/1.1",
                    "project_id": project_id,
                    "run_id": attempt.run_id,
                    "step_id": attempt.step_id,
                    "attempt_id": attempt.attempt_id,
                    "intent_id": intent_id,
                    "fingerprint": fingerprint,
                    "authorization_id": authorization.authorization_id,
                    "authorization_ref": _json_payload(
                        TypeAdapter(type(authorization)), authorization
                    ),
                    "original_grant_proof": dict(original_grant_proof),
                },
            )
            if callable(getattr(self._records or self._uow, "current_revision", None)):
                self._uow.stage_record(
                    aggregate_kind="execution_intent",
                    record_id=_intent_record_id(project_id, intent_id),
                    expected_revision=0,
                    payload={
                        "project_id": project_id,
                        "intent_id": intent_id,
                        "fingerprint": fingerprint,
                        "attempt_id": checkpoint.attempt.attempt_id,
                    },
                )
            checkpoint_refs: dict[str, tuple[object, Mapping[str, object]]] = {}
            self._stage_checkpoint_payload(
                checkpoint_refs=checkpoint_refs,
                aggregate_kind="execution_checkpoint",
                record_id=checkpoint.attempt.attempt_id,
                expected_revision=self._revision(
                    "execution_checkpoint", checkpoint.attempt.attempt_id
                ),
                payload={
                    **_json_payload(_CHECKPOINT_ADAPTER, checkpoint),
                    "project_id": project_id,
                },
            )
            for invalidation in invalidations:
                previous_record = self._read_payload(
                    "execution_checkpoint", invalidation.attempt.attempt_id
                )
                if previous_record is None:
                    raise ValueError("dependency checkpoint is unavailable")
                old = _CHECKPOINT_ADAPTER.validate_python(previous_record)
                updated = RecoveryRecord(
                    checkpoint=old.checkpoint,
                    attempt=invalidation.attempt,
                    project_id=project_id,
                )
                self._stage_checkpoint_payload(
                    checkpoint_refs=checkpoint_refs,
                    aggregate_kind="execution_checkpoint",
                    record_id=invalidation.attempt.attempt_id,
                    expected_revision=self._revision(
                        "execution_checkpoint", invalidation.attempt.attempt_id
                    ),
                    payload=_json_payload(_CHECKPOINT_ADAPTER, updated),
                )
            if current is not None:
                updated_facts = project_current_update(
                    current,
                    checkpoint.attempt,
                    invalidations=invalidations,
                    committed_at=datetime.now(UTC),
                )
                if source is not None:
                    observed = _source_verification_fact(source)
                    previous_source = next(
                        (
                            item
                            for item in updated_facts.source_verifications
                            if item.verification_id == observed.verification_id
                        ),
                        None,
                    )
                    if previous_source is not None and previous_source != observed:
                        raise ValueError("saved source observation identity conflicts")
                    if previous_source is None:
                        updated_facts = updated_facts.model_copy(
                            update={
                                "source_verifications": (
                                    *updated_facts.source_verifications,
                                    observed,
                                )
                            }
                        )
                    for result in source.check_results:
                        check = _admission_source_check_fact(result, attempt.attempt_id)
                        previous_check = next(
                            (
                                item
                                for item in updated_facts.source_check_results
                                if item.check_result_id == check.check_result_id
                            ),
                            None,
                        )
                        if previous_check is not None and previous_check != check:
                            raise ValueError("saved source check identity conflicts")
                        if previous_check is None:
                            updated_facts = updated_facts.model_copy(
                                update={
                                    "source_check_results": (
                                        *updated_facts.source_check_results,
                                        check,
                                    )
                                }
                            )
                if source_evidence is not None:
                    _, projected_source = self._stage_collected_evidence(
                        project_id,
                        checkpoint,
                        (source_evidence,),
                    )
                    if any(
                        item.evidence_id == source_evidence.evidence_id
                        for item in updated_facts.evidence_refs
                    ):
                        raise ValueError("source evidence identity already belongs to another fact")
                    updated_facts = updated_facts.model_copy(
                        update={"evidence_refs": (*updated_facts.evidence_refs, *projected_source)}
                    )
                self._stage_snapshot(
                    updated_facts,
                    allow_current_change=True,
                    checkpoint_refs=checkpoint_refs,
                )
            self._uow.commit()
            return None
        except BaseException:
            self._uow.rollback()
            raise

    def _source_material(
        self: ExecutionCommitCoordinator,
        source: ExecutionSourceVerification | None,
        facts: ExecutionFacts,
        attempt: Attempt,
        code_identity: CodeIdentity | None = None,
    ) -> tuple[ExecutionSourceVerification | None, EvidenceRef | None]:
        if source is None:
            return None, None
        reference = source.raw_output_object
        if reference is None or not source.source_instance_id or code_identity is None:
            return replace(
                source,
                state=SourceVerificationState.UNVERIFIED,
                gap_ids=(*source.gap_ids, "source_probe_material_unavailable"),
                evidence_refs=(),
            ), None
        if (
            self._source_objects is None
            or type(reference.size) is not int
            or reference.size <= 0
            or reference.size > 8 * 1024 * 1024
            or reference.project_id != facts.project_id
            or code_identity.workspace_ref != facts.run.origin_workspace_id
            or source.evidence_refs != (reference.relative_path,)
        ):
            raise ValueError("source probe material scope or verified read budget differs")
        try:
            content = self._source_objects.read_bytes(reference)
        except (OSError, ValueError, RuntimeError) as error:
            raise ValueError("source probe material cannot be reliably read") from error
        if (
            not isinstance(content, bytes)
            or len(content) != reference.size
            or "sha256:" + hashlib.sha256(content).hexdigest() != reference.digest
        ):
            raise ValueError("source probe material differs from its saved object")
        evidence = EvidenceRef(
            evidence_id="source-evidence-" + attempt.attempt_id,
            project_id=facts.project_id,
            source_instance_id=source.source_instance_id,
            run_id=facts.run_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            evidence_kind=EvidenceKind.SOURCE_CHECK,
            capture_source=EvidenceCaptureSource.PLUGIN_RUNTIME,
            object_digest=reference.digest,
            object_size=reference.size,
            code_identity=code_identity,
            integrity=EvidenceIntegrity.COMPLETE,
            redaction_state=RedactionState.NOT_REQUIRED,
            media_type=reference.media_type,
            created_at=source.verified_at,
        )
        # A saved admission probe precedes this execution. Keep its material,
        # but it cannot certify the business process's eventual module loading.
        return replace(
            source,
            evidence_refs=(evidence.evidence_id,),
            state=(
                SourceVerificationState.UNVERIFIED
                if source.state is SourceVerificationState.VERIFIED
                else source.state
            ),
            gap_ids=tuple(
                dict.fromkeys(
                    (
                        *source.gap_ids,
                        "actual_execution_load_source_unverified",
                    )
                )
            ),
        ), evidence

    def _prepare_current_start(
        self: ExecutionCommitCoordinator,
        project_id: str,
        attempt: Attempt,
    ) -> tuple[ExecutionFacts | None, tuple[AttemptInvalidation, ...]]:
        current = self.read_current_facts(project_id=project_id, run_id=attempt.run_id)
        if current is None:
            if attempt.consumed_outputs or attempt.consumed_conditions:
                raise ValueError("actual dependency basis needs a registered current run")
            return None, ()
        step = next((item for item in current.steps if item.step_id == attempt.step_id), None)
        projected = project_attempt_fact(attempt, is_current=True)
        if step is None or step.step_revision_ref != projected.step_revision_ref:
            raise ValueError("new attempt does not use the registered current step revision")
        plan = PlanRevisionRef(**current.plan_revision.model_dump())
        if attempt.expected_plan_revision_ref != plan or (
            current.run.source_binding_digest is not None
            and attempt.source_binding_digest != current.run.source_binding_digest
        ):
            raise ValueError("new attempt does not use the registered frozen run basis")
        domain: list[Attempt] = []
        for fact in current.attempts:
            payload = self._read_payload("execution_checkpoint", fact.attempt_id)
            if payload is None or payload.get("project_id") != project_id:
                raise ValueError("current dependency checkpoint is unavailable or foreign")
            saved = _CHECKPOINT_ADAPTER.validate_python(payload).attempt
            _validate_attempt_projection(saved, fact)
            domain.append(saved)
        if self._serial_execution:
            self._require_serial_start(current, attempt, tuple(domain))
        previous_id = current.current_attempt_by_step[attempt.step_id]
        old = next((item for item in domain if item.attempt_id == previous_id), None)
        expected_index = old.attempt_index + 1 if old is not None else 1
        if attempt.attempt_index != expected_index:
            raise ValueError("new attempt index must follow the current attempt")
        current_ids = {
            item.attempt_id: item
            for item in domain
            if current.current_attempt_by_step.get(item.step_id) == item.attempt_id
        }
        consumed_ids = {item.upstream_attempt_id for item in attempt.consumed_outputs} | {
            item.upstream_attempt_id for item in attempt.consumed_conditions
        }
        for upstream_id in consumed_ids:
            upstream = current_ids.get(upstream_id)
            if (
                upstream is None
                or upstream.step_id == attempt.step_id
                or upstream.state is not AttemptState.COMPLETED
            ):
                raise ValueError("actual dependency does not use a valid current upstream attempt")
        invalidations = invalidate_downstream_attempts(
            domain,
            previous_plan_revision=plan,
            current_plan_revision=plan,
            affected_upstream_attempt_ids=(previous_id,) if previous_id else (),
        )
        affected_ids = {item.attempt.attempt_id for item in invalidations}
        if consumed_ids & affected_ids:
            raise ValueError(
                "new attempt consumes a downstream basis invalidated by its replacement"
            )
        affected = [item for item in domain if item.attempt_id in affected_ids]
        if old is not None:
            affected.append(old)
        if any(
            item.state
            not in {
                AttemptState.COMPLETED,
                AttemptState.CANCELLED,
                AttemptState.EXECUTION_ERROR,
                AttemptState.INVALIDATED,
            }
            for item in affected
        ):
            raise ValueError("affected execution may be active; stop and verify before replacement")
        if any(
            item.execution_handle_ref is not None and not has_verified_exit(item)
            for item in affected
        ):
            raise ValueError(
                "affected handle termination is uncertain; stop and verify before replacement"
            )
        return current, invalidations

    @staticmethod
    def _require_serial_start(
        current: ExecutionFacts, attempt: Attempt, saved: tuple[Attempt, ...]
    ) -> None:
        """Called under the start transaction after exact checkpoint projection checks."""
        terminal = {
            AttemptState.COMPLETED,
            AttemptState.CANCELLED,
            AttemptState.EXECUTION_ERROR,
            AttemptState.INVALIDATED,
        }
        for old in saved:
            if (
                old.state not in terminal
                and not (old.state is AttemptState.PENDING_VERIFICATION and has_verified_exit(old))
            ) or (old.execution_handle_ref is not None and not has_verified_exit(old)):
                raise ValueError("serial execution has an active or unverified prior attempt")
        step = next(item for item in current.steps if item.step_id == attempt.step_id)
        by_step = {item.step_id: item for item in current.steps}
        by_attempt = {item.attempt_id: item for item in saved}
        for identity in step.dependency_step_ids:
            upstream = by_step.get(identity)
            basis = (
                by_attempt.get(upstream.current_attempt_id)
                if upstream is not None and upstream.current_attempt_id is not None
                else None
            )
            if (
                upstream is None
                or upstream.state is not StepStateFact.COMPLETED
                or basis is None
                or basis.state is not AttemptState.COMPLETED
                or not has_reliable_terminal_fact(basis)
                or not has_complete_capture(basis)
            ):
                raise ValueError(
                    "serial execution requires completed, captured current dependencies"
                )

    def _stage_authorization_revocations(
        self: ExecutionCommitCoordinator,
        *,
        before: ExecutionFacts,
        superseded_attempt_ids: tuple[str, ...],
        changed_step_ids: tuple[str, ...],
    ) -> None:
        if not superseded_attempt_ids and not changed_step_ids:
            return
        if self._execution_authorizations is None:
            _revision, unused = read_authorization_index(
                cast(RecordRepository, self._records or self._uow),
                before.run.origin_workspace_id,
                before.project_id,
                before.run_id,
            )
            if unused:
                raise ValueError("runtime revision needs the unused authorization consumer")
            return
        self._execution_authorizations.stage_revoke_affected(
            project_id=before.project_id,
            run_id=before.run_id,
            superseded_attempt_ids=superseded_attempt_ids,
            changed_step_ids=changed_step_ids,
        )
