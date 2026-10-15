"""FR14 导出用例：Markdown 摘要与可携带证据包（架构 03 §5/§8）。

缺口形态是"导出只有纯门面 + `exports.py` 四行占位"：导出键、产物路径与摘要都可以由
调用方编造，包路径没有规范校验，脱敏没有强制。本文件锁定的生产行为：

- 内容由**已保存报告修订 + 已保存复核**生成，调用方给不出 `artifact_digest`/`artifact_ref`；
- 正文与附件经生产投影器脱敏，无法安全处理的材料登记为排除/缺失并说明；
- 制品先写事务临时位置、**核对摘要后才发布**，发布后再登记；失败不报成功；
- package 内路径全为规范相对路径；摘要按实际导出字节计算，与原始对象摘要区分；
- 相同冻结输入复用同一产物，新增复核产生新导出记录；
- ZIP 包解压到**空目录**后：`report.md` + `manifest.json`（逐文件摘要）+ `evidence/…`。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from aitest.application.review.saved_exports import (
    EXPORT_AGGREGATE,
    ExportIntentConflict,
    ExportManagementError,
    ExportNotSaved,
    ExportRefused,
    SavedExportService,
    _verify_bundle_paths,
    export_key_id,
)
from aitest.domain.review.reports import ReportExportKey, ReportExportKind
from aitest.infrastructure.exports import ArtifactError, FileReportArtifactPort, safe_artifact_name
from tests.unit.test_saved_reports import _create, _Harness

_REVIEW = {
    "review_id": "review-1",
    "report_id": "report-1",
    "conclusion": "accepted",
    "explanation": "reviewed the frozen revision",
}


def _report_with_review(harness: _Harness) -> None:
    _create(harness)
    harness.command(
        action="record_local_review",
        expected=0,
        parameters=dict(_REVIEW),
        intent_id="intent-review",
        request_id="request-review",
    )


def _export(
    harness: _Harness,
    *,
    kind: str = "markdown_summary",
    review_ids: list[str] | None = None,
    attachment_refs: list[str] | None = None,
    intent_id: str = "intent-export",
    request_id: str = "request-export",
    report_id: str = "report-1",
    run_id: str | None = "run-report-1",
) -> dict[str, object]:
    parameters: dict[str, object] = {
        "report_id": report_id,
        "kind": kind,
        "review_ids": review_ids if review_ids is not None else ["review-1"],
    }
    if attachment_refs is not None:
        parameters["attachment_refs"] = attachment_refs
    if run_id is not None:
        parameters["run_id"] = run_id
    return harness.command(
        action="export_report",
        expected=0,
        parameters=parameters,
        intent_id=intent_id,
        request_id=request_id,
    )


def test_markdown_summary_is_published_verified_and_registered(tmp_path) -> None:
    harness = _Harness(tmp_path, evidence=("evidence-1",))
    _report_with_review(harness)

    result = _export(harness)
    assert result["aggregate_kind"] == EXPORT_AGGREGATE
    assert result["revision"] == 1

    stored = harness.saved_export(str(result["record_id"]))
    assert stored["schema_version"] == "aitest.report-export/1.0"
    assert stored["kind"] == "markdown_summary"
    assert stored["report_revision"] == 1
    assert stored["review_ids"] == ["review-1"]
    assert stored["review_revisions"] == {"review-1": 1}
    assert stored["redaction_policy_version"] == "aitest.redaction/1.0"
    assert str(stored["artifact_ref"]).startswith("artifacts/reports/report-1/")
    assert stored["size_bytes"] > 0 and stored["media_type"].startswith("text/markdown")

    content = harness.artifact_bytes(
        str(stored["artifact_ref"]), expected_sha256=str(stored["artifact_digest"])
    )
    body = content.decode("utf-8")
    assert body.startswith("# 测试报告 report-1")
    assert "业务结论：incomplete" in body
    assert "review-1" in body and "accepted" in body
    # 摘要按实际导出字节计算：与磁盘内容一致。
    import hashlib

    assert "sha256:" + hashlib.sha256(content).hexdigest() == stored["artifact_digest"]
    # markdown_summary 必须声明附件未随文件交付，但仍给出证据索引。
    assert "附件不随本文件交付" in body
    assert "evidence-1" in body

    # 端口自查：已发布产物可复核。
    verified = harness.artifacts.verify_artifact(
        request_id="verify-1",
        workspace_id=harness.prepared.workspace_id,
        report_id="report-1",
        expected_sha256=str(stored["artifact_digest"]),
    )
    assert verified.result is not None and verified.result["status"] == "verified"


def test_evidence_bundle_unzips_into_an_empty_directory(tmp_path) -> None:
    harness = _Harness(tmp_path, evidence=("evidence-1", "evidence-2"))
    # 第二项证据不可安全投影：必须登记为排除，而不是夹带原文。
    blocked = harness.facts.evidence_refs[1].model_copy(update={"gap_ids": ("gap-1",)})
    harness.save_facts(
        harness.facts.model_copy(
            update={
                "evidence_refs": (harness.facts.evidence_refs[0], blocked),
            }
        )
    )
    _report_with_review(harness)

    result = _export(
        harness,
        kind="evidence_bundle",
        attachment_refs=["evidence-1", "evidence-2"],
    )
    stored = harness.saved_export(str(result["record_id"]))
    content = harness.artifact_bytes(
        str(stored["artifact_ref"]), expected_sha256=str(stored["artifact_digest"])
    )

    extracted = tmp_path / "unpacked"
    extracted.mkdir()
    expected_path = "evidence/" + hashlib.sha256(b"evidence-1").hexdigest() + ".bin"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        assert names == ["report.md", "manifest.json", expected_path]
        for name in names:
            assert not name.startswith("/") and ".." not in name.split("/")
        archive.extractall(extracted)

    manifest = json.loads((extracted / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format_version"] == "aitest.evidence-bundle/1.0"
    assert manifest["report_id"] == "report-1" and manifest["report_revision"] == 1
    assert manifest["review_ids"] == ["review-1"]
    assert manifest["scope"]["scope_revision"].startswith("scope:")
    # 逐文件摘要必须与解压后的实际字节一致。
    for item in manifest["files"]:
        actual = (extracted / item["path"]).read_bytes()
        assert "sha256:" + hashlib.sha256(actual).hexdigest() == item["sha256"]
        assert len(actual) == item["size"]
    assert manifest["attachments"][0]["path"] == expected_path
    assert manifest["attachments"][0]["evidence_id"] == "evidence-1"
    assert {item["evidence_id"] for item in manifest["missing"]} == {"evidence-2"}
    # 报告正文脱离插件可读。
    assert "业务结论：incomplete" in (extracted / "report.md").read_text(encoding="utf-8")


def test_markdown_summary_never_contains_a_registered_credential(tmp_path) -> None:
    """报告点名「**实际安全 Markdown**」：已知凭据不得出现在导出产物字节里。

    凭据在**写入复核记录时**就被过滤（工作单元底线），因此导出时看到的是幂等标记：
    断言链路上两层都安全，并且导出记录声明了脱敏策略、清单声明材料已干净投影。
    """

    from aitest.infrastructure.security import known_secrets

    secret = "aitest-export-credential-9f2b7c1d4e5a"
    known_secrets().register(secret)
    harness = _Harness(tmp_path, evidence=("evidence-1",))
    _create(harness)
    harness.command(
        action="record_local_review",
        expected=0,
        parameters={
            **_REVIEW,
            "explanation": f"reviewed with {secret}",
        },
        intent_id="intent-review-1",
        request_id="request-review-1",
    )

    # 第一层：复核记录落盘前已过滤，凭据不在记录字节里。
    review_revision = harness.repository.current_revision("local_review", "review-1")
    review_payload = harness.repository.read(
        aggregate_kind="local_review", record_id="review-1", revision=review_revision
    ).payload
    assert secret not in json.dumps(review_payload, ensure_ascii=False)
    assert "[REDACTED]" in json.dumps(review_payload, ensure_ascii=False)

    # 第二层：导出产物（Markdown 正文）同样不含凭据，且声明了脱敏策略。
    result = _export(harness)
    stored = harness.saved_export(str(result["record_id"]))
    body = harness.artifact_bytes(
        str(stored["artifact_ref"]), expected_sha256=str(stored["artifact_digest"])
    ).decode("utf-8")
    assert secret not in body
    assert "review-1" in body
    assert stored["redaction_policy_version"] == "aitest.redaction/1.0"


def test_evidence_bundle_paths_are_relative_and_portable(tmp_path) -> None:
    """报告点名「**可携带包**」：包内路径必须全部相对，不含宿主绝对路径。"""

    import zipfile

    harness = _Harness(tmp_path, evidence=("evidence-1",))
    _report_with_review(harness)
    result = _export(harness, kind="evidence_bundle")
    stored = harness.saved_export(str(result["record_id"]))
    blob = harness.artifact_bytes(
        str(stored["artifact_ref"]), expected_sha256=str(stored["artifact_digest"])
    )
    with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
        names = bundle.namelist()
        assert "report.md" in names and "manifest.json" in names
        for name in names:
            assert not name.startswith(("/", "\\"))
            assert ":" not in name, name
            assert ".." not in name.split("/")
        manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
    # 清单里的路径同样是包内相对路径，且不含宿主工作空间绝对路径。
    for item in manifest["files"]:
        assert not str(item["path"]).startswith(("/", "\\"))
        assert ":" not in str(item["path"])
    assert str(tmp_path) not in json.dumps(manifest, ensure_ascii=False)


def test_same_frozen_input_reuses_the_same_artifact(tmp_path) -> None:
    harness = _Harness(tmp_path, evidence=("evidence-1",))
    _report_with_review(harness)

    first = _export(harness)
    artifacts_after_first = sorted((tmp_path / "artifacts").rglob("*"))
    replay = _export(harness, intent_id="intent-export-2", request_id="request-2")
    assert replay == first, "相同冻结输入必须复用同一导出记录"
    assert sorted((tmp_path / "artifacts").rglob("*")) == artifacts_after_first

    # 新增复核（复核集合变化）⇒ 新导出记录，旧记录不改。
    harness.command(
        action="record_local_review",
        expected=0,
        parameters={
            "review_id": "review-2",
            "report_id": "report-1",
            "conclusion": "rework",
            "explanation": "second reviewer",
        },
        intent_id="intent-review-2",
        request_id="request-review-2",
    )
    second = _export(
        harness,
        review_ids=["review-1", "review-2"],
        intent_id="intent-export-3",
        request_id="request-3",
    )
    assert second["record_id"] != first["record_id"]
    assert harness.saved_export(str(first["record_id"]))["review_ids"] == ["review-1"]


def test_export_key_is_stable_and_callers_cannot_supply_artifact_facts(tmp_path) -> None:
    harness = _Harness(tmp_path, evidence=("evidence-1",))
    _report_with_review(harness)

    key = ReportExportKey(
        report_id="report-1",
        report_revision=1,
        review_ids=("review-1",),
        kind=ReportExportKind.MARKDOWN_SUMMARY,
        redaction_policy_version="aitest.redaction/1.0",
    )
    assert export_key_id(key) == export_key_id(key)

    with pytest.raises(ExportManagementError) as refused:
        harness.command(
            action="export_report",
            expected=0,
            parameters={
                "report_id": "report-1",
                "kind": "markdown_summary",
                "review_ids": ["review-1"],
                # 调用方既不能自报产物摘要，也不能自选导出身份。
                "artifact_digest": "sha256:" + "a" * 64,
                "export_id": "export-1",
            },
            intent_id="intent-x",
            request_id="request-x",
        )
    assert "artifact_digest" in str(refused.value)
    assert "export_id" in str(refused.value)


def test_export_refuses_stale_reviews_missing_records_and_missing_projector(tmp_path) -> None:
    harness = _Harness(tmp_path, evidence=("evidence-1",))
    _report_with_review(harness)

    with pytest.raises(ExportNotSaved):
        _export(harness, review_ids=["review-missing"], intent_id="i2", request_id="r2")

    # 报告出现新修订后，旧复核不再绑定当前修订 ⇒ 显式冲突，不静默使用。
    _create(harness, expected=1, intent_id="i3", request_id="r3")
    with pytest.raises(Exception) as conflict:
        _export(harness, intent_id="i4", request_id="r4")
    assert getattr(conflict.value, "code", None) == "RECORD_REVISION_CONFLICT"

    # 未保存的报告 ⇒ RECORD_NOT_FOUND（不是 500）。
    with pytest.raises(ExportNotSaved):
        _export(harness, report_id="report-missing", intent_id="i5", request_id="r5")

    # 脱敏不可用时拒绝导出（导出底线不可关闭）：先给当前修订补一份复核，
    # 否则会先命中"复核绑定旧修订"的修订冲突。
    harness.command(
        action="record_local_review",
        expected=0,
        parameters={
            "review_id": "review-2",
            "report_id": "report-1",
            "report_revision": 2,
            "conclusion": "accepted",
            "explanation": "reviewed the second revision",
        },
        intent_id="intent-review-2",
        request_id="request-review-2",
    )
    harness.exports._projector = None  # noqa: SLF001
    with pytest.raises(ExportRefused) as refused:
        _export(
            harness,
            review_ids=["review-2"],
            intent_id="i6",
            request_id="r6",
            run_id=None,
        )
    assert refused.value.code == "CAPABILITY_UNAVAILABLE"


def test_intent_conflict_on_different_export_input(tmp_path) -> None:
    harness = _Harness(tmp_path, evidence=("evidence-1",))
    _report_with_review(harness)
    _export(harness)
    with pytest.raises(ExportIntentConflict):
        harness.command(
            action="export_report",
            expected=0,
            parameters={
                "report_id": "report-1",
                "kind": "evidence_bundle",
                "review_ids": ["review-1"],
            },
            intent_id="intent-export",
            request_id="request-9",
        )


def test_artifact_port_rejects_unsafe_paths_and_tampering(tmp_path) -> None:
    port = FileReportArtifactPort(tmp_path)
    assert safe_artifact_name(
        report_id="report-1", kind="evidence_bundle", digest="sha256:" + "b" * 64
    ).endswith("-bbbbbbbbbbbbbbbb.zip")

    with pytest.raises(ArtifactError):
        safe_artifact_name(
            report_id="../escape", kind="markdown_summary", digest="sha256:" + "b" * 64
        )
    with pytest.raises(ArtifactError):
        safe_artifact_name(report_id="report-1", kind="unknown_kind", digest="sha256:" + "b" * 64)
    with pytest.raises(ArtifactError):
        port.artifact_path("artifacts/reports/../../secret.txt")
    with pytest.raises(ArtifactError):
        port.artifact_path("/abs/path")
    with pytest.raises(ArtifactError):
        port.write_artifact(
            request_id="req-ok",
            workspace_id="ws-1",
            report_id="report-1",
            kind="markdown_summary",
            content=b"",
        )

    # 同名不同内容 = 被篡改：拒绝而不是覆盖。
    response = port.write_artifact(
        request_id="req-1",
        workspace_id="ws-1",
        report_id="report-1",
        kind="markdown_summary",
        content=b"# one",
    )
    assert response.result is not None
    ref = str(response.result["artifact_ref"])
    published = port.artifact_path(ref)
    published.write_bytes(b"# tampered")
    with pytest.raises(ArtifactError) as tampered:
        port.read_artifact(ref, expected_sha256=str(response.result["artifact_digest"]))
    assert tampered.value.code == "INTEGRITY_CHECK_FAILED"
    # 同内容重复写入是幂等复用（崩溃恢复路径），不是覆盖。
    published.write_bytes(b"# one")
    again = port.write_artifact(
        request_id="req-2",
        workspace_id="ws-1",
        report_id="report-1",
        kind="markdown_summary",
        content=b"# one",
    )
    assert again.result is not None and again.result["status"] == "reused"


def test_published_artifact_is_not_reported_as_success_when_registration_is_blocked(
    tmp_path,
) -> None:
    """制品端口给出不一致摘要时，导出必须失败且**不登记**任何导出记录。"""

    harness = _Harness(tmp_path, evidence=("evidence-1",))
    _report_with_review(harness)

    class _LyingPort:
        def __init__(self, real: FileReportArtifactPort) -> None:
            self.real = real

        def write_artifact(self, **kwargs: object) -> object:
            response = self.real.write_artifact(**kwargs)  # type: ignore[arg-type]
            assert response.result is not None
            response.result["artifact_digest"] = "sha256:" + "c" * 64
            return response

        def verify_artifact(self, **kwargs: object) -> object:
            return self.real.verify_artifact(**kwargs)  # type: ignore[arg-type]

    harness.exports._artifacts = _LyingPort(harness.artifacts)  # noqa: SLF001
    with pytest.raises(ExportRefused) as refused:
        _export(harness, intent_id="i9", request_id="r9")
    assert refused.value.code == "INTEGRITY_CHECK_FAILED"
    key = ReportExportKey(
        report_id="report-1",
        report_revision=1,
        review_ids=("review-1",),
        kind=ReportExportKind.MARKDOWN_SUMMARY,
        redaction_policy_version="aitest.redaction/1.0",
    )
    assert harness.repository.current_revision("report_export", export_key_id(key)) == 0


def test_copy_repair_brief_is_read_only_and_redacted(tmp_path) -> None:
    """复测简报：只读投影、含可解释指引、不含证据正文、未知位置保持未知。"""

    from aitest.application.review.repair_brief import RepairBriefError, SavedRepairBrief
    from aitest.contracts.commands import Command
    from aitest.infrastructure.projections import SafeMaterialProjector

    harness = _Harness(tmp_path, evidence=("evidence-1",))
    harness.repository.append(
        "issue",
        "issue-1",
        0,
        {
            "schema_version": "aitest.issue/1.0",
            "issue_id": "issue-1",
            "project_id": "project-ticket",
            "revision": 3,
            "title": "checkout fails on retry",
            "status": "ready_for_retest",
            "severity": "P1",
            "disposition": "active",
            "owner": "local-owner",
            "confirmed_by": "session:session-user-1",
            "disposition_reason": None,
            "canonical_issue_id": None,
            "fix_version": "1.2.3",
            "regression_method": "rerun the original case",
            "closure_criteria": "original acceptance criteria",
            "evidence_refs": ["evidence-1"],
            "attempt_ids": [],
            "links": {"module_ids": [], "layers": []},
        },
    )
    brief = SavedRepairBrief(
        records=harness.repository,
        runtime=type(harness.exports._runtime)(harness.facts),  # noqa: SLF001
        projector=SafeMaterialProjector(),
    )
    before = harness.repository.current_revision("issue", "issue-1")

    result = dict(
        brief.copy_repair_brief(
            Command(
                request_id="brief-1",
                action="copy_repair_brief",
                project_id="project-ticket",
                parameters={"issue_id": "issue-1", "run_id": "run-report-1"},
            )
        )
    )
    text = str(result["brief"])
    assert "checkout fails on retry" in text
    assert "复测指引" in text
    assert "1.2.3" in text and "original acceptance criteria" in text
    assert "evidence-1" in text
    assert "新的实际执行证据" in text
    assert result["issue_revision"] == 3
    # 共享面板解析的键集合（`repair-brief.spec.ts`）：缺一即静默破坏面板展示。
    assert {
        "brief",
        "issue_id",
        "issue_revision",
        "facts_state",
        "unknown_positions",
        "excluded",
        "redaction_policy_version",
    } <= set(result)
    # 只读：不新增记录、不推进修订。
    assert harness.repository.current_revision("issue", "issue-1") == before

    # 未知位置如实呈现，不用 0/false 顶替。
    unknown_facts = SavedRepairBrief(
        records=harness.repository,
        runtime=_NoFacts(),
        projector=SafeMaterialProjector(),
    )
    fallback = dict(
        unknown_facts.copy_repair_brief(
            Command(
                request_id="brief-2",
                action="copy_repair_brief",
                project_id="project-ticket",
                parameters={"issue_id": "issue-1", "run_id": "run-unknown"},
            )
        )
    )
    assert "未知" in str(fallback["brief"])
    assert fallback["unknown_positions"]

    with pytest.raises(RepairBriefError) as missing:
        brief.copy_repair_brief(
            Command(
                request_id="brief-3",
                action="copy_repair_brief",
                project_id="project-ticket",
                parameters={"issue_id": "issue-missing"},
            )
        )
    assert missing.value.code == "RECORD_NOT_FOUND"

    no_projector = SavedRepairBrief(
        records=harness.repository,
        runtime=type(harness.exports._runtime)(harness.facts),  # noqa: SLF001
        projector=None,
    )
    with pytest.raises(RepairBriefError) as refused:
        no_projector.copy_repair_brief(
            Command(
                request_id="brief-4",
                action="copy_repair_brief",
                project_id="project-ticket",
                parameters={"issue_id": "issue-1"},
            )
        )
    assert refused.value.code == "CAPABILITY_UNAVAILABLE"


class _NoFacts:
    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> object:
        raise ValueError("run facts are unavailable")


def test_extracted_bundle_is_readable_without_the_plugin(tmp_path) -> None:
    """普通 Markdown 与附件脱离插件可读：包内只有文本与字节，没有安装依赖。"""

    harness = _Harness(tmp_path, evidence=("evidence-1",))
    _report_with_review(harness)
    result = _export(harness, kind="evidence_bundle", attachment_refs=["evidence-1"])
    stored = harness.saved_export(str(result["record_id"]))
    content = harness.artifact_bytes(
        str(stored["artifact_ref"]), expected_sha256=str(stored["artifact_digest"])
    )
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            assert archive.getinfo(name).compress_type in {
                zipfile.ZIP_STORED,
                zipfile.ZIP_DEFLATED,
            }
    target = Path(tmp_path) / "portable"
    target.mkdir()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        archive.extractall(target)
    assert (target / "report.md").is_file()
    assert (target / "manifest.json").is_file()


@pytest.mark.parametrize(
    "identities",
    [("../../escape",), (r"..\..\escape",), ("CON",), ("Evidence-A", "evidence-a")],
)
def test_export_encodes_business_identities_without_creating_unsafe_members(
    tmp_path, identities,
) -> None:
    harness = _Harness(tmp_path, evidence=identities)
    _report_with_review(harness)
    result = _export(harness, kind="evidence_bundle")
    saved = harness.saved_export(str(result["record_id"]))
    raw = harness.artifact_bytes(str(saved["artifact_ref"]),
                                expected_sha256=str(saved["artifact_digest"]))
    _verify_bundle_paths(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        attachments = manifest["attachments"]
        assert {item["evidence_id"] for item in attachments} == set(identities)
        assert len({item["path"].casefold() for item in attachments}) == len(identities)
        for item in attachments:
            assert item["path"].startswith("evidence/")
            assert len(item["path"].split("/")) == 2
            assert archive.read(item["path"]) == b"abc"


@pytest.mark.parametrize("vector_index", range(4))
def test_saved_export_guard_refuses_actual_legacy_unsafe_archives(vector_index) -> None:
    """Permanent-object digest agreement alone must not authorize old unsafe ZIPs."""
    vectors_path = Path(__file__).resolve().parents[2] / (
        "docs/validation/p1-completion-repair-20261014/zip-legacy-vectors.json"
    )
    vector = json.loads(vectors_path.read_text(encoding="utf-8"))["vectors"][vector_index]
    content = base64.b64decode(vector["artifact_base64"], validate=True)
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    assert digest == vector["sha256"] and len(content) == vector["size"]

    class MemoryObjects:
        def read_bytes(self, reference):
            assert reference.digest == digest and reference.size == len(content)
            return content

    service = object.__new__(SavedExportService)
    service._objects = MemoryObjects()
    payload = {
        "project_id": "project-1",
        "kind": "evidence_bundle",
        "artifact_digest": digest,
        "object_ref": {
            "project_id": "project-1",
            "digest": digest,
            "size": len(content),
            "media_type": "application/zip",
            "relative_path": "objects/project-1/" + digest[7:],
        },
    }
    service._current = lambda _kind, _identity: payload
    with pytest.raises(ExportRefused):
        service._verify_saved_artifact("project-1", "legacy-export")


def _member_fixture(names, *, manifest_mutator=None, symbolic_link=False) -> bytes:
    """Portable ZIP bytes with deliberate hostile metadata variants; no extraction."""
    contents = {"report.md": b"report", **{name: b"abc" for name in names}}
    manifest = {
        "format_version": "aitest.evidence-bundle/1.0",
        "files": [
            {
                "path": path,
                "size": len(blob),
                "sha256": "sha256:" + hashlib.sha256(blob).hexdigest(),
            }
            for path, blob in contents.items()
        ],
        "attachments": [{"path": name} for name in names],
    }
    if manifest_mutator is not None:
        manifest_mutator(manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, blob in contents.items():
            info = zipfile.ZipInfo(name)
            if symbolic_link and name in names:
                info.create_system = 3
                info.external_attr = 0o120777 << 16
            archive.writestr(info, blob)
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))
    return buffer.getvalue()


@pytest.mark.parametrize(
    "name",
    [
        "evidence/evidence-1.bin",
        "evidence/证据.bin",
        "evidence/group/item.bin",
        "evidence/COM10.bin",
    ],
)
def test_bundle_guard_preserves_portable_unicode_and_nested_members(name) -> None:
    _verify_bundle_paths(_member_fixture([name]))


@pytest.mark.parametrize(
    "names",
    [
        ["evidence/a.bin", "evidence/A.bin"],
        ["evidence/Å.bin", "evidence/A\u030a.bin"],
        ["evidence/COM¹.bin"],
        ["evidence/CON.note.bin"],
        ["evidence/NUL.bin"],
        ["evidence/folder./item.bin"],
        ["evidence/folder /item.bin"],
        ["evidence/a:stream.bin"],
        ["evidence/item\x00suffix.bin"],
    ],
)
def test_bundle_guard_refuses_portability_and_manifest_aliases(names) -> None:
    with pytest.raises(ExportRefused):
        _verify_bundle_paths(_member_fixture(names))


def test_bundle_guard_refuses_symlink_members() -> None:
    with pytest.raises(ExportRefused):
        _verify_bundle_paths(_member_fixture(["evidence/item.bin"], symbolic_link=True))


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "foreign", "wrong_size", "unknown_format"]
)
def test_bundle_guard_checks_manifest_paths_against_actual_directory(mutation) -> None:
    def alter(manifest):
        if mutation == "missing":
            manifest["files"].pop()
        elif mutation == "duplicate":
            manifest["files"].append(manifest["files"][0])
        elif mutation == "foreign":
            manifest["attachments"][0]["path"] = "../../outside"
        elif mutation == "wrong_size":
            manifest["files"][0]["size"] += 1
        else:
            manifest["format_version"] = "unknown"

    with pytest.raises(ExportRefused):
        _verify_bundle_paths(_member_fixture(["evidence/item.bin"], manifest_mutator=alter))


def test_export_refuses_unavailable_policy_instead_of_claiming_it(tmp_path) -> None:
    harness = _Harness(tmp_path)
    _report_with_review(harness)
    sequence = harness.unit.current_commit_sequence()
    with pytest.raises(ExportRefused, match="policy is unavailable"):
        harness.command(
            action="export_report",
            expected=0,
            intent_id="unknown-policy",
            request_id="unknown-policy-request",
            parameters={
                "report_id": "report-1",
                "kind": "markdown_summary",
                "redaction_policy_version": "unknown-policy/99",
            },
        )
    assert harness.unit.current_commit_sequence() == sequence


def test_bundle_manifest_budget_is_checked_before_expansion(monkeypatch) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.md", b"report")
        archive.writestr("manifest.json", b" " * (8 * 1024 * 1024 + 1))

    def forbid_expansion(*args, **kwargs):
        raise AssertionError("oversized manifest must not be expanded")

    monkeypatch.setattr(zipfile.ZipFile, "open", forbid_expansion)
    with pytest.raises(ExportRefused, match="read budget"):
        _verify_bundle_paths(buffer.getvalue())


def test_bundle_refuses_unsupported_manifest_compression() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_BZIP2) as archive:
        archive.writestr("report.md", b"report")
        archive.writestr("manifest.json", b"{}")
    with pytest.raises(ExportRefused, match="compression is unavailable"):
        _verify_bundle_paths(buffer.getvalue())


@pytest.mark.parametrize(
    "names",
    [
        ["evidence/parent.bin", "evidence/parent.bin/child.bin"],
        ["evidence/parent.bin/child.bin", "evidence/PARENT.bin"],
        ["evidence/é.bin", "evidence/e\u0301.bin/child.bin"],
    ],
)
def test_bundle_guard_refuses_file_directory_prefix_collisions(names) -> None:
    with pytest.raises(ExportRefused, match="paths collide"):
        _verify_bundle_paths(_member_fixture(names))


@pytest.mark.parametrize("flag", [1, 64])
def test_bundle_guard_refuses_encryption_before_opening_members(monkeypatch, flag) -> None:
    import struct

    content = bytearray(_member_fixture(["evidence/ordinary.bin"]))
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = archive.getinfo("manifest.json")
        central = archive.start_dir
    struct.pack_into("<H", content, manifest.header_offset + 6, flag)
    while content[central : central + 4] == b"PK\x01\x02":
        name_size, extra_size, comment_size = struct.unpack_from("<HHH", content, central + 28)
        name = bytes(content[central + 46 : central + 46 + name_size])
        if name == b"manifest.json":
            struct.pack_into("<H", content, central + 8, flag)
            break
        central += 46 + name_size + extra_size + comment_size
    else:
        raise AssertionError("manifest central entry missing from fixture")

    def must_not_open(*_args, **_kwargs):
        raise AssertionError("encrypted members must be refused before expansion")

    monkeypatch.setattr(zipfile.ZipFile, "open", must_not_open)
    with pytest.raises(ExportRefused, match="encrypted"):
        _verify_bundle_paths(bytes(content))
