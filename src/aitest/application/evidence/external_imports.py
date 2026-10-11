"""Keep immutable safe external material without inventing plugin execution facts."""

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime

from aitest.application.execution.commands import ExecutionCommands, InvalidExecutionCommand
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import execution_payload_digest
from aitest.application.ports import EvidenceObjectStore
from aitest.contracts.commands import Command
from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.domain.execution.assertions import freeze_json_value, json_equal

_MAX_BYTES = 4 * 1024 * 1024
_MAX_ATTACHMENTS = 32
_MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024
_DOCUMENT_FIELDS = {
    "schema_version",
    "source_instance_id",
    "source_record_id",
    "layer",
    "source_identity",
    "run_id",
    "step_id",
    "attempt_id",
    "actual_output",
    "assertion_values",
    "mock_declarations",
    "attachments",
}
_RECORD_FIELDS = {
    "schema_version",
    "import_id",
    "project_id",
    "workspace_id",
    "material_digest",
    "object_ref",
    "attachment_refs",
    "origin",
    "verification_status",
    "gap_ids",
    "created_at",
}


class ExternalImportBlocked(ValueError):
    code = "EXTERNAL_IMPORT_BLOCKED"


def _encoded(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _json_material(content: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ExternalImportBlocked("external JSON contains ambiguous duplicate keys")
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise ExternalImportBlocked("external JSON contains a non-finite constant")

    return json.loads(content, object_pairs_hook=pairs, parse_constant=constant)


def _object_ref(raw: object) -> StoredObjectRef:
    if (
        not isinstance(raw, dict)
        or set(raw) != set(StoredObjectRef.__dataclass_fields__)
        or type(raw.get("size")) is not int
        or any(
            not isinstance(raw.get(key), str) or not raw[key].strip()
            for key in ("project_id", "digest", "media_type", "relative_path")
        )
    ):
        raise ExternalImportBlocked("external material needs a complete typed object reference")
    return StoredObjectRef(**raw)


class SavedExternalResultImport:
    def __init__(
        self,
        coordinator: ExecutionCommitCoordinator,
        objects: EvidenceObjectStore,
        workspace_id: str,
        protector: Callable[[Mapping[str, object]], Mapping[str, object]],
    ) -> None:
        self.coordinator, self.objects = coordinator, objects
        self.workspace_id, self.protector, self.unit = workspace_id, protector, coordinator._uow

    def _read(self, kind: str, identity: str) -> Mapping[str, object] | None:
        raw = self.coordinator._read_payload(kind, identity)
        if raw is not None and self.coordinator._revision(kind, identity) != 1:
            raise ExternalImportBlocked("external import requires an immutable original revision")
        return raw

    def _safe(self, value: Mapping[str, object]) -> dict[str, object]:
        safe = freeze_json_value(self.protector(value))
        if not isinstance(safe, dict) or len(_encoded(safe)) > _MAX_BYTES:
            raise ExternalImportBlocked("external material exceeds the safe JSON budget")
        return safe

    def _document(self, raw: object) -> dict[str, object]:
        document = freeze_json_value(raw)
        if not isinstance(document, dict) or set(document) != _DOCUMENT_FIELDS:
            raise ExternalImportBlocked("external result has unknown or missing fields")
        if (
            document["schema_version"] != "aitest.external-result/1.0"
            or not isinstance(document["layer"], str)
            or document["layer"] not in {"L1", "L2", "L3"}
        ):
            raise ExternalImportBlocked("external result schema or layer is unsupported")
        for name in ("source_instance_id", "source_record_id", "run_id", "step_id", "attempt_id"):
            value = document[name]
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ExternalImportBlocked(
                    "external identity must be explicit text or unknown null"
                )
        if document["source_identity"] is not None and not isinstance(
            document["source_identity"], dict
        ):
            raise ExternalImportBlocked(
                "external source identity must be an object or unknown null"
            )
        if not isinstance(document["actual_output"], dict) or not isinstance(
            document["assertion_values"], dict
        ):
            raise ExternalImportBlocked(
                "external output and self-reported assertions require JSON objects"
            )
        mocks, attachments = document["mock_declarations"], document["attachments"]
        if not isinstance(mocks, list) or any(not isinstance(item, dict) for item in mocks):
            raise ExternalImportBlocked("external Mock declarations require JSON objects")
        if not isinstance(attachments, list) or len(attachments) > _MAX_ATTACHMENTS:
            raise ExternalImportBlocked("external attachment list exceeds its explicit budget")
        return document

    def _attachments(self, document: Mapping[str, object], project: str) -> None:
        attachments = document["attachments"]
        assert isinstance(attachments, list)
        total = 0
        seen: set[str] = set()
        for raw in attachments:
            ref = _object_ref(raw)
            if (
                ref.project_id != project
                or type(ref.size) is not int
                or not 0 < ref.size <= _MAX_BYTES
                or ref.media_type not in {"text/plain", "application/json"}
            ):
                raise ExternalImportBlocked(
                    "external attachment project/type/size cannot be safely verified"
                )
            total += ref.size
            if total > _MAX_ATTACHMENT_BYTES or ref.digest in seen:
                raise ExternalImportBlocked(
                    "external attachments are duplicated or exceed the total byte budget"
                )
            seen.add(ref.digest)
            content = self.objects.read_bytes(ref)
            try:
                text = content.decode("utf-8")
                material = {
                    "attachment": _json_material(content)
                    if ref.media_type == "application/json"
                    else text
                }
                if not json_equal(material, self._safe(material)):
                    raise ExternalImportBlocked("external attachment is not safely filtered")
            except (UnicodeError, json.JSONDecodeError) as error:
                raise ExternalImportBlocked(
                    "external attachment is not safely readable text/JSON"
                ) from error

    def _owner(self, project: str) -> Mapping[str, object]:
        raw = self.coordinator._read_payload("project", project)
        if raw is None or (raw.get("project_id"), raw.get("workspace_id")) != (
            project,
            self.workspace_id,
        ):
            raise ExternalImportBlocked("external result needs the accurately saved local project")
        return raw

    def _recall(self, identity: str, project: str, digest: str) -> Mapping[str, object] | None:
        raw = self._read("external_import", identity)
        if raw is None:
            return None
        if (
            set(raw) != _RECORD_FIELDS
            or raw["schema_version"] != "aitest.external-import-record/1.0"
            or (raw["import_id"], raw["project_id"], raw["workspace_id"])
            != (identity, project, self.workspace_id)
            or raw["origin"] != "external_import"
            or raw["verification_status"] != "not_verified"
        ):
            raise ExternalImportBlocked("saved external import identity/origin cannot be verified")
        if raw["material_digest"] != digest:
            raise ExternalImportBlocked("external source/intent conflicts with different material")
        ref = _object_ref(raw["object_ref"])
        if (
            ref.project_id != project
            or ref.media_type != "application/json"
            or type(ref.size) is not int
            or not 0 < ref.size <= _MAX_BYTES
            or ref.digest != digest
        ):
            raise ExternalImportBlocked("saved external material scope/size/digest is invalid")
        content = self.objects.read_bytes(ref)
        material = _json_material(content)
        if (
            not isinstance(material, dict)
            or set(material) != {"schema_version", "document", "redacted"}
            or material["schema_version"] != "aitest.external-import-material/1.0"
            or type(material["redacted"]) is not bool
            or _encoded(material) != content
            or not json_equal(material, self._safe(material))
        ):
            raise ExternalImportBlocked("saved external JSON/safety cannot be verified")
        document = self._document(material["document"])
        if raw["attachment_refs"] != document["attachments"]:
            raise ExternalImportBlocked(
                "saved attachment references differ from the original document"
            )
        self._attachments(document, project)
        if raw["gap_ids"] != self._gaps(document, material["redacted"]):
            raise ExternalImportBlocked("external material gaps cannot be verified")
        return {"import_id": identity, "record_revision": 1, **dict(raw), "document": document}

    @staticmethod
    def _gaps(document: Mapping[str, object], redacted: bool) -> list[str]:
        gaps = [
            "external_self_report",
            "independent_verification_missing",
            "plugin_execution_unmatched",
        ]
        if document["source_instance_id"] is None or document["source_record_id"] is None:
            gaps.append("external_source_unknown")
        gaps.append(
            "source_identity_unknown"
            if document["source_identity"] is None
            else "source_identity_unverified"
        )
        if redacted:
            gaps.append("external_material_filtered")
        return gaps

    def apply(self, command: Command) -> Mapping[str, object]:
        project, intent = ExecutionCommands._identity(command)
        if (
            command.action != "import_external_result"
            or command.target != "external-result"
            or set(command.parameters) != {"document"}
        ):
            raise InvalidExecutionCommand("external import requires only its versioned document")
        original = self._document(command.parameters["document"])
        safe = self._document(self._safe(original))
        for key in (
            "source_instance_id",
            "source_record_id",
            "layer",
            "run_id",
            "step_id",
            "attempt_id",
            "schema_version",
        ):
            if safe[key] != original[key]:
                raise ExternalImportBlocked(
                    "external identities cannot be silently filtered and rebound"
                )
        material: dict[str, object] = {
            "schema_version": "aitest.external-import-material/1.0",
            "document": safe,
            "redacted": not json_equal(original, safe),
        }
        content = _encoded(material)
        if len(content) > _MAX_BYTES:
            raise ExternalImportBlocked("external material exceeds the safe JSON budget")
        digest = execution_payload_digest(material)
        source_key = (
            {
                "source_instance_id": safe["source_instance_id"],
                "source_record_id": safe["source_record_id"],
            }
            if safe["source_instance_id"] is not None and safe["source_record_id"] is not None
            else {"unknown_source_intent_id": intent}
        )
        identity = (
            "external-import-"
            + execution_payload_digest(
                {"workspace_id": self.workspace_id, "project_id": project, **source_key}
            )[7:]
        )
        receipt_id = (
            "external-import-intent-"
            + execution_payload_digest(
                {"workspace_id": self.workspace_id, "project_id": project, "intent_id": intent}
            )[7:]
        )
        receipt = {
            "schema_version": "aitest.external-import-intent/1.0",
            "workspace_id": self.workspace_id,
            "project_id": project,
            "intent_id": intent,
            "import_id": identity,
            "material_digest": digest,
        }
        prior = self._read("execution_intent", receipt_id)
        if prior is not None and prior != receipt:
            raise ExternalImportBlocked("external import intent conflicts with different input")
        existing = self._recall(identity, project, digest)
        if prior is not None:
            if existing is None:
                raise ExternalImportBlocked("original external import result is unavailable")
            return existing
        owner = self._owner(project)
        self._attachments(safe, project)
        ref = (
            self.objects.publish_bytes(project, content, media_type="application/json")
            if existing is None
            else None
        )
        if ref is not None and (
            ref.project_id != project
            or ref.digest != digest
            or self.objects.read_bytes(ref) != content
        ):
            raise ExternalImportBlocked("published external material bytes differ")
        self.unit.begin(command.request_id, project)
        try:
            if self._owner(project) != owner:
                raise ExternalImportBlocked("project changed before external import publication")
            if existing is None:
                assert ref is not None
                self.unit.stage_record(
                    aggregate_kind="external_import",
                    record_id=identity,
                    expected_revision=0,
                    payload={
                        "schema_version": "aitest.external-import-record/1.0",
                        "import_id": identity,
                        "project_id": project,
                        "workspace_id": self.workspace_id,
                        "material_digest": digest,
                        "object_ref": asdict(ref),
                        "attachment_refs": safe["attachments"],
                        "origin": "external_import",
                        "verification_status": "not_verified",
                        "gap_ids": self._gaps(safe, material["redacted"] is True),
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                )
            self.unit.stage_record(
                aggregate_kind="execution_intent",
                record_id=receipt_id,
                expected_revision=0,
                payload=receipt,
            )
            self.unit.commit()
        except BaseException:
            self.unit.rollback()
            raise
        result = self._recall(identity, project, digest)
        assert result is not None
        return result
