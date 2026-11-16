"""检查点与证据的自管事务发布。"""
# mypy: disable-error-code="misc,attr-defined"


from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import TypeAdapter

from aitest.application.evidence.publication import (
    EvidencePublicationContext,
    EvidencePublisher,
)
from aitest.application.execution.commit_guards import (
    _admission_source_check_fact,
    _json_payload,
    _payload_digest,
)
from aitest.application.execution.commit_types import (
    ExecutionCommitBatch,
    ExecutionCommitResult,
)
from aitest.application.execution.current import project_current_update
from aitest.application.execution.facts import (
    _evidence_fact,
    _source_verification_fact,
)
from aitest.application.execution.runtime_source import publish_runtime_source
from aitest.application.ports import (
    CollectedRedactionSummary,
    ExactRecordStaging,
    ExecutionRedactionCollector,
    ExecutionSourceProof,
)
from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork
from aitest.contracts.execution_facts import (
    EvidenceFact,
    ExecutionFacts,
    SourceCheckFact,
)
from aitest.domain.evidence.evidence import (
    EvidenceRef,
)
from aitest.domain.execution.runs import (
    Attempt,
    RecoveryRecord,
)

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


if TYPE_CHECKING:
    from aitest.application.execution.commit import ExecutionCommitCoordinator


class CheckpointPublicationMixin:
    def commit_checkpoint(
        self: ExecutionCommitCoordinator,
        *,
        project_id: str,
        checkpoint: RecoveryRecord,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> ExecutionCommitResult:
        """Commit one start/control intent before any external side effect."""
        runtime_source = None
        runtime_evidence = None
        admission_source = None
        admission_identity = None
        source_at_start = getattr(self._execution_authorizations, "source_at_start", None)
        if checkpoint.attempt.authorization_ref is not None and callable(source_at_start):
            admission_source, admission_identity = cast(
                ExecutionSourceProof, self._execution_authorizations
            ).source_at_start(project_id=project_id, attempt=checkpoint.attempt)
        if checkpoint.attempt.runtime_source is not None:
            if self._source_objects is None or not callable(source_at_start):
                raise ValueError("runtime source requires original authority and permanent objects")
            if admission_source is None or admission_identity is None:
                raise ValueError("runtime source has no original source binding")
            runtime_source, runtime_evidence = publish_runtime_source(
                attempt=checkpoint.attempt,
                source=admission_source,
                identity=admission_identity,
                objects=self._source_objects,
            )
        references = (
            self._evidence_collector.collect(project_id, checkpoint)
            if self._evidence_collector is not None and checkpoint.attempt.output_block_refs
            else ()
        )
        summary_collector = (
            cast(ExecutionRedactionCollector, self._evidence_collector)
            if callable(getattr(self._evidence_collector, "redaction_materials", None))
            else None
        )
        materials = (
            summary_collector.redaction_materials(project_id, checkpoint, references)
            if summary_collector is not None and references
            else ()
        )
        begin = getattr(self._uow, "begin", None)
        if not callable(begin):
            raise ValueError("execution commit requires a begin-capable unit of work")
        begin(request_id or f"execution-{uuid4().hex}", project_id)
        try:
            self._validate_checkpoint_update(project_id, checkpoint)
            current = self.read_current_facts(
                project_id=project_id, run_id=checkpoint.attempt.run_id
            )
            if references and (
                current is None
                or not any(
                    fact.attempt_id == checkpoint.attempt.attempt_id for fact in current.attempts
                )
            ):
                raise ValueError("output evidence requires its saved execution snapshot")
            checkpoint_refs: dict[str, tuple[object, Mapping[str, object]]] = {}
            staged = self._stage_checkpoint_payload(
                checkpoint_refs=checkpoint_refs,
                aggregate_kind="execution_checkpoint",
                record_id=checkpoint.attempt.attempt_id,
                expected_revision=self._revision(
                    "execution_checkpoint",
                    checkpoint.attempt.attempt_id,
                    expected_revision,
                ),
                payload={
                    **(
                        self._checkpoint_store.to_payload(checkpoint)
                        if self._checkpoint_store is not None
                        else _json_payload(_CHECKPOINT_ADAPTER, checkpoint)
                    ),
                    "project_id": project_id,
                },
            )
            records = [staged]
            evidence_records, projected = self._stage_collected_evidence(
                project_id,
                checkpoint,
                references,
                materials,
            )
            records.extend(evidence_records)
            if runtime_evidence is not None:
                source_records, source_projected = self._stage_collected_evidence(
                    project_id,
                    checkpoint,
                    (runtime_evidence,),
                )
                records.extend(source_records)
                projected.extend(source_projected)
            facts = None
            is_current = (
                current is not None
                and current.current_attempt_by_step.get(checkpoint.attempt.step_id)
                == checkpoint.attempt.attempt_id
            )
            has_runtime_observation = runtime_source is not None and runtime_evidence is not None
            # A saved runtime observation is a fact of its attempt even when a newer
            # attempt has since become current; publish it instead of leaving the
            # downgraded admission probe as the only source-verification fact.
            if current is not None and (is_current or projected or has_runtime_observation):
                updated = current
                if is_current:
                    updated = project_current_update(
                        current,
                        checkpoint.attempt,
                        committed_at=datetime.now(UTC),
                    )
                by_id = {item.evidence_id: item for item in updated.evidence_refs}
                for item in projected:
                    old = by_id.get(item.evidence_id)
                    if (
                        not materials
                        and old is not None
                        and old.model_dump(exclude={"redaction_summary"})
                        == item.model_dump(exclude={"redaction_summary"})
                    ):
                        item = item.model_copy(update={"redaction_summary": old.redaction_summary})
                    by_id[item.evidence_id] = item
                updated = updated.model_copy(update={"evidence_refs": tuple(by_id.values())})
                if has_runtime_observation:
                    assert runtime_source is not None and runtime_evidence is not None
                    observed = _source_verification_fact(runtime_source)
                    if not any(
                        item.verification_id == observed.verification_id
                        for item in updated.source_verifications
                    ):
                        raise ValueError("actual source has no original admission observation")
                    check = SourceCheckFact(
                        check_result_id="runtime-load:" + checkpoint.attempt.attempt_id,
                        attempt_id=checkpoint.attempt.attempt_id,
                        check_type="load",
                        scope=runtime_source.materialized_snapshot_ref,
                        source_snapshot_ref=runtime_source.expected_source_content_digest,
                        environment_ref=updated.run.environment_ref,
                        rules_revision="python-script/1",
                        adapter_version=checkpoint.attempt.adapter_version,
                        failure_class="passed" if not observed.gap_ids else "tool_failure",
                        raw_output_evidence_ref=runtime_evidence.evidence_id,
                        evidence_refs=(runtime_evidence.evidence_id,),
                    )
                    updated = updated.model_copy(
                        update={
                            "source_verifications": tuple(
                                observed
                                if item.verification_id == observed.verification_id
                                else item
                                for item in updated.source_verifications
                            ),
                            "source_check_results": (
                                *tuple(
                                    item
                                    for item in updated.source_check_results
                                    if item.check_result_id != check.check_result_id
                                ),
                                check,
                            ),
                        }
                    )
                if admission_source is not None:
                    for result in admission_source.check_results:
                        check = _admission_source_check_fact(result, checkpoint.attempt.attempt_id)
                        previous_check = next(
                            (
                                item
                                for item in updated.source_check_results
                                if item.check_result_id == check.check_result_id
                            ),
                            None,
                        )
                        if previous_check is not None and previous_check != check:
                            raise ValueError("saved source check identity conflicts")
                        if previous_check is None:
                            updated = updated.model_copy(
                                update={
                                    "source_check_results": (*updated.source_check_results, check)
                                }
                            )
                snapshot_records, facts = self._stage_snapshot(
                    updated,
                    checkpoint_refs=checkpoint_refs,
                )
                records.extend(snapshot_records)
            if self._evidence_collector is not None and references:
                self._evidence_collector.validate(references)
            if summary_collector is not None and materials:
                summary_collector.validate_redaction_materials(materials)
            committed = self._uow.commit()
        except BaseException:
            self._uow.rollback()
            raise
        return ExecutionCommitResult(staged=tuple(records), committed=committed, facts=facts)

    def _stage_collected_evidence(
        self: ExecutionCommitCoordinator,
        project_id: str,
        checkpoint: RecoveryRecord,
        references: tuple[EvidenceRef, ...],
        materials: tuple[CollectedRedactionSummary, ...] = (),
    ) -> tuple[list[object], list[EvidenceFact]]:
        records: list[object] = []
        projected: list[EvidenceFact] = []
        if len({ref.evidence_id for ref in references}) != len(references):
            raise ValueError("collected output evidence has duplicate identities")
        summaries = {}
        ids = {ref.redaction_summary_ref for ref in references}
        for material in materials:
            if (
                material.summary_id in summaries
                or material.summary_id not in ids
                or (
                    material.payload.get("summary_id"),
                    material.payload.get("project_id"),
                    material.payload.get("run_id"),
                    material.payload.get("step_id"),
                    material.payload.get("attempt_id"),
                )
                != (
                    material.summary_id,
                    project_id,
                    checkpoint.attempt.run_id,
                    checkpoint.attempt.step_id,
                    checkpoint.attempt.attempt_id,
                )
            ):
                raise ValueError("collected redaction summary identity or ownership conflicts")
            current = self._revision("execution_redaction_summary", material.summary_id)
            if current == 1:
                if _payload_digest(
                    self._read_payload(
                        "execution_redaction_summary",
                        material.summary_id,
                    )
                    or {}
                ) != _payload_digest(material.payload):
                    raise ValueError("permanent redaction summary differs from its exact record")
            elif current == 0:
                if not callable(getattr(self._uow, "stage_record_exact", None)):
                    raise ValueError("redaction summary requires exact record staging")
                staged = cast(ExactRecordStaging, self._uow).stage_record_exact(
                    aggregate_kind="execution_redaction_summary",
                    record_id=material.summary_id,
                    expected_revision=0,
                    payload=material.payload,
                )
                if type(staged) is not int or staged != 1:
                    raise ValueError("redaction summary exact revision cannot be verified")
                records.append(staged)
            else:
                raise ValueError("permanent redaction summary is immutable at revision one")
            summaries[material.summary_id] = material.summary
        for ref in references:
            if (ref.project_id, ref.run_id, ref.step_id, ref.attempt_id) != (
                project_id,
                checkpoint.attempt.run_id,
                checkpoint.attempt.step_id,
                checkpoint.attempt.attempt_id,
            ):
                raise ValueError("collected output evidence belongs to another execution")
            current = self._revision("evidence_ref", ref.evidence_id)
            raw = _json_payload(_EVIDENCE_ADAPTER, ref)
            if current == ref.evidence_revision:
                if _payload_digest(self._read_payload("evidence_ref", ref.evidence_id) or {}) != (
                    _payload_digest(raw)
                ):
                    raise ValueError("collected output evidence differs from its saved revision")
            elif current is not None and ref.evidence_revision == current + 1:
                if not callable(getattr(self._uow, "stage_record_exact", None)):
                    raise ValueError("output evidence requires exact record staging")
                staged = cast(ExactRecordStaging, self._uow).stage_record_exact(
                    aggregate_kind="evidence_ref",
                    record_id=ref.evidence_id,
                    expected_revision=current,
                    payload=raw,
                )
                if type(staged) is not int or staged != ref.evidence_revision:
                    raise ValueError("output evidence assigned revision cannot be verified")
                records.append(staged)
            else:
                raise ValueError("output evidence warehouse revision changed")
            projected.append(_evidence_fact(ref, summaries))
        return records, projected

    def ensure_checkpoint_evidence(
        self: ExecutionCommitCoordinator, *, project_id: str, attempt: Attempt
    ) -> None:
        """Repair publication of the original terminal output without another execution."""
        if self._evidence_collector is None or not attempt.output_block_refs:
            return
        checkpoint = self.read_checkpoint(project_id=project_id, attempt_id=attempt.attempt_id)
        if checkpoint.attempt != attempt:
            raise ValueError("original evidence checkpoint changed before publication")
        references = self._evidence_collector.existing(project_id, checkpoint)
        current = self.read_current_facts(project_id=project_id, run_id=attempt.run_id)
        if references is not None and current is not None:
            existing = {item.evidence_id: item for item in current.evidence_refs}
            reader = getattr(self._evidence_collector, "redaction_materials", None)
            materials = reader(project_id, checkpoint, references) if callable(reader) else ()
            summaries = {item.summary_id: item.summary for item in materials}
            if all(
                existing.get(ref.evidence_id) is not None
                and existing[ref.evidence_id] == _evidence_fact(ref, summaries)
                for ref in references
            ):
                return
        self.commit_checkpoint(project_id=project_id, checkpoint=checkpoint)

    def publish_and_stage(
        self: ExecutionCommitCoordinator,
        *,
        checkpoint: RecoveryRecord,
        evidence_publisher: EvidencePublisher,
        evidence_context: EvidencePublicationContext,
        facts: ExecutionFacts,
        expected_revisions: Mapping[str, int] | None = None,
        request_id: str | None = None,
    ) -> ExecutionCommitResult:
        evidence_refs = evidence_publisher.publish_attempt(evidence_context)
        begin = getattr(self._uow, "begin", None)
        if not callable(begin):
            raise ValueError("execution commit requires a begin-capable unit of work")
        begin(request_id or f"execution-{uuid4().hex}", evidence_context.project_id)
        return self.stage_and_commit(
            ExecutionCommitBatch(
                checkpoint=checkpoint,
                evidence_refs=evidence_refs,
                facts=facts,
                expected_revisions=expected_revisions or {},
            )
        )
