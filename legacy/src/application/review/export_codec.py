"""导出的成员键、Markdown 与摘要编解码（从 saved_exports.py 拆出）。

只做形状与摘要；服务层 re-export 公开函数与错误类型。
"""

from __future__ import annotations

import hashlib
import io
import stat
import unicodedata
import zipfile
from collections.abc import Mapping
from typing import Any

from aitest.application.review.saved_reports import (
    ReportManagementError,
    ReportRevisionConflict,
)
from aitest.domain.canonical import canonical_digest
from aitest.domain.json_material import decode_json
from aitest.domain.review.reports import (
    ReportExportKey,
    ReportSnapshot,
)

_WINDOWS_DEVICE_NAMES = frozenset(
    ("CON", "PRN", "AUX", "NUL")
    + tuple(f"COM{digit}" for digit in "123456789¹²³")
    + tuple(f"LPT{digit}" for digit in "123456789¹²³")
)
_MANIFEST_METADATA_LIMIT_BYTES = 8 * 1024 * 1024

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
            directories: set[str] = set()
            for item in members:
                key = _portable_member_key(item.orig_filename)
                parts = key.split("/")
                parents = {"/".join(parts[:index]) for index in range(1, len(parts))}
                if key in keys or key in directories or parents & keys:
                    raise ExportRefused("evidence bundle member paths collide")
                if item.flag_bits & (1 | 64):
                    raise ExportRefused("encrypted evidence bundle members are unavailable")
                if item.filename != item.orig_filename or stat.S_IFMT(
                    item.external_attr >> 16
                ) not in {
                    0,
                    stat.S_IFREG,
                }:
                    raise ExportRefused("evidence bundle member is not a regular portable file")
                keys.add(key)
                directories.update(parents)
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
    return canonical_digest(value)

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
