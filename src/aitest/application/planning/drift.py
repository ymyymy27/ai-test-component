"""准备 → 启动之间的漂移核对链：核对 `PreparedRun` 冻结的依据还在不在。

对应检查文档 B-04 第③条"准备与启动之间无完整漂移核对链"。

`PreparedRun` 冻结了各来源的**准确修订**。发起运行之前必须能**按那些修订读回**它们——
否则这次运行用的是"已经不是当初依据"的来源。记录不可变，因此"能按准确修订读到"
就等于"依据完好"；读不到就是缺口，**不得**退化成"没有影响"。

## 只核对已经有记录的来源

一期当前由工作单元落盘的类别见第 2 节表格。余下来源（用例修订、已发布规则版本、
模板版本、验收范围、源码快照）**没有记录可核**，本模块把它们**如实列进 `uncovered`**，
不假装核对过。少报覆盖比多报覆盖危险得多：前者会让人以为依据已经查过了。
"""

from __future__ import annotations

from dataclasses import dataclass

from aitest.application.planning.substrate import RecordReader
from aitest.contracts.prepared_run import PreparedRun


@dataclass(frozen=True, slots=True)
class BasisCheck:
    """一条冻结依据的可读性核对结果。"""

    source_kind: str
    record_id: str
    revision: int
    readable: bool


@dataclass(frozen=True, slots=True)
class DriftReport:
    """一次漂移核对的结果。

    `uncovered` 是**本模块无法核对**的来源类别：它们当前没有记录，
    因此"依据是否完好"这件事对它们**没有证据**。调用方必须如实呈现，
    不能把空 checks 之外的部分读成"已核对通过"。
    """

    checks: tuple[BasisCheck, ...]
    uncovered: tuple[str, ...] = ()

    @property
    def intact(self) -> bool:
        """全部可核对的依据都能按准确修订读回。"""
        return all(check.readable for check in self.checks)

    @property
    def missing(self) -> tuple[BasisCheck, ...]:
        """读不回来的依据；非空即表示冻结依据出现缺口。"""
        return tuple(check for check in self.checks if not check.readable)


#: 一期**没有记录可核、或合同里根本没冻结修订**的来源类别。
#: 列出来而不是忽略：忽略会让调用方以为它们被查过。
UNCOVERED_SOURCES: tuple[str, ...] = (
    # `PreparedRun` 合同**没有冻结项目修订**（只有 project_id），
    # 因此项目依据无从按准确修订核对；这是合同字段缺口，不是"项目不存在"。
    "project_revision",
    "case_revisions",
    "rule_versions",
    "template_versions",
    "acceptance_scope",
    "source_snapshot",
)


def _readable(
    reader: RecordReader,
    *,
    project_id: str,
    aggregate_kind: str,
    record_id: str,
    revision: int,
) -> bool:
    """按准确修订读回；读不到只表示**这条依据不可核**，不表示"项目没有它"。"""
    try:
        reader.read(
            aggregate_kind=aggregate_kind,  # type: ignore[arg-type]
            record_id=record_id,
            revision=revision,
        )
    except ValueError:
        return False
    return True


def check_frozen_basis(prepared_run: PreparedRun, *, reader: RecordReader) -> DriftReport:
    """核对 `PreparedRun` 里**已经有记录**的那几类冻结依据。

    当前覆盖：项目、绑定、环境、计划（这四类由本包的工作单元落盘）。
    其余来源见 `UNCOVERED_SOURCES`，一律列入 `uncovered`。
    """
    project_id = prepared_run.project_id
    checks = (
        BasisCheck(
            source_kind="binding",
            record_id=prepared_run.binding_id,
            revision=prepared_run.binding_revision,
            readable=_readable(
                reader,
                project_id=project_id,
                aggregate_kind="binding",
                record_id=prepared_run.binding_id,
                revision=prepared_run.binding_revision,
            ),
        ),
        BasisCheck(
            source_kind="environment",
            record_id=prepared_run.environment.environment_id,
            revision=prepared_run.environment.revision,
            readable=_readable(
                reader,
                project_id=project_id,
                aggregate_kind="environment",
                record_id=prepared_run.environment.environment_id,
                revision=prepared_run.environment.revision,
            ),
        ),
        BasisCheck(
            source_kind="plan",
            record_id=prepared_run.plan_revision.revision_id,
            revision=prepared_run.plan_revision.revision_no,
            readable=_readable(
                reader,
                project_id=project_id,
                aggregate_kind="plan",
                record_id=prepared_run.plan_revision.revision_id,
                revision=prepared_run.plan_revision.revision_no,
            ),
        ),
    )
    return DriftReport(checks=checks, uncovered=UNCOVERED_SOURCES)


__all__ = [
    "UNCOVERED_SOURCES",
    "BasisCheck",
    "DriftReport",
    "check_frozen_basis",
]
