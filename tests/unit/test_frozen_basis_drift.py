"""准备 → 启动的漂移核对链（检查文档 B-04 第③条）。

核对的是"`PreparedRun` 冻结的依据还能不能按**准确修订**读回来"。
记录不可变，所以能读到就等于依据完好；读不到就是缺口。

**覆盖范围（2026-10-03 扩展）**：绑定、环境、计划、**用例修订、已发布规则版本、
模板版本**。**仍然没有证据的来源必须如实列出**（项目修订、源码快照、验收范围），
把它们当"已核对通过"会让运行以为依据查过了。
"""

from __future__ import annotations

from aitest.application.planning.drift import (
    UNCOVERED_SOURCES,
    DriftReport,
    check_frozen_basis,
)
from aitest.application.planning.substrate import transaction
from tests.support.memory_substrate import MemoryReader, MemoryStore
from tests.support.prepared_run_factory import build_scenario

#: 夹具里确实落了记录的类别（顺序即核对顺序）。
_RECORD_BACKED = ("binding", "environment", "plan", "case_revisions", "rule_versions")
#: 模板不是记录：它按标识+版本装载已安装资源。
_TEMPLATE_BACKED = "template_versions"
#: 源码快照按 id 取最新修订并比对内容身份（`SnapshotRef` 没有修订号）。
_SNAPSHOT_BACKED = "source_snapshot"


def test_the_chain_covers_the_record_backed_sources() -> None:
    """覆盖的顺序与分组：三类按标识核对，然后**每个用例一条**、每条规则一条、
    每个模板一条，最后一条源码快照。"""
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    kinds = [check.source_kind for check in report.checks]
    assert kinds[:3] == ["binding", "environment", "plan"]
    # 每个冻结用例一条 check：一个用例读不回来就要单独报出来，不能被合并掉。
    case_count = len(scenario.prepared_run.case_revisions)
    assert kinds[3 : 3 + case_count] == ["case_revisions"] * case_count
    rest = kinds[3 + case_count :]
    assert rest == (
        ["rule_versions"] * len(scenario.prepared_run.rule_versions)
        + [_TEMPLATE_BACKED] * len(scenario.prepared_run.template_versions)
        + [_SNAPSHOT_BACKED]
    )


def test_the_frozen_source_snapshot_is_checked_by_content_identity() -> None:
    """检查文档 B-04：源码快照过去在 `uncovered` 里，现在按**内容身份**核对。

    `SnapshotRef` 没有修订号，所以核对的是"记录里的内容身份是否等于冻结值"。
    夹具没有落快照记录，因此如实报成缺口（并给出原因），而不是留给 `uncovered`。
    """
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    snapshot = next(c for c in report.checks if c.source_kind == _SNAPSHOT_BACKED)
    assert snapshot.record_id == scenario.prepared_run.snapshot.source_snapshot_id
    assert snapshot.readable is False
    assert "no persisted record" in snapshot.detail
    assert _SNAPSHOT_BACKED not in report.uncovered


def test_a_persisted_snapshot_with_a_different_identity_is_missing() -> None:
    """更隐蔽的一种：记录**存在**但内容身份与冻结值不同——源码已经变了。

    这必须报成缺口：这次运行的依据不是当初固定下来的那份。
    """
    scenario = build_scenario("git")
    snapshot_id = scenario.prepared_run.snapshot.source_snapshot_id
    with transaction(scenario.unit_of_work, scenario.prepared_run.project_id) as tx:
        tx.stage_record(
            aggregate_kind="source_snapshot",
            record_id=snapshot_id,
            expected_revision=None,
            payload={"content_identity": "sha256:something-else"},
        )
        tx.commit()

    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    snapshot = next(c for c in report.checks if c.source_kind == _SNAPSHOT_BACKED)
    assert snapshot.readable is False
    assert "differs from the frozen one" in snapshot.detail


def test_a_persisted_snapshot_with_the_frozen_identity_is_readable() -> None:
    """身份一致时核对通过——核对不是"一律报缺口"。"""
    scenario = build_scenario("git")
    snapshot = scenario.prepared_run.snapshot
    with transaction(scenario.unit_of_work, scenario.prepared_run.project_id) as tx:
        tx.stage_record(
            aggregate_kind="source_snapshot",
            record_id=snapshot.source_snapshot_id,
            expected_revision=None,
            payload={"content_identity": snapshot.content_identity},
        )
        tx.commit()

    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    check = next(c for c in report.checks if c.source_kind == _SNAPSHOT_BACKED)
    assert check.readable is True
    assert check.detail == ""


def test_case_and_rule_revisions_are_read_back_by_exact_revision() -> None:
    """检查文档 B-04："Case/规则/验收范围已有保存函数却未纳入核对。"

    纳入后：用例按 `case_id` + 准确修订核对、规则按 `rule_id` + 准确修订核对。
    夹具只落了 project/binding/plan，因此这两类**如实报成缺口**，而不是留给 `uncovered`。
    """
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    kinds = {check.source_kind for check in report.checks}
    assert "case_revisions" in kinds
    assert "rule_versions" in kinds
    assert not (kinds & set(UNCOVERED_SOURCES))
    # 夹具没有这两类记录，所以它们是"缺口"而不是"未覆盖"——两者含义不同。
    missing = {check.source_kind for check in report.missing}
    assert "case_revisions" in missing
    assert "rule_versions" in missing


def test_the_frozen_template_version_is_loadable() -> None:
    """模板按**标识+版本**核对：冻结的那个版本必须能从已安装资源装载。"""
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    template = next(c for c in report.checks if c.source_kind == _TEMPLATE_BACKED)
    assert template.readable is True
    assert template.record_id == "template:ticket-workflow@1.0.0"
    # 修订号对模板"不适用"：用 0 表示，避免被读成"修订 0 存在"。
    assert template.revision == 0
    assert template.detail == ""


def test_a_frozen_template_version_that_is_not_installed_is_missing() -> None:
    """装不出来的模板版本必须报成缺口，不能因为"别的依据都在"就说整体完好。"""
    scenario = build_scenario("git")
    prepared = scenario.prepared_run.model_copy(
        update={
            "template_versions": (
                type(scenario.prepared_run.template_versions[0])(
                    template_id="no-such-template",
                    version="9.9.9",
                    digest="sha256:none",
                ),
            )
        }
    )
    report = check_frozen_basis(prepared, reader=scenario.reader)
    template = next(c for c in report.checks if c.source_kind == _TEMPLATE_BACKED)
    assert template.readable is False
    assert template.detail
    assert report.intact is False


def test_a_frozen_basis_that_cannot_be_read_back_is_reported_as_missing() -> None:
    """夹具链路只落了项目/绑定/计划，**环境没有记录**——必须报成缺口，
    不能因为"别的依据都在"就把整体说成完好。"""
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    missing = {check.source_kind for check in report.missing}
    assert "environment" in missing
    assert report.intact is False


def test_the_chain_catches_a_declared_revision_that_was_never_persisted() -> None:
    """夹具链路本身就是一条真实反例。

    `PreparedRun` 声明 `binding_revision = 2`，而落盘的绑定记录只有修订 1。
    没有这条核对链时，这个不一致会一路带到启动；现在它被如实报成缺口。
    """
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)

    binding = next(c for c in report.checks if c.source_kind == "binding")
    assert scenario.prepared_run.binding_revision == 2
    assert binding.revision == 2
    assert binding.readable is False
    assert "binding" in {check.source_kind for check in report.missing}


def test_the_plan_frozen_by_the_fixture_is_reproducible() -> None:
    """计划那一版是真实落盘的，按准确修订读得回来。"""
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    plan = next(c for c in report.checks if c.source_kind == "plan")
    assert plan.readable is True


def test_an_empty_workspace_reports_every_record_check_as_missing() -> None:
    """空工作空间里**记录类**核对全部落空；模板走资源装载，因此不受影响。"""
    scenario = build_scenario("git")
    empty = MemoryReader(MemoryStore())
    report = check_frozen_basis(scenario.prepared_run, reader=empty)
    assert report.intact is False
    record_checks = [c for c in report.checks if c.source_kind != _TEMPLATE_BACKED]
    assert report.missing == tuple(record_checks)


def test_sources_without_evidence_are_listed_as_uncovered() -> None:
    """未覆盖的来源必须显式列出，**不得**退化成"没有影响"。

    两类缺口原因各不相同，都属"合同字段缺口"，不是"依据不存在"：
    ① 项目修订根本没冻结进 `PreparedRun`；② 验收范围只有修订号、没有 `scope_id`。
    """
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    assert report.uncovered == UNCOVERED_SOURCES
    assert set(report.uncovered) == {"project_revision", "acceptance_scope"}
    # 已经纳入核对的类别**不得**再出现在未覆盖里。
    for kind in (
        "case_revisions",
        "rule_versions",
        "template_versions",
        _SNAPSHOT_BACKED,
    ):
        assert kind not in report.uncovered


def test_the_report_is_a_pure_value_object() -> None:
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    assert isinstance(report, DriftReport)
    assert report.missing == tuple(c for c in report.checks if not c.readable)
