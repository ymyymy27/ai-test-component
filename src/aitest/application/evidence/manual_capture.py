"""受控人工补证入口：登记人工**实际材料**，不产生业务通过（P1-FR13、功能 §4）。

报告点名的缺口是「缺受控人工补证入口」：`ManualEvidenceAdapter` 会构造记录与
TraceNode，但没有任何生产入口把人工实际材料安全落盘、也不产生 `evidence_ref` 事实。

合同依据：

- 功能 §4「待核实…只能由人工核实或补证后离开」；「人工复核与计划确认分别存储，
  不覆盖其他维度」；
- 功能 §4「重试新建 Attempt…新尝试立即当前」——补证绑定**当前尝试**，不重放副作用；
- 架构 02 §9/§13「人工步骤依赖明确采集记录」；`EvidenceRef` 绑定项目/来源/Run/Step/
  Attempt、类型、代码身份、内容摘要、完整性、脱敏状态；
- 架构 02 §12「经工作单元认领一次后成为永久证据」：材料按内容寻址永久保存。

本模块的边界（不做的部分同样重要）：

1. **不判业务通过**：只登记证据；`business_passed` 恒为 false，`requires_verification`
   恒为 true（补证不替代独立核验）；
2. **不改尝试/运行状态**：状态迁移仍由既有控制/核验路径决定，不在本动作里伪造；
3. **不做安全替换**：材料一旦被凭据过滤改变即**拒绝**，不把过滤后的字节当原文永久保存；
4. 附件只接受**已按内容寻址保存**的对象引用（同一事务里核对项目/摘要/大小/可读性）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Final, Literal, Protocol, cast

from aitest.application.execution.facts import execution_payload_digest
from aitest.application.planning.preparation import preparation_identity_digest
from aitest.application.planning.preparation_origin import load_saved_preparation
from aitest.contracts.execution_facts import (
    AttemptStateFact,
    CodeIdentityFact,
    EvidenceCaptureSourceFact,
    EvidenceFact,
    EvidenceIntegrityFact,
    EvidenceKindFact,
    EvidenceLevelFact,
    ProjectionStateFact,
    RedactionStateFact,
    SourceBindingKindFact,
)
from aitest.domain.evidence.evidence import StoredObjectRef

MANUAL_EVIDENCE_AGGREGATE: Final[Literal["manual_evidence"]] = "manual_evidence"
EVIDENCE_AGGREGATE: Final[Literal["evidence_ref"]] = "evidence_ref"
INTENT_AGGREGATE: Final[Literal["execution_intent"]] = "execution_intent"
MANUAL_SCHEMA = "aitest.manual-evidence/1.0"
INTENT_SCHEMA = "aitest.manual-evidence-intent/1.0"

_OPERATIONS = frozenset(
    {
        "button",
        "pagination",
        "navigation",
        "form",
        "scroll",
        "mouse",
        "error_prompt",
        "data_display",
    }
)
_TEXT_FIELDS = ("run_id", "step_id", "attempt_id", "operation_type", "target_ref",
                "observed_result", "business_object_id")
_OPTIONAL_TEXT = ("input_summary",)
_MAX_BYTES = 512 * 1024
_MAX_ATTACHMENTS = 32
_MANUAL_GAPS = ("manual_capture", "independent_verification_unconfirmed")


class ManualEvidenceBlocked(ValueError):
    """人工补证拒绝；错误码沿用已登记集合。"""

    code = "MANUAL_EVIDENCE_BLOCKED"


class _Coordinator(Protocol):
    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> Any: ...


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManualEvidenceBlocked(f"{name} must be nonempty text")
    return value


def _object_ref(raw: object) -> StoredObjectRef:
    if (
        not isinstance(raw, Mapping)
        or set(raw) != set(StoredObjectRef.__dataclass_fields__)
        or type(raw.get("size")) is not int
        or any(
            not isinstance(raw.get(key), str) or not raw[key].strip()
            for key in ("project_id", "digest", "media_type", "relative_path")
        )
    ):
        raise ManualEvidenceBlocked("manual attachment needs a complete typed object reference")
    return StoredObjectRef(
        cast(str, raw["project_id"]),
        cast(str, raw["digest"]),
        cast(int, raw["size"]),
        cast(str, raw["media_type"]),
        cast(str, raw["relative_path"]),
    )


def _encoded(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class SavedManualEvidenceCapture:
    """登记一次人工补证；材料永久保存，结论不由本动作产生。"""

    def __init__(
        self,
        *,
        coordinator: _Coordinator,
        unit: Any,
        reader: Any,
        objects: Any,
        workspace_id: str,
        protector: Any | None = None,
    ) -> None:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ManualEvidenceBlocked("manual evidence requires a workspace identity")
        self.coordinator, self.unit, self.reader = coordinator, unit, reader
        self.objects, self.workspace_id = objects, workspace_id
        self.protector = protector or (lambda value: value)

    # ------------------------------------------------------------ 入口

    def apply(self, command: Any) -> Mapping[str, object]:
        project = _text(getattr(command, "project_id", None), "project_id")
        intent = _text(getattr(command, "intent_id", None), "intent_id")
        request_id = _text(getattr(command, "request_id", None), "request_id")
        parameters = getattr(command, "parameters", None)
        if getattr(command, "action", None) != "attach_evidence" or not isinstance(
            parameters, Mapping
        ):
            raise ManualEvidenceBlocked("manual evidence requires the versioned command")
        document = self._document(parameters)
        facts = self._facts(project, cast(str, document["run_id"]))
        step, attempt = self._target(facts, document)
        safe = self._safe_document(document)
        attachments = self._attachments(safe, project)

        material: dict[str, object] = {
            "schema_version": MANUAL_SCHEMA,
            "document": safe,
            "redacted": False,
        }
        content = _encoded(material)
        if len(content) > _MAX_BYTES:
            raise ManualEvidenceBlocked("manual material exceeds its safe JSON budget")
        digest = execution_payload_digest(material)
        evidence_id = "manual-evidence-" + digest[7:23]
        record_id = (
            "manual-record-"
            + execution_payload_digest(
                {
                    "workspace_id": self.workspace_id,
                    "project_id": project,
                    "run_id": document["run_id"],
                    "step_id": document["step_id"],
                    "attempt_id": document["attempt_id"],
                    "evidence_id": evidence_id,
                }
            )[7:23]
        )
        receipt_id = (
            "manual-evidence-intent-"
            + execution_payload_digest(
                {"workspace_id": self.workspace_id, "project_id": project, "intent_id": intent}
            )[7:]
        )
        receipt = {
            "schema_version": INTENT_SCHEMA,
            "workspace_id": self.workspace_id,
            "project_id": project,
            "intent_id": intent,
            "evidence_id": evidence_id,
            "material_digest": digest,
        }
        prior = self._read(INTENT_AGGREGATE, receipt_id)
        if prior is not None and prior != receipt:
            raise ManualEvidenceBlocked("manual evidence intent conflicts with different input")
        existing = self._read(MANUAL_EVIDENCE_AGGREGATE, record_id)
        if prior is not None:
            if existing is None:
                raise ManualEvidenceBlocked("original manual evidence result is unavailable")
            return self._result(existing, existing_evidence_id=evidence_id)

        ref = self.objects.publish_bytes(project, content, media_type="application/json")
        if (
            ref.project_id != project
            or ref.digest != digest
            or self.objects.read_bytes(ref) != content
        ):
            raise ManualEvidenceBlocked("published manual material bytes differ")
        evidence = self._evidence_fact(
            facts=facts,
            attempt=attempt,
            evidence_id=evidence_id,
            ref=ref,
            document=safe,
            attachments=attachments,
        )
        self.unit.begin(request_id, project, intent_id=intent)
        try:
            self.unit.stage_record(
                aggregate_kind=EVIDENCE_AGGREGATE,
                record_id=evidence_id,
                expected_revision=0,
                payload=evidence.model_dump(mode="json"),
            )
            self.unit.stage_record(
                aggregate_kind=MANUAL_EVIDENCE_AGGREGATE,
                record_id=record_id,
                expected_revision=0,
                payload={
                    "schema_version": MANUAL_SCHEMA,
                    "record_id": record_id,
                    "project_id": project,
                    "workspace_id": self.workspace_id,
                    "evidence_id": evidence_id,
                    "run_id": document["run_id"],
                    "step_id": document["step_id"],
                    "attempt_id": document["attempt_id"],
                    "operation_type": document["operation_type"],
                    "target_ref": document["target_ref"],
                    "observed_result": document["observed_result"],
                    "business_object_id": document["business_object_id"],
                    "input_summary": document.get("input_summary") or "",
                    "attachment_refs": [asdict(item) for item in attachments],
                    "material_digest": digest,
                    "object_ref": asdict(ref),
                    "code_identity": evidence.code_identity.model_dump(mode="json"),
                    "evidence_kind": evidence.evidence_kind.value,
                    "capture_source": evidence.capture_source.value,
                    "evidence_level": evidence.evidence_level.value,
                    "gap_ids": list(evidence.gap_ids),
                    "business_passed": False,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            self.unit.stage_record(
                aggregate_kind=INTENT_AGGREGATE,
                record_id=receipt_id,
                expected_revision=0,
                payload=receipt,
            )
            self.unit.commit()
        except BaseException:
            self.unit.rollback()
            raise
        stored = self._read(MANUAL_EVIDENCE_AGGREGATE, record_id)
        if stored is None:
            raise ManualEvidenceBlocked("manual evidence was not committed")
        return self._result(stored, existing_evidence_id=evidence_id, request_id=request_id)

    # ------------------------------------------------------------ 校验

    def _document(self, parameters: Mapping[str, object]) -> dict[str, object]:
        unknown = set(parameters) - set(_TEXT_FIELDS) - set(_OPTIONAL_TEXT) - {
            "attachment_refs"
        }
        if unknown:
            raise ManualEvidenceBlocked(
                f"attach_evidence received unknown parameters: {sorted(unknown)}"
            )
        document: dict[str, object] = {
            name: _text(parameters.get(name), name) for name in _TEXT_FIELDS
        }
        if document["operation_type"] not in _OPERATIONS:
            raise ManualEvidenceBlocked(
                f"unknown manual operation type: {document['operation_type']}"
            )
        summary = parameters.get("input_summary")
        if summary is not None:
            document["input_summary"] = _text(summary, "input_summary")
        raw_attachments = parameters.get("attachment_refs") or []
        if not isinstance(raw_attachments, list) or len(raw_attachments) > _MAX_ATTACHMENTS:
            raise ManualEvidenceBlocked("manual attachments exceed their explicit budget")
        # 文档里保存**已核对的对象引用**（字典形状），对象本身在 `_attachments` 里核对。
        document["attachment_refs"] = [asdict(_object_ref(item)) for item in raw_attachments]
        return document

    def _safe_document(self, document: Mapping[str, object]) -> dict[str, object]:
        """凭据过滤**不可替换**：过滤会改变材料即拒绝，绝不把过滤后字节当原文。"""

        payload: dict[str, object] = {}
        for key, value in document.items():
            if key == "attachment_refs":
                payload[key] = [
                    item["relative_path"]
                    for item in cast(list[Mapping[str, object]], value)
                ]
            else:
                payload[key] = value
        if self.protector(payload) != payload:
            raise ManualEvidenceBlocked(
                "manual material contains credentials or unsafe fields; it was not saved"
            )
        return dict(document)

    def _attachments(
        self, document: Mapping[str, object], project: str
    ) -> tuple[StoredObjectRef, ...]:
        raw = document.get("attachment_refs") or []
        assert isinstance(raw, list)
        verified: list[StoredObjectRef] = []
        seen: set[str] = set()
        for item in raw:
            ref = _object_ref(item)
            if ref.project_id != project or not 0 < ref.size <= _MAX_BYTES:
                raise ManualEvidenceBlocked("manual attachment scope or size is invalid")
            if ref.digest in seen:
                raise ManualEvidenceBlocked("manual attachments must be unique")
            seen.add(ref.digest)
            try:
                content = self.objects.read_bytes(ref)
            except (OSError, ValueError) as error:
                raise ManualEvidenceBlocked(
                    f"manual attachment bytes cannot be verified: {error}"
                ) from error
            if "sha256:" + hashlib.sha256(content).hexdigest() != ref.digest:
                raise ManualEvidenceBlocked("manual attachment digest cannot be verified")
            try:
                text = content.decode("utf-8")
            except UnicodeError as error:
                raise ManualEvidenceBlocked(
                    "manual attachments must be readable text"
                ) from error
            if self.protector({"attachment": text}) != {"attachment": text}:
                raise ManualEvidenceBlocked("manual attachment contains unsafe material")
            verified.append(ref)
        return tuple(verified)

    def _facts(self, project: str, run_id: str) -> Any:
        try:
            facts = self.coordinator.read_runtime_revision_facts(
                project_id=project, run_id=run_id
            )
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ManualEvidenceBlocked(f"saved run facts are unavailable: {error}") from error
        if facts is None or facts.project_id != project:
            raise ManualEvidenceBlocked("saved run facts belong to another project")
        return facts

    def _target(self, facts: Any, document: Mapping[str, object]) -> tuple[Any, Any]:
        step_id, attempt_id = cast(str, document["step_id"]), cast(str, document["attempt_id"])
        step = next((item for item in facts.steps if item.step_id == step_id), None)
        if step is None:
            raise ManualEvidenceBlocked("manual evidence requires a saved step of the run")
        attempt = next((item for item in facts.attempts if item.attempt_id == attempt_id), None)
        if attempt is None or attempt.step_id != step_id:
            raise ManualEvidenceBlocked("manual evidence requires the attempt of that step")
        current = facts.current_attempt_by_step.get(step_id, step.current_attempt_id)
        # 补证针对**当前尝试**：历史尝试的材料属于历史，不能补成当前依据。
        if current != attempt_id or not attempt.is_current:
            raise ManualEvidenceBlocked("manual evidence requires the current attempt")
        if attempt.state is not AttemptStateFact.COMPLETED:
            raise ManualEvidenceBlocked(
                "manual evidence requires a completed attempt (unknown outcomes stay unknown)"
            )
        return step, attempt

    def _evidence_fact(
        self,
        *,
        facts: Any,
        attempt: Any,
        evidence_id: str,
        ref: StoredObjectRef,
        document: Mapping[str, object],
        attachments: tuple[StoredObjectRef, ...],
    ) -> EvidenceFact:
        """人工证据的 CodeIdentity 取**同一份已保存准备**，不猜绑定形式。"""

        prepared = self._prepared(facts.project_id, facts.run.intent_id)
        attachments_refs = tuple(item.relative_path for item in attachments)
        return EvidenceFact(
            evidence_id=evidence_id,
            evidence_revision=1,
            project_id=facts.project_id,
            source_instance_id=facts.run.run_id,
            run_id=facts.run_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            evidence_kind=(
                EvidenceKindFact.ATTACHMENT
                if attachments_refs and not document.get("observed_result")
                else EvidenceKindFact.MANUAL_STEP
            ),
            capture_source=EvidenceCaptureSourceFact.MANUAL,
            code_identity=CodeIdentityFact(
                binding_kind=SourceBindingKindFact(prepared.binding_form.value),
                workspace_ref=facts.run.origin_workspace_id,
                commit_id=prepared.git_base_commit,
                file_manifest_digest=prepared.plain_manifest_digest,
                revision_ref=prepared.snapshot.source_snapshot_id,
            ),
            object_digest=ref.digest,
            object_size=ref.size,
            media_type=ref.media_type,
            integrity=EvidenceIntegrityFact.COMPLETE,
            redaction_state=RedactionStateFact.NOT_REQUIRED,
            projection_state=ProjectionStateFact.DISPLAYABLE,
            evidence_level=EvidenceLevelFact.MANUAL_ASSOCIATED,
            gap_ids=_MANUAL_GAPS,
            created_at=None,
        )

    def _prepared(self, project: str, run_intent_id: str) -> Any:
        receipt = self.reader.find_preparation_by_intent(intent_id=run_intent_id)
        if receipt is None or getattr(receipt, "cancelled", False):
            raise ManualEvidenceBlocked("saved preparation receipt is unavailable")
        request = getattr(receipt, "request", None)
        if request is None:
            raise ManualEvidenceBlocked("saved preparation receipt is unreadable")
        prepared_run_id = "prepared-" + preparation_identity_digest(
            project_id=project,
            client_id=request.client_id,
            prepare_request_id=request.prepare_request_id,
        )
        try:
            prepared, _digest = load_saved_preparation(
                reader=self.reader,
                project_id=project,
                workspace_id=self.workspace_id,
                prepared_run_id=prepared_run_id,
            )
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ManualEvidenceBlocked(
                f"saved preparation cannot be verified: {error}"
            ) from error
        return prepared

    # ------------------------------------------------------------ 读写

    def _read(self, aggregate_kind: str, record_id: str) -> dict[str, Any] | None:
        revision = self.reader.current_revision(
            aggregate_kind=aggregate_kind, record_id=record_id
        )
        if type(revision) is not int or revision < 0:
            raise ManualEvidenceBlocked("warehouse revision cannot be verified")
        if revision == 0:
            return None
        record = self.reader.read(
            aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
        )
        payload = getattr(record, "payload", None)
        if not isinstance(payload, Mapping):
            raise ManualEvidenceBlocked("saved manual evidence payload is unreadable")
        return dict(payload)

    def _result(
        self,
        stored: Mapping[str, object],
        *,
        existing_evidence_id: str,
        request_id: str = "",
    ) -> Mapping[str, object]:
        if (
            stored.get("schema_version") != MANUAL_SCHEMA
            or stored.get("evidence_id") != existing_evidence_id
            or stored.get("business_passed") is not False
        ):
            raise ManualEvidenceBlocked("saved manual evidence identity cannot be verified")
        return {
            "evidence_id": existing_evidence_id,
            "record_id": str(stored["record_id"]),
            "record_revision": 1,
            "capture_source": str(stored["capture_source"]),
            "evidence_kind": str(stored["evidence_kind"]),
            "evidence_level": str(stored["evidence_level"]),
            "business_passed": False,
            "requires_verification": True,
            "gaps": list(cast(list[str], stored["gap_ids"])),
        }


__all__ = [
    "EVIDENCE_AGGREGATE",
    "INTENT_AGGREGATE",
    "MANUAL_EVIDENCE_AGGREGATE",
    "MANUAL_SCHEMA",
    "ManualEvidenceBlocked",
    "SavedManualEvidenceCapture",
]
