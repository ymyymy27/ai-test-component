"""Save independent business reads before publishing their verification facts."""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from pydantic import TypeAdapter

from aitest.application.errors import CapabilityUnavailable
from aitest.application.evidence.evidence_review import (
    EvidenceReviewService,
    freeze_verification_request,
)
from aitest.application.execution.authorization import ExecutionAuthorizationService
from aitest.application.execution.commands import ExecutionCommands, InvalidExecutionCommand
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import execution_payload_digest
from aitest.application.execution.snapshots import read_execution_snapshot
from aitest.application.execution.start_identity import execution_start_fingerprint
from aitest.application.ports import (
    BusinessVerificationCapturePort,
    BusinessVerificationResolver,
    CapturedBusinessVerification,
    EvidenceObjectStore,
    RecordRepository,
    VerificationRequest,
)
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import (
    CodeIdentityFact,
    EvidenceCaptureSourceFact,
    EvidenceFact,
    EvidenceIntegrityFact,
    EvidenceKindFact,
    EvidenceLevelFact,
    ExecutionFacts,
    ProjectionStateFact,
    RedactionStateFact,
    SourceBindingKindFact,
    VerificationFact,
    VerificationObservationFact,
)
from aitest.domain.evidence.evidence import EvidenceRef, StoredObjectRef, VerificationObservation
from aitest.domain.evidence.verification import compare_business_fields
from aitest.domain.execution.assertions import freeze_json_value, json_equal

_MAX_MATERIAL_BYTES = 4 * 1024 * 1024
_ADMISSION_FIELDS = {
    "schema_version",
    "workspace_id",
    "project_id",
    "run_id",
    "step_id",
    "attempt_id",
    "intent_id",
    "base_snapshot_commit_id",
    "snapshot_digest",
    "request",
    "code_identity",
    "source_instance_id",
}
_RESULT_FIELDS = {
    "schema_version",
    "project_id",
    "admission_digest",
    "object_ref",
    "evidence",
    "verification",
    "status",
    "result_snapshot",
    "result_snapshot_digest",
}


class BusinessVerificationBlocked(ValueError):
    code = "BUSINESS_VERIFICATION_BLOCKED"


def _bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _request_payload(request: VerificationRequest) -> dict[str, object]:
    return cast(
        dict[str, object],
        freeze_json_value(
            {
                **asdict(request),
                "evidence_refs": list(request.evidence_refs),
            }
        ),
    )


def _request(raw: object) -> VerificationRequest:
    if not isinstance(raw, Mapping) or set(raw) != set(VerificationRequest.__dataclass_fields__):
        raise BusinessVerificationBlocked("saved verification request has unknown fields")
    fields = dict(raw)
    refs = fields.get("evidence_refs")
    if not isinstance(refs, list):
        raise BusinessVerificationBlocked("saved verification evidence list is invalid")
    fields["evidence_refs"] = tuple(refs)
    return freeze_verification_request(VerificationRequest(**fields))


def _evidence_record(evidence: EvidenceFact) -> dict[str, object]:
    raw = evidence.model_dump(mode="json", exclude={"redaction_summary"})
    value = TypeAdapter(EvidenceRef).validate_json(json.dumps(raw), strict=True)
    return cast(dict[str, object], TypeAdapter(EvidenceRef).dump_python(value, mode="json"))


class SavedBusinessVerification:
    def __init__(
        self,
        coordinator: ExecutionCommitCoordinator,
        authorizations: ExecutionAuthorizationService,
        objects: EvidenceObjectStore,
        *,
        workspace_id: str,
        instance_id: str,
        resolver: BusinessVerificationResolver | None,
        verifier: BusinessVerificationCapturePort | None,
        protector: Callable[[Mapping[str, object]], Mapping[str, object]],
    ) -> None:
        self.coordinator, self.authorizations, self.objects = coordinator, authorizations, objects
        self.workspace_id, self.instance_id = workspace_id, instance_id
        self.resolver, self.verifier, self.protector = resolver, verifier, protector
        self.unit = coordinator._uow
        self.records = cast(RecordRepository, coordinator._records or self.unit)

    def _safe(self, material: Mapping[str, object]) -> dict[str, object]:
        value = freeze_json_value(self.protector(material))
        if not isinstance(value, dict) or len(_bytes(value)) > _MAX_MATERIAL_BYTES:
            raise BusinessVerificationBlocked("safe verification material exceeds its JSON budget")
        return value

    def _unchanged_safe(self, material: Mapping[str, object]) -> None:
        if _bytes(self._safe(material)) != _bytes(material):
            raise BusinessVerificationBlocked("verification identity or expected basis is unsafe")

    def _read(self, kind: str, identity: str) -> Mapping[str, object] | None:
        revision = self.records.current_revision(aggregate_kind=kind, record_id=identity)
        if type(revision) is not int or revision not in (0, 1):
            raise BusinessVerificationBlocked("verification material is not an immutable revision")
        if revision == 0:
            return None
        record = self.records.read(aggregate_kind=kind, record_id=identity, revision=1)
        if (
            getattr(record, "aggregate_kind", None), getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != (kind, identity, 1) or type(getattr(record, "revision", None)) is not int:
            raise BusinessVerificationBlocked("verification material envelope cannot be verified")
        raw = getattr(record, "payload", None)
        if not isinstance(raw, Mapping):
            raise BusinessVerificationBlocked("verification material body cannot be verified")
        return raw

    def _base(self, admission: Mapping[str, object]) -> ExecutionFacts:
        facts = read_execution_snapshot(
            self.records, project_id=cast(str, admission["project_id"]),
            run_id=cast(str, admission["run_id"]),
            snapshot_id=cast(str, admission["base_snapshot_commit_id"]),
            digest=cast(str, admission["snapshot_digest"]),
        )
        if (
            (
                facts.project_id,
                facts.run_id,
                facts.run.origin_workspace_id,
                facts.snapshot_commit_id,
            )
            != (
                admission["project_id"],
                admission["run_id"],
                self.workspace_id,
                admission["base_snapshot_commit_id"],
            )
            or facts.current_attempt_by_step.get(str(admission["step_id"]))
            != admission["attempt_id"]
            or not any(
                item.attempt_id == admission["attempt_id"]
                and item.step_id == admission["step_id"]
                and item.is_current
                for item in facts.attempts
            )
        ):
            raise BusinessVerificationBlocked("verification snapshot has a different operation")
        return facts

    def _admission(self, identity: str, scope: Mapping[str, object]) -> Mapping[str, object] | None:
        raw = self._read("execution_intent", identity + "-admission")
        if raw is None:
            return None
        if (
            set(raw) != _ADMISSION_FIELDS
            or raw["schema_version"] != "aitest.business-query-intent/1.0"
            or any(not isinstance(value, str) or not value.strip() for value in (
                raw[key] for key in (
                    "workspace_id", "project_id", "run_id", "step_id", "attempt_id", "intent_id",
                    "base_snapshot_commit_id", "snapshot_digest", "source_instance_id",
                )
            ))
        ):
            raise BusinessVerificationBlocked("unknown saved verification admission")
        if any(raw[key] != value for key, value in scope.items()):
            raise BusinessVerificationBlocked(
                "verification intent conflicts with its original input"
            )
        self._unchanged_safe(raw)
        self._base(raw)
        request = _request(raw["request"])
        if request.verification_of != raw["attempt_id"]:
            raise BusinessVerificationBlocked("verification request points at a different attempt")
        CodeIdentityFact.model_validate_json(json.dumps(raw["code_identity"]), strict=True)
        return raw

    def _evidence_inputs(self, request: VerificationRequest, facts: ExecutionFacts) -> None:
        by_id = {item.evidence_id: item for item in facts.evidence_refs}
        for identity in request.evidence_refs:
            item = by_id.get(identity)
            if item is None or (item.project_id, item.run_id, item.attempt_id) != (
                facts.project_id,
                facts.run_id,
                request.verification_of,
            ):
                raise BusinessVerificationBlocked(
                    "requested evidence is outside the saved operation"
                )
            self.objects.read_bytes(
                StoredObjectRef(
                    item.project_id,
                    item.object_digest,
                    item.object_size,
                    item.media_type or "application/octet-stream",
                    f"objects/{item.project_id}/{item.object_digest.removeprefix('sha256:')}",
                )
            )

    def _claim(
        self, identity: str, scope: Mapping[str, object], request_id: str
    ) -> Mapping[str, object]:
        if self.resolver is None or self.verifier is None:
            raise CapabilityUnavailable(
                "a trusted business query resolver/adapter is not configured"
            )
        project, run, step, attempt_id = (
            str(scope[key]) for key in ("project_id", "run_id", "step_id", "attempt_id")
        )
        facts = self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)
        if (
            facts.run.origin_workspace_id,
            facts.snapshot_commit_id,
            facts.current_attempt_by_step.get(step),
        ) != (self.workspace_id, scope["base_snapshot_commit_id"], attempt_id):
            raise BusinessVerificationBlocked(
                "verification needs the exact current attempt/snapshot"
            )
        checkpoint = self.coordinator.read_checkpoint(project_id=project, attempt_id=attempt_id)
        if (
            checkpoint is None
            or checkpoint.attempt.run_id != run
            or checkpoint.attempt.step_id != step
        ):
            raise BusinessVerificationBlocked("the actual operation checkpoint is unavailable")
        attempt = checkpoint.attempt
        if attempt.authorization_ref is None:
            raise BusinessVerificationBlocked("the original operation authorization is unavailable")
        _, action = self.authorizations._origin(project, attempt.authorization_ref.authorization_id)
        if (
            self.coordinator.find_start(
                project_id=project,
                intent_id=attempt.intent_id,
                fingerprint=execution_start_fingerprint(action.attempt, action.request),
            )
            != attempt
        ):
            raise BusinessVerificationBlocked(
                "operation differs from its original execution intent"
            )
        _, _, prepared, _ = self.authorizations._context(project, run, step)
        request = freeze_verification_request(
            self.resolver.resolve(
                facts=facts.model_copy(deep=True), step_id=step, attempt_id=attempt_id
            )
        )
        if request.verification_of != attempt_id:
            raise BusinessVerificationBlocked(
                "resolved query does not verify the original operation"
            )
        self._evidence_inputs(request, facts)
        admission: dict[str, object] = {
            "schema_version": "aitest.business-query-intent/1.0",
            **scope,
            "snapshot_digest": execution_payload_digest(facts.model_dump(mode="json")),
            "request": _request_payload(request),
            "code_identity": CodeIdentityFact(
                binding_kind=SourceBindingKindFact(prepared.binding_form.value),
                workspace_ref=self.workspace_id,
                commit_id=prepared.git_base_commit,
                file_manifest_digest=prepared.plain_manifest_digest,
                revision_ref=prepared.snapshot.source_snapshot_id,
            ).model_dump(mode="json"),
            "source_instance_id": self.instance_id,
        }
        self._unchanged_safe(admission)
        self.unit.begin(request_id + "-admission", project)
        try:
            if (
                self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)
                != facts
            ):
                raise BusinessVerificationBlocked(
                    "verification basis changed before query admission"
                )
            self.unit.stage_record(
                aggregate_kind="execution_intent",
                record_id=identity + "-admission",
                expected_revision=0,
                payload=admission,
            )
            self.unit.commit()
        except BaseException:
            self.unit.rollback()
            raise
        return admission

    def _material(
        self, admission: Mapping[str, object], capture: CapturedBusinessVerification
    ) -> dict[str, object]:
        request = _request(admission["request"])
        if not isinstance(capture, CapturedBusinessVerification):
            raise BusinessVerificationBlocked("query returned no actual capture material")
        result = EvidenceReviewService.validate(request, capture.verification)
        actual = freeze_json_value(capture.actual_fields)
        if actual is not None and not isinstance(actual, dict):
            raise BusinessVerificationBlocked("business query actual fields require a JSON object")
        if actual is None:
            if result.observation in {
                VerificationObservation.MATCHED,
                VerificationObservation.MISMATCHED,
            }:
                raise BusinessVerificationBlocked(
                    "a claimed observation lacks the actual query JSON"
                )
            unavailable: tuple[str, ...] = ()
            safe_actual = None
        else:
            observed, gaps = compare_business_fields(actual, request.expected_facts)
            if result.observation != observed or result.gap_ids != gaps:
                raise BusinessVerificationBlocked(
                    "claimed observation disagrees with actual query JSON"
                )
            safe_actual = self._safe(actual)
            # Entire required field is unavailable if any nested part was filtered.
            unavailable = tuple(
                sorted(
                    key
                    for key in request.expected_facts
                    if key in actual
                    and (key not in safe_actual or not json_equal(actual[key], safe_actual[key]))
                )
            )
        material: dict[str, object] = {
            "schema_version": "aitest.business-query-material/1.0",
            "admission_digest": execution_payload_digest(admission),
            "actual_fields": safe_actual,
            "redacted": actual is not None and not json_equal(actual, safe_actual),
            "unavailable_fields": list(unavailable),
            "unavailable_observation": result.observation.value if actual is None else None,
            "unavailable_gap_ids": list(result.gap_ids) if actual is None else [],
            "captured_at": datetime.now(UTC).isoformat(),
        }
        self._unchanged_safe(material)
        return material

    def _derive(
        self,
        identity: str,
        admission: Mapping[str, object],
        material: Mapping[str, object],
        ref: StoredObjectRef,
    ) -> tuple[EvidenceFact, VerificationFact]:
        if (
            set(material)
            != {
                "schema_version",
                "admission_digest",
                "actual_fields",
                "redacted",
                "unavailable_fields",
                "unavailable_observation",
                "unavailable_gap_ids",
                "captured_at",
            }
            or material["schema_version"] != "aitest.business-query-material/1.0"
            or material["admission_digest"] != execution_payload_digest(admission)
            or ref.project_id != admission["project_id"]
            or ref.media_type != "application/json"
            or type(material.get("redacted")) is not bool
        ):
            raise BusinessVerificationBlocked(
                "saved actual query material has a different identity"
            )
        self._unchanged_safe(material)
        request = _request(admission["request"])
        actual = freeze_json_value(material["actual_fields"])
        unavailable = material["unavailable_fields"]
        if not isinstance(unavailable, list) or any(not isinstance(x, str) for x in unavailable):
            raise BusinessVerificationBlocked("saved filtered field list is invalid")
        if unavailable != sorted(set(unavailable)) or (unavailable and not material["redacted"]):
            raise BusinessVerificationBlocked("saved filtered fields lack accurate provenance")
        if actual is None:
            if not isinstance(material["unavailable_observation"], str):
                raise BusinessVerificationBlocked("saved unavailable query observation is invalid")
            observation = VerificationObservation(material["unavailable_observation"])
            raw_gaps = material["unavailable_gap_ids"]
            if (
                observation in {VerificationObservation.MATCHED, VerificationObservation.MISMATCHED}
                or unavailable
                or material["redacted"]
                or not isinstance(raw_gaps, list)
                or any(not isinstance(x, str) or not x for x in raw_gaps)
            ):
                raise BusinessVerificationBlocked(
                    "unavailable actual material claims a known result"
                )
            gaps = tuple(raw_gaps)
        elif isinstance(actual, dict):
            if (
                material["unavailable_observation"] is not None
                or material["unavailable_gap_ids"] != []
            ):
                raise BusinessVerificationBlocked(
                    "saved actual material has contradictory query facts"
                )
            observation, gaps = compare_business_fields(
                actual, request.expected_facts, unavailable_fields=tuple(unavailable)
            )
        else:
            raise BusinessVerificationBlocked("saved actual JSON is invalid")
        captured_at = datetime.fromisoformat(str(material["captured_at"]))
        if captured_at.tzinfo is None:
            raise BusinessVerificationBlocked("saved query time is not an aware capture fact")
        evidence = EvidenceFact(
            evidence_id=identity + "-evidence",
            evidence_revision=1,
            project_id=str(admission["project_id"]),
            source_instance_id=str(admission["source_instance_id"]),
            run_id=str(admission["run_id"]),
            step_id=str(admission["step_id"]),
            attempt_id=str(admission["attempt_id"]),
            evidence_kind=EvidenceKindFact.VERIFICATION,
            capture_source=EvidenceCaptureSourceFact.PLUGIN_RUNTIME,
            code_identity=CodeIdentityFact.model_validate_json(
                json.dumps(admission["code_identity"]), strict=True,
            ),
            object_digest=ref.digest,
            object_size=ref.size,
            media_type=ref.media_type,
            integrity=EvidenceIntegrityFact.COMPLETE,
            redaction_state=(
                RedactionStateFact.REDACTED
                if material["redacted"]
                else RedactionStateFact.NOT_REQUIRED
            ),
            projection_state=ProjectionStateFact.DISPLAYABLE,
            evidence_level=EvidenceLevelFact.UNKNOWN,
            gap_ids=gaps,
            created_at=captured_at,
        )
        verification = VerificationFact(
            verification_id=identity,
            verification_of=request.verification_of,
            business_object_id=request.business_object_id,
            query_method=request.query_method,
            observation=VerificationObservationFact(observation.value),
            query_interval=request.query_interval,
            deadline_condition=request.deadline_condition,
            target_deployment_ref=request.target_deployment_ref,
            actual_result_ref=ref.digest,
            evidence_refs=(*request.evidence_refs, evidence.evidence_id),
            gap_ids=gaps,
            verified_at=captured_at,
        )
        return evidence, verification

    def _recall(
        self, identity: str, admission: Mapping[str, object]
    ) -> Mapping[str, object] | None:
        raw = self._read("verification", identity)
        if raw is None:
            return None
        if (
            set(raw) != _RESULT_FIELDS
            or raw["schema_version"] != "aitest.saved-business-query/1.0"
            or raw["project_id"] != admission["project_id"]
            or raw["admission_digest"] != execution_payload_digest(admission)
        ):
            raise BusinessVerificationBlocked("saved verification result scope cannot be verified")
        ref_raw = raw["object_ref"]
        if not isinstance(ref_raw, dict) or set(ref_raw) != set(
            StoredObjectRef.__dataclass_fields__
        ) or any(not isinstance(ref_raw[key], str) or not ref_raw[key].strip() for key in (
            "project_id", "digest", "media_type", "relative_path",
        )):
            raise BusinessVerificationBlocked("saved actual query object reference is invalid")
        ref = StoredObjectRef(**ref_raw)
        if type(ref.size) is not int or not 0 < ref.size <= _MAX_MATERIAL_BYTES:
            raise BusinessVerificationBlocked("saved actual query size exceeds its read budget")
        if ref.project_id != admission["project_id"] or ref.media_type != "application/json":
            raise BusinessVerificationBlocked("saved actual query object belongs to another scope")
        content = self.objects.read_bytes(ref)
        if (
            type(content) is not bytes or len(content) != ref.size
            or "sha256:" + hashlib.sha256(content).hexdigest() != ref.digest
        ):
            raise BusinessVerificationBlocked(
                "saved actual query bytes differ from their reference",
            )
        material = json.loads(content)
        if not isinstance(material, dict) or _bytes(material) != content:
            raise BusinessVerificationBlocked(
                "saved query object is not its canonical JSON material"
            )
        evidence, verification = self._derive(identity, admission, material, ref)
        if any(
            not isinstance(raw[key], Mapping)
            or execution_payload_digest(cast(Mapping[str, object], raw[key])) != (
                execution_payload_digest(expected)
            )
            for key, expected in (
                ("evidence", evidence.model_dump(mode="json")),
                ("verification", verification.model_dump(mode="json")),
            )
        ):
            raise BusinessVerificationBlocked(
                "saved verification differs from its actual query material"
            )
        saved_evidence = self._read("evidence_ref", evidence.evidence_id)
        if saved_evidence is None or execution_payload_digest(saved_evidence) != (
            execution_payload_digest(_evidence_record(evidence))
        ):
            raise BusinessVerificationBlocked(
                "saved query evidence record is unavailable or different"
            )
        snapshot_raw = None
        if raw["status"] == "attached":
            snapshot = read_execution_snapshot(
                self.records, project_id=admission["project_id"],
                run_id=cast(str, admission["run_id"]),
                snapshot_id=cast(str, raw["result_snapshot"]),
                digest=cast(str, raw["result_snapshot_digest"]),
            )
            base = self._base(admission)
            unchanged = {"facts_id", "committed_at", "snapshot_commit_id", "snapshot_cursor",
                         "evidence_refs", "verifications"}
            if (
                execution_payload_digest(snapshot.model_dump(mode="json", exclude=unchanged))
                != execution_payload_digest(base.model_dump(mode="json", exclude=unchanged))
                or snapshot.evidence_refs != (*base.evidence_refs, evidence)
                or snapshot.verifications != (*base.verifications, verification)
                or snapshot.snapshot_cursor <= base.snapshot_cursor
                or snapshot.snapshot_commit_id == base.snapshot_commit_id
            ):
                raise BusinessVerificationBlocked(
                    "original result snapshot rewrote its query basis",
                )
            snapshot_raw = snapshot.model_dump(mode="json")
        elif (
            raw["status"] != "historical_only"
            or raw["result_snapshot"] is not None
            or raw["result_snapshot_digest"] is not None
        ):
            raise BusinessVerificationBlocked("saved verification attachment status is unknown")
        return {
            "status": raw["status"],
            "verification": raw["verification"],
            "evidence": raw["evidence"],
            "execution_facts": snapshot_raw,
        }

    def _publish(
        self, identity: str, admission: Mapping[str, object], material: Mapping[str, object]
    ) -> Mapping[str, object]:
        content = _bytes(material)
        ref = self.objects.publish_bytes(
            str(admission["project_id"]), content, media_type="application/json"
        )
        if self.objects.read_bytes(ref) != content:
            raise BusinessVerificationBlocked("saved query bytes changed during publication")
        evidence, verification = self._derive(identity, admission, material, ref)
        project, run = str(admission["project_id"]), str(admission["run_id"])
        self.unit.begin("business-query-result-" + uuid4().hex, project)
        try:
            current = self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)
            snapshot = None
            if current == self._base(admission):
                updated = current.model_copy(
                    update={
                        "facts_id": identity + "-facts",
                        "committed_at": datetime.now(UTC),
                        "evidence_refs": (*current.evidence_refs, evidence),
                        "verifications": (*current.verifications, verification),
                    }
                )
                _, snapshot = self.coordinator._stage_snapshot(updated)
            self.unit.stage_record(
                aggregate_kind="evidence_ref",
                record_id=evidence.evidence_id,
                expected_revision=0,
                payload=_evidence_record(evidence),
            )
            self.unit.stage_record(
                aggregate_kind="verification",
                record_id=identity,
                expected_revision=0,
                payload={
                    "schema_version": "aitest.saved-business-query/1.0",
                    "project_id": project,
                    "admission_digest": execution_payload_digest(admission),
                    "object_ref": asdict(ref),
                    "evidence": evidence.model_dump(mode="json"),
                    "verification": verification.model_dump(mode="json"),
                    "status": "attached" if snapshot is not None else "historical_only",
                    "result_snapshot": snapshot.snapshot_commit_id
                    if snapshot is not None
                    else None,
                    "result_snapshot_digest": execution_payload_digest(
                        snapshot.model_dump(mode="json")
                    )
                    if snapshot is not None
                    else None,
                },
            )
            self.unit.commit()
        except BaseException:
            self.unit.rollback()
            raise
        result = self._recall(identity, admission)
        assert result is not None
        return result

    def apply(self, command: Command) -> Mapping[str, object]:
        project, intent = ExecutionCommands._identity(command)
        values = command.parameters
        if (
            command.action != "verify_pending"
            or set(values) != {"run_id", "step_id", "attempt_id", "base_snapshot_commit_id"}
            or any(not isinstance(x, str) or not x.strip() for x in values.values())
            or command.target != values.get("step_id")
        ):
            raise InvalidExecutionCommand(
                "verification requires only the exact saved operation and snapshot"
            )
        scope = {
            "workspace_id": self.workspace_id,
            "project_id": project,
            "intent_id": intent,
            **values,
        }
        identity = (
            "business-query-"
            + execution_payload_digest(
                {"workspace_id": self.workspace_id, "project_id": project, "intent_id": intent}
            )[7:]
        )
        admission = self._admission(identity, scope)
        if admission is not None:
            result = self._recall(identity, admission)
            if result is not None:
                return result
            raise BusinessVerificationBlocked(
                "original query outcome is unverified; a new query requires a new intent"
            )
        admission = self._claim(identity, scope, command.request_id)
        assert self.verifier is not None
        # Even a trusted adapter gets a separate copy of the frozen request.
        capture = self.verifier.capture(_request(admission["request"]))
        return self._publish(identity, admission, self._material(admission, capture))
