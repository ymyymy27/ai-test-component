"""准备 → 启动的漂移核对链（检查文档 B-04 第③条）。

核对的是"`PreparedRun` 冻结的依据还能不能按**准确修订**读回来"。
记录不可变，所以能读到就等于依据完好；读不到就是缺口。

**未覆盖的来源必须如实列出**：一期还有若干来源没有记录可核，
把它们当"已核对通过"会让运行以为依据查过了。
"""

from __future__ import annotations

from aitest.application.planning.drift import (
    UNCOVERED_SOURCES,
    DriftReport,
    check_frozen_basis,
)
from tests.support.memory_substrate import MemoryReader, MemoryStore
from tests.support.prepared_run_factory import build_scenario


def test_the_chain_covers_the_record_backed_sources() -> None:
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    assert [check.source_kind for check in report.checks] == [
        "binding",
        "environment",
        "plan",
    ]


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


def test_an_empty_workspace_reports_every_check_as_missing() -> None:
    scenario = build_scenario("git")
    empty = MemoryReader(MemoryStore())
    report = check_frozen_basis(scenario.prepared_run, reader=empty)
    assert report.intact is False
    assert len(report.missing) == len(report.checks)


def test_sources_without_records_are_listed_as_uncovered() -> None:
    """未覆盖的来源必须显式列出，**不得**退化成"没有影响"。"""
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    assert report.uncovered == UNCOVERED_SOURCES
    for kind in ("case_revisions", "rule_versions", "template_versions"):
        assert kind in report.uncovered
    # 项目修订根本没冻结进 PreparedRun，属合同字段缺口，同样如实列出。
    assert "project_revision" in report.uncovered


def test_the_report_is_a_pure_value_object() -> None:
    scenario = build_scenario("git")
    report = check_frozen_basis(scenario.prepared_run, reader=scenario.reader)
    assert isinstance(report, DriftReport)
    assert report.missing == tuple(c for c in report.checks if not c.readable)
