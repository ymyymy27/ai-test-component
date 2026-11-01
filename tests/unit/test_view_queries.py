"""宿主只读视图接缝（架构 06 §1/§2/§4/§54）。

架构 06 把面板读数写成 **DTO 视图**（"接口提供 PanelDTO/PlanDTO/RunDTO/DecisionDTO/ReportDTO，
视图不直接读文件、调用适配器或重算判定"），并把 `interfaces/local/editor_host.py` 的职责写成
"动作调用、只读查询与事件续读"。`CommittedViewReader` 就是那层只读查询接缝：

- 判定**复用** `SavedReportService.live_decision`（报告与面板同一次领域判定，界面不重算）；
- 读的是**已提交事实**，快照边界与配对游标原样带出（§2/§4 的 SnapshotEnvelope 语义）；
- **不写任何记录**：调用前后提交序号与记录修订不变；
- 无已保存运行、身份不合法时**结构化拒绝**，不返回空视图冒充"没有记录"。
"""

from __future__ import annotations

import pytest

from aitest.application.review.saved_reports import ReportGuardRefused
from aitest.interfaces.dto import RunDTO
from aitest.interfaces.local.view_queries import CommittedViewReader
from tests.support.prepared_run_factory import PROJECT_ID
from tests.unit.test_saved_reports import _RUN_ID, _Harness

PROJECT = PROJECT_ID


def test_run_view_uses_the_same_decision_as_the_report_and_writes_nothing(tmp_path) -> None:
    harness = _Harness(tmp_path, evidence=("evidence-1",))
    reader = CommittedViewReader(decisions=harness.service)
    commit_before = harness.unit.current_commit_sequence()

    view = reader.run_view(project_id=PROJECT, run_id=_RUN_ID)

    assert isinstance(view, RunDTO)
    # 同一已提交边界 + 配对游标（§4：不能先读视图再取"此刻最新"游标）。
    assert view.snapshot_commit_id == harness.facts.snapshot_commit_id
    assert view.snapshot_cursor == harness.facts.snapshot_cursor
    assert view.run_id == _RUN_ID and view.run_revision == harness.facts.run_revision
    # S/M 与覆盖来自领域判定（与报告同一口径），不是视图层重算。
    assert view.selected_case_ids == tuple(sorted(harness.facts.run.selected_scope))
    assert view.required_case_ids == tuple(sorted(harness.facts.run.required_scope))
    assert view.selected_summary.denominator == len(harness.facts.run.selected_scope)
    assert view.required_summary.denominator == len(harness.facts.run.required_scope)
    # 每个步骤都来自已提交事实，且当前尝试与尝试状态一一对应。
    assert {row.step_id for row in view.steps} == {step.step_id for step in harness.facts.steps}
    for row in view.steps:
        assert row.current_attempt_id == harness.facts.current_attempt_by_step.get(row.step_id)
        assert row.adapter_kind == "command"

    # 只读：提交序号与记录修订都不变。
    assert harness.unit.current_commit_sequence() == commit_before
    assert harness.unit.repo.current_revision("report", "report-1") == 0


def test_missing_run_and_invalid_identity_are_refused(tmp_path) -> None:
    """真装配：没有已保存运行 ⇒ 结构化拒绝（不返回空视图冒充"没有记录"）。"""

    from aitest.bootstrap import assemble_workspace_core

    core = assemble_workspace_core(tmp_path, instance_id="view-refusal-core")
    try:
        reader = core.view_queries
        assert isinstance(reader, CommittedViewReader)
        with pytest.raises(ReportGuardRefused):
            reader.run_view(project_id=PROJECT, run_id="run-does-not-exist")
        with pytest.raises(ValueError):
            reader.run_view(project_id="  ", run_id=_RUN_ID)
        with pytest.raises(ValueError):
            reader.run_view(project_id=PROJECT, run_id="")
    finally:
        core.lifetime_lock.release()


def test_view_reader_requires_the_decision_capability() -> None:
    with pytest.raises(ValueError):
        CommittedViewReader(decisions=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", ["plan", "case", "report", "issue"])
def test_exact_record_view_is_scoped_and_omits_private_fields(kind) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock
    record = SimpleNamespace(aggregate_kind=kind, record_id="saved", revision=2,
                             payload={"project_id":PROJECT, f"{kind}_id":"saved",
                                      "status":"draft", "private_note":"not public"})
    store = Mock()
    store.read.return_value = record
    reader = CommittedViewReader(decisions=Mock(), reader=store)
    result = reader.record_view(project_id=PROJECT, kind=kind, record_id="saved", revision=2)
    assert result["record_revision"] == 2
    assert result["historical_snapshot"] is True
    assert "private_note" not in result["record"]
    store.read.assert_called_once_with(aggregate_kind=kind, record_id="saved", revision=2)
    with pytest.raises(ValueError):
        reader.record_view(project_id="other", kind=kind, record_id="saved", revision=2)
    with pytest.raises(ValueError):
        reader.record_view(project_id=PROJECT, kind=kind, record_id="saved", revision=1)


def test_record_selector_requires_exact_revision_and_rejects_mixed_selectors() -> None:
    from aitest.contracts.read_views import ReadViewQuery
    valid = {"view":"report", "record_id":"report-1", "revision":1}
    assert ReadViewQuery.model_validate(valid).revision == 1
    for invalid in ({"view":"report", "record_id":"report-1"},
                    {**valid, "run_id":"run-1"}, {**valid, "revision":True},
                    {**valid, "record_id":None}, {**valid, "revision":None}):
        with pytest.raises(ValueError):
            ReadViewQuery.model_validate(invalid)


# ---------------------------------------------------------------------------
# 证据正文只读视图（Issue #125）：严格脱敏门控
# ---------------------------------------------------------------------------

_EVIDENCE_DIGEST = "sha256:" + "a" * 64


def _evidence_record(payload_override: dict | None = None) -> object:
    from types import SimpleNamespace

    payload = {
        "project_id": PROJECT, "evidence_id": "ev-1", "evidence_revision": 2,
        "run_id": "run-1", "step_id": "step-1", "attempt_id": "attempt-1",
        "source_instance_id": "instance-1", "evidence_kind": "command_output",
        "capture_source": "plugin_runtime", "object_digest": _EVIDENCE_DIGEST,
        "object_size": 5, "integrity": "complete", "redaction_state": "redacted",
        "projection_state": "displayable", "evidence_level": "full_link",
        "gap_ids": [], "media_type": "text/plain", "created_at": "2026-01-01T00:00:00",
        "code_identity": {"workspace_ref": "C:/secret/path", "commit_id": "c1"},
    }
    if payload_override:
        payload.update(payload_override)
    return SimpleNamespace(
        aggregate_kind="evidence_ref", record_id="ev-1", revision=2, payload=payload,
    )


def test_evidence_view_returns_filtered_body_and_strips_code_identity() -> None:
    """安全态（redacted）：返回正文；白名单剔除 code_identity 中的 workspace_ref。"""
    from unittest.mock import Mock

    store = Mock()
    store.read.return_value = _evidence_record()
    objects = Mock()
    objects.read_bytes.return_value = b"hello"
    reader = CommittedViewReader(decisions=Mock(), reader=store, objects=objects)

    view = reader.evidence_view(project_id=PROJECT, record_id="ev-1", revision=2)

    assert view["record_kind"] == "evidence"
    assert view["body"] == "hello"
    assert "body_omitted_reason" not in view
    assert "code_identity" not in view["record"]
    assert "workspace_ref" not in str(view["record"])


@pytest.mark.parametrize("redaction_state", ["blocked", "unknown", None, "garbage"])
def test_evidence_view_unsafe_redaction_returns_metadata_only(redaction_state) -> None:
    """redaction_state 非安全态：不返回正文，给出 body_omitted_reason。"""
    from unittest.mock import Mock

    store = Mock()
    store.read.return_value = _evidence_record({"redaction_state": redaction_state})
    objects = Mock()
    reader = CommittedViewReader(decisions=Mock(), reader=store, objects=objects)

    view = reader.evidence_view(project_id=PROJECT, record_id="ev-1", revision=2)

    assert "body" not in view
    assert view["body_omitted_reason"] == "redaction_unsafe"


def test_evidence_view_rejects_cross_project_identity() -> None:
    """project_id / evidence_id 不匹配则拒绝（不返回空视图冒充没有记录）。"""
    from unittest.mock import Mock

    store = Mock()
    store.read.return_value = _evidence_record()
    reader = CommittedViewReader(decisions=Mock(), reader=store, objects=Mock())

    with pytest.raises(ValueError):
        reader.evidence_view(project_id="other-project", record_id="ev-1", revision=2)


def test_evidence_view_digest_mismatch_is_silently_omitted() -> None:
    """对象仓摘要不符（ValueError）：不抛原始异常，只标 object_unreadable。"""
    from unittest.mock import Mock

    store = Mock()
    store.read.return_value = _evidence_record()
    objects = Mock()
    objects.read_bytes.side_effect = ValueError("digest mismatch")
    reader = CommittedViewReader(decisions=Mock(), reader=store, objects=objects)

    view = reader.evidence_view(project_id=PROJECT, record_id="ev-1", revision=2)

    assert "body" not in view
    assert view["body_omitted_reason"] == "object_unreadable"


def test_evidence_view_binary_content_is_not_returned() -> None:
    """二进制正文：面板不渲染，仅返回元数据。"""
    from unittest.mock import Mock

    store = Mock()
    store.read.return_value = _evidence_record(
        {"object_size": 4, "media_type": "image/png"},
    )
    objects = Mock()
    objects.read_bytes.return_value = b"\xff\xd8\xff\xe0"
    reader = CommittedViewReader(decisions=Mock(), reader=store, objects=objects)

    view = reader.evidence_view(project_id=PROJECT, record_id="ev-1", revision=2)

    assert "body" not in view
    assert view["body_omitted_reason"] == "binary_content"


def test_evidence_view_without_object_store_degrades_to_metadata() -> None:
    """对象仓未接线：降级为只返元数据，不抛错。"""
    from unittest.mock import Mock

    store = Mock()
    store.read.return_value = _evidence_record()
    reader = CommittedViewReader(decisions=Mock(), reader=store)  # objects=None

    view = reader.evidence_view(project_id=PROJECT, record_id="ev-1", revision=2)

    assert "body" not in view
    assert view["body_omitted_reason"] == "object_unavailable"


def test_evidence_view_oversized_object_returns_metadata_only() -> None:
    """超大对象（>4MB）：不读正文，避免面板负载失控。"""
    from unittest.mock import Mock

    store = Mock()
    store.read.return_value = _evidence_record({"object_size": 5 * 1024 * 1024})
    objects = Mock()
    reader = CommittedViewReader(decisions=Mock(), reader=store, objects=objects)

    view = reader.evidence_view(project_id=PROJECT, record_id="ev-1", revision=2)

    assert "body" not in view
    assert view["body_omitted_reason"] == "body_too_large"


def test_evidence_selector_contract() -> None:
    """ReadViewQuery 接受 evidence 选择器，并拒绝缺字段或混 run_id 等混用。"""
    from aitest.contracts.read_views import ReadViewQuery

    valid = {"view": "evidence", "record_id": "ev-1", "revision": 2}
    query = ReadViewQuery.model_validate(valid)
    assert query.view == "evidence" and query.record_id == "ev-1" and query.revision == 2

    for invalid in (
        {"view": "evidence", "record_id": "ev-1"},
        {"view": "evidence", "revision": 2},
        {**valid, "run_id": "run-1"},
        {**valid, "revision": None},
        {**valid, "record_id": None},
    ):
        with pytest.raises(ValueError):
            ReadViewQuery.model_validate(invalid)


def test_evidence_view_through_dispatch_carries_snapshot_boundary() -> None:
    """dispatch evidence 路由到 evidence_view，并带出配对快照边界。"""
    from types import SimpleNamespace
    from unittest.mock import Mock

    from aitest.contracts.commands import Command

    store = Mock()
    store.read.return_value = _evidence_record({"object_size": 3})
    store.query.return_value = SimpleNamespace(
        commit_id=1, event_cursor="cursor-1", generation=1, index_version=1,
    )
    objects = Mock()
    objects.read_bytes.return_value = b"abc"
    reader = CommittedViewReader(decisions=Mock(), reader=store, objects=objects)

    command = Command(
        project_id=PROJECT, action="read_view", request_id="req-1",
        parameters={"view": "evidence", "record_id": "ev-1", "revision": 2},
    )
    result = reader.dispatch(command)

    assert result["view_type"] == "evidence"
    assert result["view"]["body"] == "abc"
    assert "commit_id" in result and "event_cursor" in result
