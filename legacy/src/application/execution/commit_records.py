"""只读事实读取与启动认领查询。"""
# mypy: disable-error-code="misc,attr-defined"


from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from aitest.application.execution.commit_guards import (
    _authorization_record_id,
    _intent_record_id,
    _json_payload,
    _payload_digest,
    _run_pointer_id,
    _validate_current_facts,
    _validate_original_grant_proof,
)
from aitest.application.execution.facts import (
    _run_fact,
    _step_fact,
)
from aitest.application.execution.facts import (
    validate_attempt_projection as _validate_attempt_projection,
)
from aitest.application.execution.run_record import read_run_record
from aitest.application.execution.snapshots import read_execution_snapshot
from aitest.application.execution.step_content import StepContentReader
from aitest.application.ports import (
    RecordRepository,
)
from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork
from aitest.contracts.execution_facts import (
    ExecutionFacts,
)
from aitest.domain.evidence.evidence import (
    EvidenceRef,
)
from aitest.domain.execution.dependencies import (
    CaseReuseBasis,
    CaseReuseInvalidation,
    invalidate_reuse_bases,
)
from aitest.domain.execution.runs import (
    Attempt,
    AuthorizationRef,
    InputRef,
    PlanRevisionRef,
    RecoveryRecord,
    Step,
    authorization_action_basis,
)

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


if TYPE_CHECKING:
    from aitest.application.execution.commit import ExecutionCommitCoordinator


class CommitRecordsMixin:
    def recall_initial_run(
        self: ExecutionCommitCoordinator,
        *,
        run_id: str,
        project_id: str,
        fingerprint: str,
        intent_id: str,
        prepared_run_id: str,
        workspace_id: str,
    ) -> ExecutionFacts | None:
        reader = self._records or self._uow
        current = getattr(reader, "current_revision", None)
        if not callable(current):
            raise ValueError("original registration receipt reader is unavailable")
        identity = "run-registration:" + run_id
        revision = current(aggregate_kind="execution_intent", record_id=identity)
        if type(revision) is not int or revision not in {0, 1}:
            raise ValueError("original registration receipt has an unverified warehouse revision")
        if revision == 0:
            return None
        intent = self._registration_payload("execution_intent", identity, project_id)
        if (
            set(intent)
            != {
                "schema_version",
                "project_id",
                "run_id",
                "intent_id",
                "prepared_run_id",
                "fingerprint",
                "snapshot_commit_id",
                "snapshot_revision",
                "snapshot_digest",
            }
            or intent.get("schema_version") != "aitest.run-registration-intent/1.0"
            or intent.get("project_id") != project_id
            or intent.get("run_id") != run_id
            or intent.get("intent_id") != intent_id
            or intent.get("prepared_run_id") != prepared_run_id
            or intent.get("fingerprint") != fingerprint
            or type(intent.get("snapshot_revision")) is not int
            or intent.get("snapshot_revision") != 1
            or not isinstance(intent.get("snapshot_commit_id"), str)
            or not intent["snapshot_commit_id"]
        ):
            raise ValueError("run registration intent conflicts with saved preparation")
        payload = self._registration_payload(
            "execution_facts", str(intent["snapshot_commit_id"]), project_id
        )
        if _payload_digest(payload) != intent.get("snapshot_digest"):
            raise ValueError("original run registration snapshot digest cannot be verified")
        facts = ExecutionFacts.model_validate_json(json.dumps(dict(payload)), strict=True)
        if (
            (facts.project_id, facts.run_id, facts.run.origin_workspace_id)
            != (project_id, run_id, workspace_id)
            or facts.snapshot_commit_id != intent["snapshot_commit_id"]
            or facts.snapshot_revision != 1
        ):
            raise ValueError("original run registration belongs to another project/run")
        _validate_current_facts(facts)
        raw_run = self._registration_payload("run", run_id, project_id)
        run = read_run_record(raw_run)
        if (
            (run.run_id, run.project_id, run.origin_workspace_id)
            != (run_id, project_id, workspace_id)
            or run.frozen_input_refs
            != (InputRef(prepared_run_id, f"prepared_run:{prepared_run_id}@1", fingerprint, True),)
            or _run_fact(run) != facts.run
        ):
            raise ValueError("original registration run does not prove this frozen preparation")
        if self._records is None:
            raise ValueError("original registration step material reader is unavailable")
        contents = StepContentReader(self._records)
        for fact in facts.steps:
            raw_step = self._registration_payload("step", fact.step_id, project_id)
            step = TypeAdapter(Step).validate_json(
                json.dumps({key: value for key, value in raw_step.items() if key != "project_id"}),
                strict=True,
            )
            if (
                dict(raw_step)
                != {"project_id": project_id, **_json_payload(TypeAdapter(Step), step)}
                or _step_fact(step) != fact
            ):
                raise ValueError(
                    "original registration step projection differs from saved authority"
                )
            try:
                contents.read(run=run, step=step)
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise ValueError("original registration step content cannot be verified") from error
        return facts

    def _registration_payload(
        self: ExecutionCommitCoordinator, kind: str, identity: str, project_id: str
    ) -> Mapping[str, object]:
        read = getattr(self._records or self._uow, "read", None)
        if not callable(read):
            raise ValueError("original registration material reader is unavailable")
        try:
            saved = read(aggregate_kind=kind, record_id=identity, revision=1)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError("original registration material is unavailable") from error
        if (
            getattr(saved, "aggregate_kind", None),
            getattr(saved, "record_id", None),
            getattr(saved, "revision", None),
        ) != (kind, identity, 1) or type(getattr(saved, "revision", None)) is not int:
            raise ValueError("original registration material envelope cannot be verified")
        payload = getattr(saved, "payload", None)
        if not isinstance(payload, Mapping) or payload.get("project_id") != project_id:
            raise ValueError("original registration material has a different project")
        return payload

    def read_current_facts(
        self: ExecutionCommitCoordinator, *, project_id: str, run_id: str
    ) -> ExecutionFacts | None:
        """Read exactly the saved publication reference; never infer current from history."""
        pointer = self._read_payload("execution_facts_current", _run_pointer_id(project_id, run_id))
        if pointer is None:
            return None
        if (
            set(pointer)
            != {
                "schema_version",
                "project_id",
                "run_id",
                "snapshot_commit_id",
                "snapshot_revision",
                "digest",
                "previous_snapshot_commit_id",
            }
            or pointer.get("schema_version") != "aitest.execution-facts-reference/1.0"
            or pointer.get("project_id") != project_id
            or pointer.get("run_id") != run_id
            or type(pointer.get("snapshot_revision")) is not int
            or pointer.get("snapshot_revision") != 1
        ):
            raise ValueError("current execution snapshot reference cannot be verified")
        snapshot_id = pointer.get("snapshot_commit_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise ValueError("current execution snapshot reference lacks an identity")
        previous = pointer.get("previous_snapshot_commit_id")
        if previous is not None and (
            not isinstance(previous, str) or not previous.strip() or previous == snapshot_id
        ):
            raise ValueError("current execution snapshot reference has invalid history identity")
        return read_execution_snapshot(
            cast(RecordRepository, self._records or self._uow),
            project_id=project_id,
            run_id=run_id,
            snapshot_id=snapshot_id,
            digest=cast(str, pointer.get("digest")),
        )

    def read_checkpoint(
        self: ExecutionCommitCoordinator, *, project_id: str, attempt_id: str
    ) -> RecoveryRecord:
        """Recovery sidecars are projections; load the saved authority before using them."""
        payload = self._read_payload("execution_checkpoint", attempt_id)
        if payload is None or payload.get("project_id") != project_id:
            raise ValueError("authoritative recovery checkpoint is unavailable or foreign")
        record = _CHECKPOINT_ADAPTER.validate_python(payload)
        if record.attempt.attempt_id != attempt_id:
            raise ValueError("authoritative recovery checkpoint identity cannot be verified")
        self._validate_checkpoint_update(project_id, record)
        return record

    def read_runtime_revision_facts(
        self: ExecutionCommitCoordinator, *, project_id: str, run_id: str
    ) -> ExecutionFacts:
        """Read authority for an assessment; the returned snapshot is not a write grant."""
        facts = self.read_current_facts(project_id=project_id, run_id=run_id)
        if facts is None:
            raise ValueError("runtime revision requires a registered current run")
        frozen_plan = PlanRevisionRef(**facts.plan_revision.model_dump())
        for fact in facts.attempts:
            # Historical activity also constrains edits; it requires the same authority.
            saved = self.read_checkpoint(project_id=project_id, attempt_id=fact.attempt_id)
            if saved.attempt.expected_plan_revision_ref != frozen_plan:
                raise ValueError("runtime checkpoint does not use the exact frozen run plan")
            _validate_attempt_projection(saved.attempt, fact)
        if self.read_current_facts(project_id=project_id, run_id=run_id) != facts:
            raise ValueError("current execution snapshot changed during runtime assessment")
        return facts

    def find_start(
        self: ExecutionCommitCoordinator, *, project_id: str, intent_id: str, fingerprint: str
    ) -> Attempt | None:
        payload = self._read_payload("execution_intent", _intent_record_id(project_id, intent_id))
        if payload is None:
            payload = self._read_payload("execution_intent", f"{project_id}:{intent_id}")
        if payload is None:
            return None
        intent_key = _intent_record_id(project_id, intent_id)
        if not self._revision("execution_intent", intent_key):
            intent_key = f"{project_id}:{intent_id}"
        if self._revision("execution_intent", intent_key) != 1:
            raise ValueError("saved execution intent must remain immutable at revision 1")
        if set(payload) != {"project_id", "intent_id", "fingerprint", "attempt_id"} or (
            payload.get("intent_id") != intent_id
        ):
            raise ValueError("execution intent has unknown fields or identity")
        if payload.get("project_id") != project_id or payload.get("fingerprint") != fingerprint:
            raise ValueError("execution intent conflicts with different input")
        attempt_id = payload.get("attempt_id")
        if not isinstance(attempt_id, str):
            raise ValueError("execution intent lacks an attempt identity")
        checkpoint = self._read_payload("execution_checkpoint", attempt_id)
        if checkpoint is None:
            raise ValueError("execution intent checkpoint is unavailable")
        # 检查点编解码器和领域记录共享相同 JSON 字段合同。
        if checkpoint.get("project_id") != project_id:
            raise ValueError("execution checkpoint project cannot be verified")
        attempt = _CHECKPOINT_ADAPTER.validate_python(checkpoint).attempt
        if attempt.intent_id != intent_id or attempt.intent_digest != fingerprint:
            raise ValueError("execution checkpoint intent basis cannot be verified")
        authorization = attempt.authorization_ref
        if authorization is None:
            raise ValueError("execution checkpoint authorization cannot be verified")
        claim = self._read_payload(
            "execution_authorization", _authorization_record_id(authorization.authorization_id)
        )
        if claim is None or any(
            claim.get(name) != expected
            for name, expected in (
                ("project_id", project_id),
                ("run_id", attempt.run_id),
                ("step_id", attempt.step_id),
                ("attempt_id", attempt.attempt_id),
                ("intent_id", intent_id),
                ("fingerprint", fingerprint),
                ("authorization_id", authorization.authorization_id),
            )
        ):
            raise ValueError("saved authorization consumption cannot be verified")
        if claim.get("schema_version") != "aitest.execution-authorization-claim/1.1":
            raise ValueError("legacy authorization claim has no original proof; inspect history")
        if (
            set(claim)
            != {
                "schema_version",
                "project_id",
                "run_id",
                "step_id",
                "attempt_id",
                "intent_id",
                "fingerprint",
                "authorization_id",
                "authorization_ref",
                "original_grant_proof",
            }
            or self._revision(
                "execution_authorization", _authorization_record_id(authorization.authorization_id)
            )
            != 1
        ):
            raise ValueError(
                "saved original authorization claim is not immutable or has extra fields"
            )
        saved_authorization = TypeAdapter(AuthorizationRef).validate_json(
            json.dumps(claim.get("authorization_ref")), strict=True
        )
        if claim["authorization_ref"] != _json_payload(
            TypeAdapter(AuthorizationRef), saved_authorization
        ):
            raise ValueError("saved authorization reference has unknown fields")
        if authorization_action_basis(saved_authorization) != authorization_action_basis(
            authorization
        ):
            raise ValueError("saved authorization basis cannot be verified")
        proof = claim.get("original_grant_proof")
        if self._execution_authorizations is None or not isinstance(proof, Mapping):
            raise ValueError("saved original authorization proof is unavailable")
        _validate_original_grant_proof(proof, authorization)
        self._execution_authorizations.validate_occupation(
            project_id=project_id, attempt=attempt, proof=proof
        )
        return attempt

    def read_reuse_invalidations(
        self: ExecutionCommitCoordinator,
        *,
        project_id: str,
        run_id: str,
        bases: tuple[CaseReuseBasis, ...],
    ) -> tuple[CaseReuseInvalidation, ...]:
        """Rebuild denial from one saved boundary, never from volatile hints.

        The existing start transaction already retains all Attempts and the
        actual consumer closure. Even the first optional-step start denies R
        for its whole case; failure, cancellation and restart cannot undo it.
        This read does not qualify or grant any candidate for reuse.
        """
        facts = self.read_current_facts(project_id=project_id, run_id=run_id)
        if facts is None:
            raise ValueError("reuse denial requires a registered current run")
        cases = {step.step_id: step.case_id for step in facts.steps}
        started_cases = tuple(sorted({cases[item.step_id] for item in facts.attempts}))
        affected = {
            item.attempt_id
            for item in facts.attempts
            if not item.is_current or item.state.value == "invalidated"
        } | {
            item.affected_attempt_id
            for item in facts.dependency_invalidations
            if item.affected_attempt_id is not None
        }
        return invalidate_reuse_bases(
            bases,
            affected_upstream_attempt_ids=tuple(sorted(affected)),
            started_case_ids=started_cases,
        )
