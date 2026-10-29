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
