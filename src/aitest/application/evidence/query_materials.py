"""Exact saved independent-query material shared by replay and historic sources."""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime
from typing import cast

from pydantic import TypeAdapter

from aitest.application.evidence.evidence_review import freeze_verification_request
from aitest.application.execution.facts import execution_payload_digest
from aitest.application.execution.snapshots import read_execution_snapshot
from aitest.application.ports import EvidenceObjectStore, RecordRepository, VerificationRequest
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
    VerificationFact,
    VerificationObservationFact,
)
from aitest.domain.evidence.evidence import EvidenceRef, StoredObjectRef, VerificationObservation
from aitest.domain.evidence.verification import compare_business_fields
from aitest.domain.execution.assertions import freeze_json_value

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


class SavedBusinessQueryReader:
    def __init__(
        self,
        records: RecordRepository,
        objects: EvidenceObjectStore,
        *,
        workspace_id: str,
        protector: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None,
    ) -> None:
        self.records, self.objects, self.workspace_id = records, objects, workspace_id
        self.protector = protector or (lambda value: value)

    def read(self, *, project_id: str, verification_id: str) -> Mapping[str, object]:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (
                project_id,
                verification_id,
                self.workspace_id,
            )
        ):
            raise BusinessVerificationBlocked(
                "saved query requires exact project/workspace/identity",
            )
        raw = self._read("execution_intent", verification_id + "-admission")
        if raw is None or set(raw) != _ADMISSION_FIELDS:
            raise BusinessVerificationBlocked("saved query admission is unavailable")
        scope = {
            "workspace_id": self.workspace_id,
            "project_id": project_id,
            **{
                key: raw[key]
                for key in (
                    "intent_id",
                    "run_id",
                    "step_id",
                    "attempt_id",
                    "base_snapshot_commit_id",
                )
            },
        }
        admission = self._admission(verification_id, scope)
        if (
            admission is None
            or verification_id
            != "business-query-"
            + execution_payload_digest(
                {
                    "workspace_id": self.workspace_id,
                    "project_id": project_id,
                    "intent_id": admission["intent_id"],
                }
            )[7:]
        ):
            raise BusinessVerificationBlocked(
                "saved query identity differs from its original intent",
            )
        result = self._recall(verification_id, admission)
        if result is None:
            raise BusinessVerificationBlocked("saved query result is unavailable")
        return result

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
            getattr(record, "aggregate_kind", None),
            getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != (kind, identity, 1) or type(getattr(record, "revision", None)) is not int:
            raise BusinessVerificationBlocked("verification material envelope cannot be verified")
        raw = getattr(record, "payload", None)
        if not isinstance(raw, Mapping):
            raise BusinessVerificationBlocked("verification material body cannot be verified")
        return raw

    def _base(self, admission: Mapping[str, object]) -> ExecutionFacts:
        facts = read_execution_snapshot(
            self.records,
            project_id=cast(str, admission["project_id"]),
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
            or any(
                not isinstance(value, str) or not value.strip()
                for value in (
                    raw[key]
                    for key in (
                        "workspace_id",
                        "project_id",
                        "run_id",
                        "step_id",
                        "attempt_id",
                        "intent_id",
                        "base_snapshot_commit_id",
                        "snapshot_digest",
                        "source_instance_id",
                    )
                )
            )
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
                json.dumps(admission["code_identity"]),
                strict=True,
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
        if (
            not isinstance(ref_raw, dict)
            or set(ref_raw) != set(StoredObjectRef.__dataclass_fields__)
            or any(
                not isinstance(ref_raw[key], str) or not ref_raw[key].strip()
                for key in (
                    "project_id",
                    "digest",
                    "media_type",
                    "relative_path",
                )
            )
        ):
            raise BusinessVerificationBlocked("saved actual query object reference is invalid")
        ref = StoredObjectRef(**ref_raw)
        if type(ref.size) is not int or not 0 < ref.size <= _MAX_MATERIAL_BYTES:
            raise BusinessVerificationBlocked("saved actual query size exceeds its read budget")
        if ref.project_id != admission["project_id"] or ref.media_type != "application/json":
            raise BusinessVerificationBlocked("saved actual query object belongs to another scope")
        content = self.objects.read_bytes(ref)
        if (
            type(content) is not bytes
            or len(content) != ref.size
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
            or execution_payload_digest(cast(Mapping[str, object], raw[key]))
            != (execution_payload_digest(expected))
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
                self.records,
                project_id=admission["project_id"],
                run_id=cast(str, admission["run_id"]),
                snapshot_id=cast(str, raw["result_snapshot"]),
                digest=cast(str, raw["result_snapshot_digest"]),
            )
            base = self._base(admission)
            unchanged = {
                "facts_id",
                "committed_at",
                "snapshot_commit_id",
                "snapshot_cursor",
                "evidence_refs",
                "verifications",
            }
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
