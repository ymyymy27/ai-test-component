"""按冻结的仓储引用核对依据；旧记录缺少冻结标识时要求重新准备。

正文版本不是仓储修订。源码快照按准确记录修订读取，并比较 content_identity。
本模块验证冻结材料，不把历史材料可读性冒充当前源码一致性。
"""

from __future__ import annotations

from dataclasses import dataclass

from aitest.application.planning.substrate import RecordReader
from aitest.contracts.prepared_run import PreparedRun


@dataclass(frozen=True, slots=True)
class BasisCheck:
    """一条冻结依据的可读性核对结果。

    `readable=False` 只表示**这条依据不可核**，不表示"项目没有它"。
    `detail` 给出可核对的失败原因（例如模板资源缺失），便于如实呈现而不是只回一个布尔。
    """

    source_kind: str
    record_id: str
    revision: int
    readable: bool
    detail: str = ""


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
        return (
            not self.uncovered
            and bool(self.checks)
            and all(check.readable for check in self.checks)
        )

    @property
    def missing(self) -> tuple[BasisCheck, ...]:
        """读不回来的依据；非空即表示冻结依据出现缺口。"""
        return tuple(check for check in self.checks if not check.readable)


#: 一期**没有记录可核、或合同里根本没冻结标识**的来源类别。
#: 列出来而不是忽略：忽略会让调用方以为它们被查过。
UNCOVERED_SOURCES: tuple[str, ...] = ()

#: 模板资源不算"记录"：它按 `<template_id>/<version>.json` 安装在包资源里，
#: 因此核对方式是**能否按被冻结的标识+版本装载**，而不是按修订读记录。
_TEMPLATE_PREFIX = "template:"


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
        record = reader.read(
            aggregate_kind=aggregate_kind,  # type: ignore[arg-type]
            record_id=record_id,
            revision=revision,
        )
    except (ValueError, OSError):
        return False
    return record.payload.get("project_id", record.payload.get("local_project_id")) == project_id


def _snapshot_identity_check(
    reader: RecordReader,
    *,
    project_id: str,
    snapshot_id: str,
    record_revision: int,
    frozen_identity: str,
) -> tuple[bool, str]:
    try:
        record = reader.read(
            aggregate_kind="source_snapshot", record_id=snapshot_id, revision=record_revision
        )
    except (ValueError, OSError):
        return False, "the frozen source snapshot has no persisted record"
    if record.payload.get("project_id") != project_id:
        return False, "the frozen source snapshot belongs to another project"
    if record.payload.get("content_identity") != frozen_identity:
        return False, "the persisted snapshot content identity differs from the frozen one"
    return True, ""


def _template_readable(template_id: str, version: str) -> tuple[bool, str]:
    """模板版本按**标识+版本**核对：能否从已安装资源装载。

    模板是按 `<template_id>/<version>.json` 安装在包资源里的，不是记录，
    所以这里核对的是"冻结的那个版本还在不在"，不是"某个修订号读不读得到"。
    """
    from aitest.application.planning.draft import TemplateNotFoundError, load_template
    from aitest.domain.planning.templates import TemplateRef

    try:
        pack = load_template(TemplateRef(template_id=template_id, version=version))
    except (TemplateNotFoundError, ValueError, OSError) as error:
        return False, f"template resource is not loadable: {type(error).__name__}"
    if pack.template_id != template_id or pack.version != version:
        return False, "loaded template identity does not match the frozen reference"
    return True, ""


def check_frozen_basis(prepared_run: PreparedRun, *, reader: RecordReader) -> DriftReport:
    """准确读取冻结修订；所有材料必须归属同一项目。"""
    project_id = prepared_run.project_id
    checks: list[BasisCheck] = [
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
    ]

    # 用例修订：按 `case_id` + 准确修订读回（`case` 记录由本包落盘）。
    checks += [
        BasisCheck(
            source_kind="case_revisions",
            record_id=ref.case_id,
            revision=ref.revision,
            readable=_readable(
                reader,
                project_id=project_id,
                aggregate_kind="case",
                record_id=ref.case_id,
                revision=ref.revision,
            ),
        )
        for ref in prepared_run.case_revisions
    ]

    # 已发布规则版本：按 `rule_id` + 准确修订读回（`rule_version` 由发布落盘）。
    checks += [
        BasisCheck(
            source_kind="rule_versions",
            record_id=ref.rule_id,
            revision=ref.revision,
            readable=_readable(
                reader,
                project_id=project_id,
                aggregate_kind="rule_version",
                record_id=ref.rule_id,
                revision=ref.revision,
            ),
        )
        for ref in prepared_run.rule_versions
    ]

    # 模板版本：按标识 + 版本装载已安装资源（修订位用 0 表示"不适用修订号"）。
    for template_ref in prepared_run.template_versions:
        loadable, detail = _template_readable(template_ref.template_id, template_ref.version)
        checks.append(
            BasisCheck(
                source_kind="template_versions",
                record_id=f"{_TEMPLATE_PREFIX}{template_ref.template_id}@{template_ref.version}",
                revision=0,
                readable=loadable,
                detail=detail,
            )
        )

    # 源码快照：按冻结仓储修订读取，并核对内容身份。
    snapshot = prepared_run.snapshot
    snapshot_readable, snapshot_detail = _snapshot_identity_check(
        reader,
        project_id=project_id,
        snapshot_id=snapshot.source_snapshot_id,
        record_revision=snapshot.record_revision,
        frozen_identity=snapshot.content_identity,
    )
    checks.append(
        BasisCheck(
            source_kind="source_snapshot",
            record_id=snapshot.source_snapshot_id,
            revision=snapshot.record_revision,
            readable=snapshot_readable,
            detail=snapshot_detail,
        )
    )

    for kind, aggregate, record_id, revision in (
        ("project_revision", "project", project_id, prepared_run.project_revision),
        (
            "acceptance_scope",
            "acceptance_scope",
            prepared_run.scope_id,
            prepared_run.acceptance_scope_revision,
        ),
    ):
        present = record_id is not None and revision is not None
        readable = present and _readable(
            reader,
            project_id=project_id,
            aggregate_kind=aggregate,
            record_id=record_id or "missing",
            revision=revision or 1,
        )
        checks.append(
            BasisCheck(
                source_kind=kind,
                record_id=record_id or "missing",
                revision=revision or 0,
                readable=readable,
                detail="" if present else "needs_reprepare: legacy frozen reference is missing",
            )
        )
    return DriftReport(checks=tuple(checks), uncovered=UNCOVERED_SOURCES)


__all__ = [
    "UNCOVERED_SOURCES",
    "BasisCheck",
    "DriftReport",
    "check_frozen_basis",
]
