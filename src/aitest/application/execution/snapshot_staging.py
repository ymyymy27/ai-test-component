"""调用方事务内的暂存与提交。"""
# mypy: disable-error-code="misc,attr-defined"


from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from aitest.application.execution.commit_guards import (
    _json_payload,
    _validate_current_facts,
    _validate_frozen_run_basis,
    _validate_frozen_step_basis,
    _validate_publication_current,
)
from aitest.application.execution.commit_types import (
    ExecutionCommitBatch,
    ExecutionCommitResult,
)
from aitest.application.execution.facts import (
    validate_attempt_projection as _validate_attempt_projection,
)
from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork
from aitest.contracts.execution_facts import (
    ExecutionFacts,
)
from aitest.domain.evidence.evidence import (
    EvidenceRef,
)
from aitest.domain.execution.runs import (
    RecoveryRecord,
)

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


if TYPE_CHECKING:
    from aitest.application.execution.commit import ExecutionCommitCoordinator


class SnapshotStagingMixin:
    def stage(
        self: ExecutionCommitCoordinator,
        batch: ExecutionCommitBatch,
    ) -> tuple[object, ...]:
        staged, _facts = self._stage_with_facts(batch)
        return staged

    def _stage_with_facts(
        self: ExecutionCommitCoordinator,
        batch: ExecutionCommitBatch,
    ) -> tuple[tuple[object, ...], ExecutionFacts]:
        facts = batch.facts
        _validate_current_facts(facts)
        if batch.checkpoint.attempt.run_id != facts.run_id:
            raise ValueError("checkpoint and ExecutionFacts must share run_id")
        if batch.checkpoint.attempt.attempt_id == "":
            raise ValueError("checkpoint requires an attempt_id")
        previous = self.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
        if previous is not None:
            _validate_publication_current(previous, facts)
            _validate_frozen_run_basis(previous, facts)
            _validate_frozen_step_basis(previous, facts)

        attempt = batch.checkpoint.attempt
        self._validate_checkpoint_update(facts.project_id, batch.checkpoint)
        if callable(getattr(self._records or self._uow, "current_revision", None)):
            current = [item for item in facts.attempts if item.attempt_id == attempt.attempt_id]
            if len(current) != 1:
                raise ValueError("ExecutionFacts do not match the authoritative checkpoint")
            _validate_attempt_projection(attempt, current[0])
            # Other current steps must be backed by their saved checkpoints,
            # rather than allowing the publisher to declare arbitrary completion.
            for item in facts.attempts:
                if not item.is_current or item.attempt_id == attempt.attempt_id:
                    continue
                payload = self._read_payload("execution_checkpoint", item.attempt_id)
                if payload is None or payload.get("project_id") != facts.project_id:
                    raise ValueError("current attempt checkpoint is unavailable or foreign")
                _validate_attempt_projection(
                    _CHECKPOINT_ADAPTER.validate_python(payload).attempt, item
                )
        for evidence in batch.evidence_refs:
            if (
                evidence.project_id != facts.project_id
                or evidence.run_id != facts.run_id
                or evidence.attempt_id != attempt.attempt_id
                or evidence.step_id != attempt.step_id
            ):
                raise ValueError("evidence and checkpoint must share project/run/step/attempt")

        staged: list[object] = []
        checkpoint_refs: dict[str, tuple[object, Mapping[str, object]]] = {}
        staged.append(
            self._stage_checkpoint_payload(
                checkpoint_refs=checkpoint_refs,
                aggregate_kind="execution_checkpoint",
                record_id=batch.checkpoint.attempt.attempt_id,
                expected_revision=self._revision(
                    "execution_checkpoint",
                    attempt.attempt_id,
                    batch.expected_revisions.get(f"execution_checkpoint:{attempt.attempt_id}"),
                ),
                payload={
                    **(
                        self._checkpoint_store.to_payload(batch.checkpoint)
                        if self._checkpoint_store is not None
                        else _json_payload(_CHECKPOINT_ADAPTER, batch.checkpoint)
                    ),
                    "project_id": facts.project_id,
                },
            )
        )
        for evidence in batch.evidence_refs:
            if evidence.run_id != facts.run_id:
                raise ValueError("evidence and ExecutionFacts must share run_id")
            staged.append(
                self._uow.stage_record(
                    aggregate_kind="evidence_ref",
                    record_id=evidence.evidence_id,
                    expected_revision=self._revision(
                        "evidence_ref",
                        evidence.evidence_id,
                        batch.expected_revisions.get(f"evidence_ref:{evidence.evidence_id}"),
                    ),
                    payload=_json_payload(_EVIDENCE_ADAPTER, evidence),
                )
            )
        snapshot_records, facts = self._stage_snapshot(
            facts, expected_revisions=batch.expected_revisions, checkpoint_refs=checkpoint_refs
        )
        staged.extend(snapshot_records)
        return tuple(staged), facts

    def stage_and_commit(
        self: ExecutionCommitCoordinator,
        batch: ExecutionCommitBatch,
    ) -> ExecutionCommitResult:
        try:
            staged, facts = self._stage_with_facts(batch)
            return ExecutionCommitResult(staged=staged, committed=self._uow.commit(), facts=facts)
        except BaseException:
            self._uow.rollback()
            raise
