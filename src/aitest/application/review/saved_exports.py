"""持久导出用例：Markdown 摘要与可携带证据包（P1-FR14，架构 03 §5/§8）。

由已保存报告准确修订、冻结执行快照、准确问题修订与所选本地复核生成内容。
调用方只给标识与选择器；附件限定在原报告证据集合，缺准确来源不回落当前运行。
内容经安全投影，坏/缺材料登记缺口，不生成伪装为有效证据的空附件。

原意图先回读其永久制品并核对字节；同冻结输入的新意图复用同一制品且保存意图。
制品保存、摘要校验与记录登记全部完成才报成功。包内路径必须是规范相对路径。
markdown_summary 声明附件未随文件交付并列证据索引；evidence_bundle 保存
report.md、manifest.json 与安全附件。原冻结材料和已交付制品不改写。
"""

from __future__ import annotations

import hashlib
import io
import json
import stat
import unicodedata
import zipfile
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol, cast

from aitest.application.execution.runtime_revision import (
    SavedRuntimeRevisionReader,
    SnapshotContentRef,
)
from aitest.application.planning.model_ports import Projection
from aitest.application.planning.substrate import RecordReader
from aitest.application.review.saved_defects import (
    ISSUE_AGGREGATE,
    blocking_state,
    issue_from_payload,
)
from aitest.application.review.saved_reports import (
    LOCAL_REVIEW_AGGREGATE,
    REPORT_AGGREGATE,
    ReportActorReader,
    ReportClock,
    ReportManagementError,
    ReportRecordRepository,
    ReportRevisionConflict,
    ReportUnitOfWork,
    local_review_from_payload,
    report_from_payload,
)
from aitest.contracts.execution_facts import EvidenceFact, ExecutionFacts
from aitest.contracts.reuse_reference import parse_reuse_reference
from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.domain.json_material import decode_json
from aitest.domain.planning.model_outbound import MaterialKind
from aitest.domain.review.reports import (
    ReportExport,
    ReportExportKey,
    ReportExportKind,
    ReportSnapshot,
    create_report_export,
)

EXPORT_AGGREGATE = "report_export"
EXPORT_INTENT_AGGREGATE = "report_export_intent"
EXPORT_SCHEMA = "aitest.report-export/1.0"
EXPORT_INTENT_SCHEMA = "aitest.report-export-intent/1.0"
MANIFEST_FORMAT_VERSION = "aitest.evidence-bundle/1.0"
DEFAULT_REDACTION_POLICY_VERSION = "aitest.redaction/1.0"

SUPPORTED_ACTIONS = ("export_report",)


class ExportManagementError(ReportManagementError):
    """导出用例错误；错误码沿用已登记集合。"""


class ExportConflict(ReportRevisionConflict):
    """导出引用的修订与实际修订不一致（复用已登记错误码）。"""


class ExportNotSaved(ExportManagementError):
    code = "RECORD_NOT_FOUND"


class ExportIntentConflict(ExportManagementError):
    code = "INTENT_CONFLICT"


class ExportRefused(ExportManagementError):
    code = "INVALID_REQUEST"


class ExportArtifactPort(Protocol):
    def write_artifact(
        self,
        *,
        request_id: str,
        workspace_id: str,
        report_id: str,
        kind: str,
        content: bytes,
        media_type: str = "application/octet-stream",
    ) -> Any: ...
    def verify_artifact(
        self,
        *,
        request_id: str,
        workspace_id: str,
        report_id: str,
        expected_sha256: str,
    ) -> Any: ...


class ExportProjectionPort(Protocol):
    def project(
        self,
        *,
        material: Mapping[MaterialKind, str],
        source_snippets_enabled: bool,
    ) -> Projection: ...


class ExportRuntimeFactsReader(Protocol):
    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> ExecutionFacts: ...


_WINDOWS_DEVICE_NAMES = frozenset(
    ("CON", "PRN", "AUX", "NUL")
    + tuple(f"COM{digit}" for digit in "123456789¹²³")
    + tuple(f"LPT{digit}" for digit in "123456789¹²³")
)
_MANIFEST_METADATA_LIMIT_BYTES = 8 * 1024 * 1024


def _portable_member_key(path: str) -> str:
    """Verify a portable relative member; return its conservative collision key."""
    for component in path.split("/"):
        if (
            not component
            or component in {".", ".."}
            or component.endswith((" ", "."))
            or any(ord(char) < 32 or ord(char) == 127 or char in '<>:"\\|?*' for char in component)
            or component.partition(".")[0].rstrip(" ").upper() in _WINDOWS_DEVICE_NAMES
        ):
            raise ExportRefused("evidence bundle member path is not portable and relative")
        try:
            if len(component.encode("utf-8")) > 255:
                raise ExportRefused("evidence bundle member component is too long")
        except UnicodeError as error:
            raise ExportRefused("evidence bundle member path cannot be encoded") from error
    return unicodedata.normalize("NFC", path).casefold()


def _verify_bundle_paths(content: bytes) -> None:
    """Refuse unsafe old artifacts on both intent recall and frozen-key reuse.

    Read directory metadata and a bounded manifest; never extract attachments.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if not {"report.md", "manifest.json"} <= {item.filename for item in members}:
                raise ExportRefused("saved evidence bundle lacks its required members")
            keys: set[str] = set()
            for item in members:
                key = _portable_member_key(item.orig_filename)
                if key in keys:
                    raise ExportRefused("evidence bundle member paths collide")
                if item.filename != item.orig_filename or stat.S_IFMT(
                    item.external_attr >> 16
                ) not in {
                    0,
                    stat.S_IFREG,
                }:
                    raise ExportRefused("evidence bundle member is not a regular portable file")
                keys.add(key)
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > _MANIFEST_METADATA_LIMIT_BYTES:
                raise ExportRefused("evidence bundle manifest exceeds its verified read budget")
            if manifest_info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ExportRefused("evidence bundle manifest compression is unavailable")
            with archive.open(manifest_info) as stream:
                raw = stream.read(_MANIFEST_METADATA_LIMIT_BYTES + 1)
            if len(raw) > _MANIFEST_METADATA_LIMIT_BYTES:
                raise ExportRefused("evidence bundle manifest exceeds its verified read budget")
            manifest = decode_json(raw)
            if (
                not isinstance(manifest, Mapping)
                or manifest.get("format_version") != MANIFEST_FORMAT_VERSION
                or not isinstance(manifest.get("files"), list)
                or not isinstance(manifest.get("attachments"), list)
            ):
                raise ExportRefused("evidence bundle manifest paths cannot be verified")
            by_name = {item.filename: item for item in members}
            declared: set[str] = set()
            for item in manifest["files"]:
                if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                    raise ExportRefused("evidence bundle manifest member is unreadable")
                path = item["path"]
                _portable_member_key(path)
                if (
                    path in declared
                    or path == "manifest.json"
                    or path not in by_name
                    or type(item.get("size")) is not int
                    or item["size"] != by_name[path].file_size
                ):
                    raise ExportRefused("evidence bundle manifest differs from its directory")
                declared.add(path)
            if declared != set(by_name) - {"manifest.json"}:
                raise ExportRefused("evidence bundle manifest does not bind every member")
            attachments: set[str] = set()
            for item in manifest["attachments"]:
                if (
                    not isinstance(item, Mapping)
                    or not isinstance(item.get("path"), str)
                    or item["path"] in attachments
                    or item["path"] not in declared - {"report.md"}
                ):
                    raise ExportRefused("evidence bundle attachment paths cannot be verified")
                attachments.add(item["path"])
            if attachments != declared - {"report.md"}:
                raise ExportRefused("evidence bundle attachment manifest is incomplete")
    except ExportRefused:
        raise
    except (zipfile.BadZipFile, UnicodeError, ValueError, NotImplementedError) as error:
        raise ExportRefused("saved evidence bundle directory cannot be verified") from error


def _digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _bytes_digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExportManagementError(f"{name} must be nonempty text")
    return value


def _text_list(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ExportManagementError(f"{name} must be a list of strings")
    items = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ExportManagementError(f"{name} must be a list of nonempty strings")
    if len(set(items)) != len(items):
        raise ExportManagementError(f"{name} must not repeat values")
    return items


def export_key_id(key: ReportExportKey) -> str:
    """导出键的稳定记录标识：同一冻结输入只有一个导出记录。"""

    return (
        "export-"
        + _digest(
            [
                key.report_id,
                key.report_revision,
                list(key.review_ids),
                key.kind.value,
                key.redaction_policy_version,
                list(key.attachment_refs),
            ]
        )[7:]
    )


def _markdown(
    snapshot: ReportSnapshot,
    reviews: tuple[Any, ...],
    *,
    issue_titles: Mapping[str, str],
    evidence_index: tuple[Mapping[str, object], ...],
    declarations: tuple[str, ...],
) -> str:
    context = snapshot.context
    decision = snapshot.decision
    lines = [
        f"# 测试报告 {snapshot.report_id}（内容修订 {snapshot.content_revision}）",
        "",
        f"- 项目：{context.project_id}",
        f"- 运行：{context.run_id}（运行修订 {context.run_revision}）",
        f"- 范围：{context.scope_name}（{context.scope_revision}）",
        f"- 来源身份：{context.source_identity}",
        f"- 环境：{context.environment_ref}；规则：{context.rules_revision}",
        f"- 验收范围修订：{context.acceptance_revision}；模板：{context.template_revision}",
        f"- 判定策略版本：{context.policy_version}",
        "",
        "## 判定",
        "",
        f"- 业务结论：{decision.business_outcome.value}",
        "- 证据等级："
        + (decision.evidence_grade.value if decision.evidence_grade is not None else "不适用"),
        # 覆盖数字给的是计数，不展开具体用例（详情按引用读取）。
        "- 覆盖计数："
        + ",".join(
            f"{name}={len(value)}"
            for name, value in (
                ("S", decision.coverage.selected),
                ("M", decision.coverage.required),
                ("E", decision.coverage.executed),
                ("R", decision.coverage.reused),
                ("V", decision.coverage.verified),
                ("P", decision.coverage.passed),
            )
        ),
        "",
        "## 缺口",
        "",
    ]
    if decision.gaps:
        for gap in decision.gaps:
            subject = f"（{gap.subject_id}）" if gap.subject_id else ""
            lines.append(f"- {gap.code.value}{subject}：{gap.safe_reason}")
    else:
        lines.append("- 无")
    lines.extend(["", "## 决定性失败（H）", ""])
    if decision.decisive_failures:
        for failure in decision.decisive_failures:
            lines.append(
                f"- 用例 {failure.case_id}／步骤 {failure.step_id}／尝试 {failure.attempt_id}"
                f"／断言 {failure.assertion_ref}：证据 {', '.join(failure.evidence_refs)}"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## 关联问题（冻结修订）", ""])
    if snapshot.issue_revisions:
        for issue_id, revision in snapshot.issue_revisions:
            title = issue_titles.get(issue_id, "（标题不可读）")
            lines.append(f"- {issue_id}@{revision}：{title}")
    else:
        lines.append("- 无")
    lines.extend(["", "## 本地复核", ""])
    if reviews:
        for review in reviews:
            lines.append(
                f"- {review.review_id}（{review.reviewer}，{review.conclusion}）："
                f"{review.explanation}"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## 证据索引（只给引用，正文不内联）", ""])
    if evidence_index:
        for item in evidence_index:
            if item.get("status") == "missing":
                lines.append(
                    f"- {item['evidence_id']}：材料缺失（{item.get('reason', 'unknown')}）"
                )
                continue
            lines.append(
                "- {evidence_id}（{evidence_kind}，大小 {size}，摘要 {digest}）{note}".format(
                    evidence_id=item["evidence_id"],
                    evidence_kind=item["evidence_kind"],
                    size=item["size"],
                    digest=item["sha256"],
                    note=item.get("note", ""),
                )
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## 声明", ""])
    for declaration in declarations:
        lines.append(f"- {declaration}")
    return "\n".join(lines) + "\n"


class ExportObjectStore(Protocol):
    """永久对象库：成功导出的制品先在此保存并纳入已提交引用（架构 03 §8）。"""

    def publish_bytes(self, project_id: str, content: bytes, *, media_type: str = ...) -> Any: ...
    def read_bytes(self, ref: Any, *, max_bytes: int | None = None) -> bytes: ...


class SavedExportService:
    """导出用例：内容生成 + 制品发布 + 幂等登记。"""

    def __init__(
        self,
        *,
        unit: ReportUnitOfWork,
        reader: RecordReader,
        records: ReportRecordRepository,
        clock: ReportClock,
        actors: ReportActorReader,
        runtime: ExportRuntimeFactsReader,
        workspace_id: str,
        artifacts: ExportArtifactPort,
        projector: ExportProjectionPort | None,
        objects: ExportObjectStore | None = None,
        instance_id: str = "local-core",
    ) -> None:
        for name, value in (
            ("unit", unit),
            ("reader", reader),
            ("records", records),
            ("clock", clock),
            ("actors", actors),
            ("runtime", runtime),
            ("artifacts", artifacts),
            # 永久制品必须先入对象库：`artifact_digest` 是已提交引用，
            # 没有对象就是未保存的导出（存储侧会直接拒绝该记录）。
            ("objects", objects),
        ):
            if value is None:
                raise ExportManagementError(
                    f"export use cases require the {name} capability",
                    code="CAPABILITY_UNAVAILABLE",
                )
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ExportManagementError("export use cases require a workspace identity")
        self._unit, self._reader, self._records = unit, reader, records
        self._clock, self._actors, self._runtime = clock, actors, runtime
        self._workspace_id = workspace_id
        self._artifacts = artifacts
        self._projector = projector
        self._objects = objects
        self._instance_id = instance_id

    # ------------------------------------------------------------ 动作

    def export_report(self, command: object) -> Mapping[str, object]:
        project_id = _text(getattr(command, "project_id", None), "project_id")
        intent_id = _text(getattr(command, "intent_id", None), "intent_id")
        request_id = _text(getattr(command, "request_id", None), "request_id")
        parameters = getattr(command, "parameters", None)
        if not isinstance(parameters, Mapping):
            raise ExportManagementError("parameters must be an object")
        allowed = {
            "report_id",
            "kind",
            "review_ids",
            "attachment_refs",
            "redaction_policy_version",
            "run_id",
        }
        unknown = set(parameters) - allowed
        if unknown:
            raise ExportManagementError(
                f"export_report received unknown parameters: {sorted(unknown)}"
            )
        report_id = _text(parameters.get("report_id"), "report_id")
        kind = ReportExportKind(_text(parameters.get("kind"), "kind"))
        review_ids = _text_list(parameters.get("review_ids"), "review_ids")
        attachment_refs = _text_list(parameters.get("attachment_refs"), "attachment_refs")
        policy = _text(
            parameters.get("redaction_policy_version") or DEFAULT_REDACTION_POLICY_VERSION,
            "redaction_policy_version",
        )
        if policy != DEFAULT_REDACTION_POLICY_VERSION:
            raise ExportRefused("requested export redaction policy is unavailable")
        run_id = parameters.get("run_id")
        run_id = None if run_id is None else _text(run_id, "run_id")

        command_input = {
            "report_id": report_id,
            "kind": kind.value,
            "review_ids": list(review_ids),
            "attachment_refs": list(attachment_refs),
            "redaction_policy_version": policy,
            "run_id": run_id,
        }
        fingerprint = _digest(["export_report", command_input])
        intent_record_id = "export-intent-" + _digest([project_id, intent_id])[7:]
        recalled = self._recall(project_id, intent_record_id, fingerprint, command_input)
        if recalled is not None:
            return recalled

        record = self._current(REPORT_AGGREGATE, report_id)
        if record is None:
            raise ExportNotSaved(f"report {report_id} is not saved")
        if record.get("project_id") != project_id:
            raise ExportNotSaved(f"report {report_id} belongs to another project")
        snapshot = report_from_payload(record)
        if run_id is not None and run_id != snapshot.context.run_id:
            raise ExportRefused("export evidence must belong to the frozen report run")
        reviews = self._reviews(project_id, snapshot, review_ids)

        key = ReportExportKey(
            report_id=report_id,
            report_revision=snapshot.content_revision,
            review_ids=review_ids,
            kind=kind,
            redaction_policy_version=policy,
            attachment_refs=attachment_refs,
        )
        # 导出身份由冻结输入决定：调用方给不出 export_id，也拿不到"另造一个导出"的口子。
        key_record_id = export_key_id(key)
        existing = self._existing_export(key_record_id)
        if existing is not None:
            self._verify_saved_artifact(project_id, key_record_id)
            return self._commit(
                project_id=project_id,
                key_record_id=key_record_id,
                intent_id=intent_id,
                request_id=request_id,
                intent_record_id=intent_record_id,
                fingerprint=fingerprint,
                payload={},
                reuse_existing=True,
                command_input=command_input,
            )

        facts = None
        frozen = record.get("execution_snapshot_ref")
        if frozen is not None:
            try:
                reference = SnapshotContentRef.model_validate_json(json.dumps(frozen), strict=True)
                facts = SavedRuntimeRevisionReader(self._records).read_snapshot(  # type: ignore[arg-type]
                    project_id=project_id,
                    run_id=snapshot.context.run_id,
                    reference=reference,
                )
                if facts.run_revision != snapshot.context.run_revision:
                    raise ExportRefused("frozen report execution revision differs")
            except (OSError, KeyError, TypeError, ValueError):
                # Preserve a readable report and list missing evidence. Never
                # substitute the current run for an unavailable frozen source.
                facts = None
        source_evidence, source_gaps = self._frozen_reuse_evidence(snapshot, record)
        content, media_type, excluded, missing = self._content(
            snapshot,
            reviews,
            kind=kind,
            attachment_refs=attachment_refs,
            facts=facts,
            source_evidence=source_evidence,
            source_gaps=source_gaps,
        )
        # 1) 永久制品先入对象库（凭据在落盘前过滤；摘要基于过滤后字节）。
        object_ref = self._objects.publish_bytes(  # type: ignore[union-attr]
            project_id, content, media_type=media_type
        )
        sanitized = self._objects.read_bytes(object_ref)  # type: ignore[union-attr]
        if sanitized != content:
            excluded = [
                *excluded,
                {
                    "material_kind": "artifact",
                    "field_path": "artifact.credential_filtered",
                },
            ]
        # 2) 交付副本写事务临时位置、核对摘要后才发布。
        written = self._artifacts.write_artifact(
            request_id=request_id,
            workspace_id=self._workspace_id,
            report_id=report_id,
            kind=kind.value,
            content=sanitized,
            media_type=media_type,
        )
        result = getattr(written, "result", None)
        if not isinstance(result, Mapping) or written.error is not None:
            raise ExportRefused(
                "artifact publication did not report a verified product",
                code="INTEGRITY_CHECK_FAILED",
            )
        artifact_ref = _text(result.get("artifact_ref"), "artifact_ref")
        artifact_digest = _text(result.get("artifact_digest"), "artifact_digest")
        if artifact_digest != _text(getattr(object_ref, "digest", None), "object digest"):
            raise ExportRefused(
                "published artifact digest differs from the stored object identity",
                code="INTEGRITY_CHECK_FAILED",
            )
        if artifact_digest != _bytes_digest(sanitized):
            raise ExportRefused(
                "published artifact digest differs from the exported bytes",
                code="INTEGRITY_CHECK_FAILED",
            )
        verified = self._artifacts.verify_artifact(
            request_id=request_id,
            workspace_id=self._workspace_id,
            report_id=report_id,
            expected_sha256=artifact_digest,
        )
        verified_result = getattr(verified, "result", None)
        if not isinstance(verified_result, Mapping) or verified_result.get("status") != "verified":
            raise ExportRefused(
                "published artifact could not be re-verified; it is not a success",
                code="INTEGRITY_CHECK_FAILED",
            )

        export = create_report_export(
            snapshot,
            reviews,
            (),
            export_id=key_record_id,
            report_revision=snapshot.content_revision,
            review_ids=review_ids,
            kind=kind,
            redaction_policy_version=policy,
            artifact_ref=artifact_ref,
            artifact_digest=artifact_digest,
            attachment_refs=attachment_refs,
            created_at=self._clock.now(),
        )
        payload = self._export_payload(
            export,
            snapshot=snapshot,
            reviews=reviews,
            excluded=excluded,
            missing=missing,
            size_bytes=len(sanitized),
            media_type=media_type,
            object_ref=object_ref,
        )
        return self._commit(
            project_id=project_id,
            key_record_id=key_record_id,
            intent_id=intent_id,
            request_id=request_id,
            intent_record_id=intent_record_id,
            fingerprint=fingerprint,
            payload=payload,
            command_input=command_input,
        )

    # ------------------------------------------------------------ 内容生成

    def _frozen_reuse_evidence(
        self,
        snapshot: ReportSnapshot,
        record: Mapping[str, object],
    ) -> tuple[tuple[EvidenceFact, ...], tuple[dict[str, object], ...]]:
        reused = snapshot.decision.coverage.reused
        if not reused:
            return (), ()
        raw = record.get("reuse")
        if not isinstance(raw, Mapping) or "references" not in raw or "source_snapshots" not in raw:
            return (), tuple(
                {"case_id": case, "reason": "frozen_reuse_sources_unavailable"}
                for case in sorted(reused)
            )
        try:
            references_raw, snapshots_raw = raw["references"], raw["source_snapshots"]
            if not isinstance(references_raw, list) or not isinstance(snapshots_raw, list):
                raise ValueError("frozen reuse sources are not finite lists")
            references = tuple(parse_reuse_reference(item) for item in references_raw)
            if len(references) != len(reused) or {item.case_id for item in references} != reused:
                raise ValueError("frozen reuse cases differ from the report")
            if any(item.target_run_id != snapshot.context.run_id for item in references):
                raise ValueError("frozen reuse reference targets another run")
            sources: dict[str, SnapshotContentRef] = {}
            for item in snapshots_raw:
                if not isinstance(item, Mapping) or set(item) != {"run_id", "snapshot_ref"}:
                    raise ValueError("frozen reuse source identity is unreadable")
                run_id = _text(item.get("run_id"), "source run_id")
                if run_id in sources or run_id == snapshot.context.run_id:
                    raise ValueError("frozen reuse sources conflict")
                sources[run_id] = SnapshotContentRef.model_validate_json(
                    json.dumps(item["snapshot_ref"]), strict=True
                )
            if set(sources) != {item.source_run_id for item in references}:
                raise ValueError("frozen reuse sources differ from the references")
        except (KeyError, TypeError, ValueError) as error:
            raise ExportRefused("frozen reuse metadata cannot be verified") from error
        evidence: dict[str, EvidenceFact] = {}
        gaps: list[dict[str, object]] = []
        for run_id, reference in sources.items():
            try:
                facts = SavedRuntimeRevisionReader(self._records).read_snapshot(  # type: ignore[arg-type]
                    project_id=snapshot.context.project_id,
                    run_id=run_id,
                    reference=reference,
                )
            except (OSError, KeyError, TypeError, ValueError):
                gaps.append(
                    {"source_run_id": run_id, "reason": "frozen_reuse_snapshot_unavailable"}
                )
                continue
            by_id = {item.evidence_id: item for item in facts.evidence_refs}
            steps = {item.step_id: item for item in facts.steps}
            attempts = {item.attempt_id: item for item in facts.attempts}
            for selected in (item for item in references if item.source_run_id == run_id):
                for step_id, attempt_id in selected.source_attempt_by_step:
                    step, attempt = steps.get(step_id), attempts.get(attempt_id)
                    if (
                        step is None
                        or step.case_id != selected.case_id
                        or attempt is None
                        or attempt.step_id != step_id
                        or attempt.run_id != run_id
                    ):
                        raise ExportRefused("frozen reuse attempt mapping belongs elsewhere")
                allowed_attempts = {item[1] for item in selected.source_attempt_by_step}
                for identity in selected.evidence_refs:
                    item = by_id.get(identity)
                    if (
                        identity not in snapshot.evidence_refs
                        or item is None
                        or item.attempt_id not in allowed_attempts
                        or item.run_id != run_id
                        or item.project_id != snapshot.context.project_id
                    ):
                        raise ExportRefused("frozen reuse evidence belongs elsewhere")
                    previous = evidence.get(identity)
                    if previous is not None and previous != item:
                        raise ExportRefused("frozen reuse evidence identity conflicts")
                    evidence[identity] = item
        return tuple(evidence[key] for key in sorted(evidence)), tuple(gaps)

    @staticmethod
    def _evidence_by_id(
        facts: ExecutionFacts | None,
        source_evidence: tuple[EvidenceFact, ...],
    ) -> dict[str, EvidenceFact]:
        result = {item.evidence_id: item for item in (facts.evidence_refs if facts else ())}
        for item in source_evidence:
            previous = result.get(item.evidence_id)
            if previous is not None and previous != item:
                raise ExportRefused("frozen evidence identity conflicts across runs")
            result[item.evidence_id] = item
        return result

    def _content(
        self,
        snapshot: ReportSnapshot,
        reviews: tuple[Any, ...],
        *,
        kind: ReportExportKind,
        attachment_refs: tuple[str, ...],
        facts: ExecutionFacts | None,
        source_evidence: tuple[EvidenceFact, ...] = (),
        source_gaps: tuple[dict[str, object], ...] = (),
    ) -> tuple[bytes, str, list[dict[str, object]], list[dict[str, object]]]:
        issue_titles = self._issue_titles(snapshot)
        evidence_index, missing = self._evidence_index(
            snapshot, attachment_refs, facts, source_evidence=source_evidence
        )
        missing.extend(source_gaps)
        declarations = [
            "本文件是本地过程记录，不引入签名或平台审核保证。",
            "证据正文不内联；按证据标识引用，附件不随本文件交付。"
            if kind is ReportExportKind.MARKDOWN_SUMMARY
            else "证据附件按投影策略脱敏后随包交付。",
        ]
        declarations.extend(
            f"复用来源材料缺口：{item['reason']}；不以当前源运行替代。" for item in source_gaps
        )
        markdown = _markdown(
            snapshot,
            reviews,
            issue_titles=issue_titles,
            evidence_index=tuple(evidence_index),
            declarations=tuple(declarations),
        )
        projected, excluded = self._project(
            {
                MaterialKind.CASE_CONTENT: markdown,
            }
        )
        body = projected.get(MaterialKind.CASE_CONTENT, markdown)
        if kind is ReportExportKind.MARKDOWN_SUMMARY:
            return body.encode("utf-8"), "text/markdown; charset=utf-8", excluded, missing
        return (
            self._bundle(
                body,
                snapshot,
                reviews,
                evidence_index=evidence_index,
                excluded=excluded,
                missing=missing,
                facts=facts,
                source_evidence=source_evidence,
            ),
            "application/zip",
            excluded,
            missing,
        )

    def _project(
        self, material: Mapping[MaterialKind, str]
    ) -> tuple[dict[MaterialKind, str], list[dict[str, object]]]:
        """脱敏是导出底线：没有投影器就拒绝导出，不输出未脱敏正文。"""

        if self._projector is None:
            raise ExportRefused(
                "export requires the redaction projector capability",
                code="CAPABILITY_UNAVAILABLE",
            )
        projection = self._projector.project(material=material, source_snippets_enabled=False)
        excluded: list[dict[str, object]] = [
            {"material_kind": kind_value.value, "field_path": path}
            for kind_value, path in projection.excluded
        ]
        # 投影器把"命中凭据标记而整体排除"记为 PARTIAL：此时正文不可交付。
        if MaterialKind.CASE_CONTENT not in {item.material_kind for item in projection.projected}:
            raise ExportRefused("the report body cannot be safely projected")
        return (
            {item.material_kind: item.projected_text for item in projection.projected},
            excluded,
        )

    def _evidence_index(
        self,
        snapshot: ReportSnapshot,
        attachment_refs: tuple[str, ...],
        facts: ExecutionFacts | None,
        *,
        source_evidence: tuple[EvidenceFact, ...] = (),
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        """证据索引：只给引用与摘要；不可安全处理的材料登记缺失/排除原因。"""

        by_id = self._evidence_by_id(facts, source_evidence)
        index: list[dict[str, object]] = []
        missing: list[dict[str, object]] = []
        if any(identity not in snapshot.evidence_refs for identity in attachment_refs):
            raise ExportRefused("attachments must be references frozen in the report")
        for evidence_id in attachment_refs or snapshot.evidence_refs:
            fact = by_id.get(evidence_id)
            if fact is None:
                missing.append({"evidence_id": evidence_id, "reason": "facts_unavailable"})
                index.append(
                    {
                        "evidence_id": evidence_id,
                        "status": "missing",
                        "reason": "frozen_facts_unavailable",
                    }
                )
                continue

            def _value(name: str, item: object = fact) -> object:
                return getattr(getattr(item, name, None), "value", None)

            blocked = (
                _value("integrity") != "complete"
                or _value("redaction_state") == "blocked"
                or _value("projection_state") == "blocked"
                or bool(getattr(fact, "gap_ids", ()))
            )
            entry: dict[str, object] = {
                "evidence_id": evidence_id,
                "evidence_kind": fact.evidence_kind.value,
                "attempt_id": fact.attempt_id,
                "sha256": fact.object_digest,
                "size": fact.object_size,
                "status": "included",
            }
            if blocked:
                entry["status"] = "excluded"
                missing.append(
                    {"evidence_id": evidence_id, "reason": "unsafe_or_incomplete_material"}
                )
            elif evidence_id not in attachment_refs and attachment_refs:
                entry["status"] = "listed_only"
            index.append(entry)
        return index, missing

    def _issue_titles(self, snapshot: ReportSnapshot) -> dict[str, str]:
        titles: dict[str, str] = {}
        for issue_id, revision in snapshot.issue_revisions:
            record = self._records.read(
                aggregate_kind=ISSUE_AGGREGATE,
                record_id=issue_id,
                revision=revision,
            ).payload
            try:
                issue = issue_from_payload(record)
            except ValueError:
                continue
            titles[issue_id] = (
                f"{issue.title}（{issue.status.value}，阻塞={blocking_state(issue)}）"
            )
        return titles

    def _bundle(
        self,
        markdown: str,
        snapshot: ReportSnapshot,
        reviews: tuple[Any, ...],
        *,
        evidence_index: list[dict[str, object]],
        excluded: list[dict[str, object]],
        missing: list[dict[str, object]],
        facts: ExecutionFacts | None,
        source_evidence: tuple[EvidenceFact, ...] = (),
    ) -> bytes:
        """ZIP 包：report.md + manifest.json + evidence/…（全部规范相对路径）。"""

        attachments: list[dict[str, object]] = []
        contents: dict[str, bytes] = {}
        member_keys: set[str] = set()
        by_id = self._evidence_by_id(facts, source_evidence)
        for entry in evidence_index:
            if entry["status"] != "included":
                continue
            evidence_id = cast(str, entry["evidence_id"])
            fact = by_id.get(evidence_id)
            if fact is None or self._objects is None:
                missing.append({"evidence_id": evidence_id, "reason": "evidence_unavailable"})
                continue
            try:
                blob = self._read_object_bytes(fact)
            except ExportRefused:
                missing.append({"evidence_id": evidence_id, "reason": "evidence_unreadable"})
                continue
            relative = f"evidence/{evidence_id}.bin"
            member_key = _portable_member_key(relative)
            if member_key in member_keys:
                raise ExportRefused("evidence bundle member paths collide")
            member_keys.add(member_key)
            contents[relative] = blob
            attachments.append(
                {
                    "path": relative,
                    "sha256": _bytes_digest(blob),
                    "size": len(blob),
                    "source_object_digest": entry["sha256"],
                    "status": "projected_placeholder" if not blob else "projected_bytes",
                }
            )
        report_bytes = markdown.encode("utf-8")
        manifest = {
            "format_version": MANIFEST_FORMAT_VERSION,
            "report_id": snapshot.report_id,
            "report_revision": snapshot.content_revision,
            "review_ids": [review.review_id for review in reviews],
            "review_revisions": {review.review_id: review.report_revision for review in reviews},
            "scope": {
                "scope_revision": snapshot.context.scope_revision,
                "scope_name": snapshot.context.scope_name,
                "acceptance_revision": snapshot.context.acceptance_revision,
            },
            "projection_policy": {
                "redaction_policy_version": DEFAULT_REDACTION_POLICY_VERSION,
                "projected_materials": len(excluded) == 0,
            },
            "files": [
                {
                    "path": "report.md",
                    "sha256": _bytes_digest(report_bytes),
                    "size": len(report_bytes),
                },
                *[
                    {"path": item["path"], "sha256": item["sha256"], "size": item["size"]}
                    for item in attachments
                ],
            ],
            "attachments": attachments,
            "excluded": excluded,
            "missing": missing,
            "generated_at": self._clock.now().isoformat(),
        }
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo("report.md", date_time=(1980, 1, 1, 0, 0, 0))
            archive.writestr(info, report_bytes)
            info = zipfile.ZipInfo("manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
            archive.writestr(info, manifest_bytes)
            for path in sorted(contents):
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                archive.writestr(info, contents[path])
        content = buffer.getvalue()
        _verify_bundle_paths(content)
        return content

    def _read_object_bytes(self, fact: Any) -> bytes:
        from aitest.domain.evidence.evidence import StoredObjectRef

        digest = getattr(fact, "object_digest", None)
        size = getattr(fact, "object_size", None)
        project = getattr(fact, "project_id", None)
        if not isinstance(digest, str) or type(size) is not int or not isinstance(project, str):
            raise ExportRefused("frozen evidence object identity is missing")
        try:
            content = self._objects.read_bytes(  # type: ignore[union-attr]
                StoredObjectRef(
                    project,
                    digest,
                    size,
                    getattr(fact, "media_type", None) or "application/octet-stream",
                    f"objects/{project}/{digest.removeprefix('sha256:')}",
                )
            )
        except (OSError, ValueError) as error:
            raise ExportRefused("frozen evidence object bytes are unreadable") from error
        if (
            not isinstance(content, bytes)
            or len(content) != size
            or _bytes_digest(content) != digest
        ):
            raise ExportRefused("frozen evidence bytes differ from their saved digest/size")
        return content

    # ------------------------------------------------------------ 读取与登记

    def _reviews(
        self, project_id: str, snapshot: ReportSnapshot, review_ids: tuple[str, ...]
    ) -> tuple[Any, ...]:
        reviews: list[Any] = []
        for review_id in review_ids:
            record = self._current(LOCAL_REVIEW_AGGREGATE, review_id)
            if record is None:
                raise ExportNotSaved(f"local review {review_id} is not saved")
            review = local_review_from_payload(record)
            if review.project_id != project_id or review.report_id != snapshot.report_id:
                raise ExportRefused(f"local review {review_id} belongs elsewhere")
            if review.report_revision != snapshot.content_revision:
                raise ExportConflict(
                    expected=review.report_revision, current=snapshot.content_revision
                )
            reviews.append(review)
        return tuple(reviews)

    def _existing_export(self, key_record_id: str) -> ReportExport | None:
        record = self._current(EXPORT_AGGREGATE, key_record_id)
        if record is None:
            return None
        payload = record
        key_raw = payload.get("key")
        if not isinstance(key_raw, Mapping):
            raise ExportManagementError("saved export key is unreadable")
        key = ReportExportKey(
            report_id=_text(key_raw.get("report_id"), "report_id"),
            report_revision=cast(int, key_raw.get("report_revision")),
            review_ids=_text_list(key_raw.get("review_ids"), "review_ids"),
            kind=ReportExportKind(_text(key_raw.get("kind"), "kind")),
            redaction_policy_version=_text(
                key_raw.get("redaction_policy_version"), "redaction_policy_version"
            ),
            attachment_refs=_text_list(key_raw.get("attachment_refs"), "attachment_refs"),
        )
        created = payload.get("created_at")
        return ReportExport(
            export_id=_text(payload.get("export_id"), "export_id"),
            key=key,
            artifact_ref=_text(payload.get("artifact_ref"), "artifact_ref"),
            artifact_digest=_text(payload.get("artifact_digest"), "artifact_digest"),
            created_at=None
            if created is None
            else datetime.fromisoformat(_text(created, "created_at")),
        )

    def _export_payload(
        self,
        export: ReportExport,
        *,
        snapshot: ReportSnapshot,
        reviews: tuple[Any, ...],
        excluded: list[dict[str, object]],
        missing: list[dict[str, object]],
        size_bytes: int,
        media_type: str,
        object_ref: Any,
    ) -> dict[str, object]:
        return {
            "schema_version": EXPORT_SCHEMA,
            "export_id": export.export_id,
            "project_id": snapshot.context.project_id,
            "report_id": export.key.report_id,
            "report_revision": export.key.report_revision,
            "kind": export.key.kind.value,
            "review_ids": list(export.key.review_ids),
            "review_revisions": {review.review_id: review.report_revision for review in reviews},
            "attachment_refs": list(export.key.attachment_refs),
            "redaction_policy_version": export.key.redaction_policy_version,
            # 自描述导出键：相同冻结输入只有一个记录（幂等复用按此重建）。
            "key": {
                "report_id": export.key.report_id,
                "report_revision": export.key.report_revision,
                "review_ids": list(export.key.review_ids),
                "kind": export.key.kind.value,
                "redaction_policy_version": export.key.redaction_policy_version,
                "attachment_refs": list(export.key.attachment_refs),
            },
            "artifact_ref": export.artifact_ref,
            "artifact_digest": export.artifact_digest,
            # 永久对象引用：导出制品必须先由对象库保存（存储侧按此键核对闭包）。
            "object_ref": {
                "project_id": getattr(object_ref, "project_id", None),
                "digest": getattr(object_ref, "digest", None),
                "size": getattr(object_ref, "size", None),
                "media_type": getattr(object_ref, "media_type", None),
                "relative_path": getattr(object_ref, "relative_path", None),
            },
            "size_bytes": size_bytes,
            "media_type": media_type,
            "excluded": excluded,
            "missing": missing,
            "created_at": None if export.created_at is None else export.created_at.isoformat(),
            # 存储侧最小摘要键：
            "content_revision": export.key.report_revision,
        }

    def _facts(self, project_id: str, run_id: str) -> ExecutionFacts:
        try:
            facts = self._runtime.read_runtime_revision_facts(project_id=project_id, run_id=run_id)
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ExportRefused(f"saved run facts are unavailable: {error}") from error
        if not isinstance(facts, ExecutionFacts):
            raise ExportRefused("saved run facts are unreadable")
        return facts

    def _current(self, aggregate_kind: str, record_id: str) -> dict[str, Any] | None:
        revision = self._records.current_revision(
            aggregate_kind=aggregate_kind, record_id=record_id
        )
        if type(revision) is not int or revision < 0:
            raise ExportManagementError("warehouse revision cannot be verified")
        if revision == 0:
            return None
        record = self._records.read(
            aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
        )
        if (
            getattr(record, "aggregate_kind", None),
            getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != (aggregate_kind, record_id, revision):
            raise ExportManagementError("saved record envelope cannot be verified")
        payload = getattr(record, "payload", None)
        if not isinstance(payload, Mapping):
            raise ExportManagementError("saved record payload is unreadable")
        return dict(payload)

    def _recall(
        self,
        project_id: str,
        intent_record_id: str,
        fingerprint: str,
        command_input: Mapping[str, object],
    ) -> Mapping[str, object] | None:
        payload = self._current(EXPORT_INTENT_AGGREGATE, intent_record_id)
        if payload is None:
            return None
        if (
            payload.get("schema_version") != EXPORT_INTENT_SCHEMA
            or payload.get("project_id") != project_id
        ):
            raise ExportIntentConflict("export intent belongs to different inputs")
        if payload.get("input_digest") != fingerprint:
            # Legacy intents hashed the frozen export key, not the command. Read
            # that exact key rather than resolving the latest report revision.
            export_id = payload.get("export_id")
            original = self._current(EXPORT_AGGREGATE, str(export_id))
            if original is None or payload.get("input_digest") != _digest(
                [
                    "export_report",
                    export_id,
                ]
            ):
                raise ExportIntentConflict("export intent has different frozen input")
            expected = dict(command_input)
            requested_run = expected.pop("run_id")
            original_key = dict(original["key"])
            original_key.pop("report_revision")
            if expected != original_key:
                raise ExportIntentConflict("export intent has different frozen input")
            if requested_run is not None:
                report = self._records.read(
                    aggregate_kind=REPORT_AGGREGATE,
                    record_id=str(original["report_id"]),
                    revision=int(original["report_revision"]),
                )
                if report_from_payload(report.payload).context.run_id != requested_run:
                    raise ExportIntentConflict("export intent has a different source run")
        result = payload.get("result")
        if not isinstance(result, Mapping) or set(result) != {
            "aggregate_kind",
            "record_id",
            "revision",
        }:
            raise ExportManagementError("saved export intent result is unreadable")
        self._verify_saved_artifact(project_id, str(result["record_id"]))
        return dict(result)

    def _verify_saved_artifact(self, project_id: str, export_id: str) -> None:
        payload = self._current(EXPORT_AGGREGATE, export_id)
        if payload is None or payload.get("project_id") != project_id:
            raise ExportRefused("saved export belongs to a different project or is missing")
        raw = payload.get("object_ref")
        if not isinstance(raw, Mapping) or raw.get("project_id") != project_id:
            raise ExportRefused("saved export permanent object cannot be verified")
        ref = StoredObjectRef(
            project_id,
            _text(raw.get("digest"), "digest"),
            cast(int, raw.get("size")),
            _text(raw.get("media_type"), "media_type"),
            _text(raw.get("relative_path"), "relative_path"),
        )
        content = self._objects.read_bytes(ref)  # type: ignore[union-attr]
        if _bytes_digest(content) != payload.get("artifact_digest"):
            raise ExportRefused("saved export artifact digest differs")
        if payload.get("kind") == ReportExportKind.EVIDENCE_BUNDLE.value:
            _verify_bundle_paths(content)

    def _commit(
        self,
        *,
        project_id: str,
        key_record_id: str,
        intent_id: str,
        request_id: str,
        intent_record_id: str,
        fingerprint: str,
        payload: dict[str, object],
        command_input: Mapping[str, object],
        reuse_existing: bool = False,
    ) -> Mapping[str, object]:
        result = {
            "aggregate_kind": EXPORT_AGGREGATE,
            "record_id": key_record_id,
            "revision": 1,
        }
        self._unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            if not reuse_existing:
                staged = self._unit.stage_record(
                    aggregate_kind=EXPORT_AGGREGATE,
                    record_id=key_record_id,
                    expected_revision=0,
                    payload=payload,
                )
                staged_revision = getattr(staged, "revision", staged)
                if type(staged_revision) is not int or staged_revision != 1:
                    raise ExportManagementError("staged export revision cannot be verified")
            self._unit.stage_record(
                aggregate_kind=EXPORT_INTENT_AGGREGATE,
                record_id=intent_record_id,
                expected_revision=0,
                payload={
                    "schema_version": EXPORT_INTENT_SCHEMA,
                    "project_id": project_id,
                    "intent_id": intent_id,
                    "export_id": key_record_id,
                    "input_digest": fingerprint,
                    "command_input": dict(command_input),
                    "result": dict(result),
                },
            )
            self._unit.commit(request_id)
        except BaseException:
            self._unit.rollback(request_id)
            raise
        return dict(result)


__all__ = [
    "DEFAULT_REDACTION_POLICY_VERSION",
    "EXPORT_AGGREGATE",
    "EXPORT_INTENT_AGGREGATE",
    "EXPORT_SCHEMA",
    "MANIFEST_FORMAT_VERSION",
    "SUPPORTED_ACTIONS",
    "ExportArtifactPort",
    "ExportConflict",
    "ExportIntentConflict",
    "ExportManagementError",
    "ExportNotSaved",
    "ExportProjectionPort",
    "ExportRefused",
    "SavedExportService",
    "export_key_id",
]
