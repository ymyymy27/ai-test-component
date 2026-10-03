"""准备 → 启动之间的漂移核对链：核对 `PreparedRun` 冻结的依据还在不在。

对应检查文档 B-04 第③条"准备与启动之间无完整漂移核对链"。

`PreparedRun` 冻结了各来源的**准确修订**。发起运行之前必须能**按那些修订读回**它们——
否则这次运行用的是"已经不是当初依据"的来源。记录不可变，因此"能按准确修订读到"
就等于"依据完好"；读不到就是缺口，**不得**退化成"没有影响"。

## 覆盖范围（2026-10-03 扩展）

| 类别 | 核对方式 |
| --- | --- |
| `binding` / `environment` / `plan` | 按标识 + 准确修订读记录 |
| `case_revisions` | 按 `case_id` + 准确修订读 `case` 记录 |
| `rule_versions` | 按 `rule_id` + 准确修订读 `rule_version` 记录 |
| `template_versions` | 按 `template_id` + `version` 装载**已安装模板资源**（模板不是记录） |

**仍然没有证据的类别**见 `UNCOVERED_SOURCES`，如实列进 `uncovered`，不假装核对过：
少报覆盖比多报覆盖危险得多——前者会让人以为依据已经查过了。

三类的**缺口原因各不相同**，都属"合同字段或记录类别缺口"，不是"依据不存在"：

1. `project_revision`：`PreparedRun` 只冻结 `project_id`，**没有项目修订**；
2. `source_snapshot`：一期**还没有快照记录类别**（端口与适配归 A）；
3. `acceptance_scope`：只有**修订号、没有 `scope_id`**，不知道要读哪一条 scope 记录。
"""

from __future__ import annotations

from dataclasses import dataclass

from aitest.application.planning.substrate import RecordReader
from aitest.contracts.prepared_run import PreparedRun

#: `read()` 逐个探测修订的上限。只追加记录的一期里不会有这个量级的修订；
#: 设上限是为了让"读到很高的修订号"这件事不至于变成无界循环。
_MAX_REVISION_PROBE = 1024


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
        return all(check.readable for check in self.checks)

    @property
    def missing(self) -> tuple[BasisCheck, ...]:
        """读不回来的依据；非空即表示冻结依据出现缺口。"""
        return tuple(check for check in self.checks if not check.readable)


#: 一期**没有记录可核、或合同里根本没冻结标识**的来源类别。
#: 列出来而不是忽略：忽略会让调用方以为它们被查过。
UNCOVERED_SOURCES: tuple[str, ...] = (
    # `PreparedRun` 合同**没有冻结项目修订**（只有 project_id），
    # 因此项目依据无从按准确修订核对；这是合同字段缺口，不是"项目不存在"。
    "project_revision",
    # `acceptance_scope_revision` 在 `PreparedRun` 里**只有修订号、没有 scope_id**
    # （见 `prepare_run.py` 第 99 行：取自 `plan.scope.revision`）。
    # 不知道要读哪个 scope 记录，就无法按准确修订核对——这同样是合同字段缺口。
    "acceptance_scope",
)

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
        reader.read(
            aggregate_kind=aggregate_kind,  # type: ignore[arg-type]
            record_id=record_id,
            revision=revision,
        )
    except ValueError:
        return False
    return True


def _latest_revision(
    reader: RecordReader, *, aggregate_kind: str, record_id: str
) -> tuple[int, dict[str, object]] | None:
    """只用 `read()` 逐个修订读出**最高存在的那一版**。

    为什么不 `query()`：`RecordReader` 协议虽有 `query`，但它的可用性取决于实现
    （查询走索引，需先建索引；内存替身与真实存储的行为不同）。
    核对链要能在任何合规的 `read()` 实现上工作，因此这里只用 `read()`。
    记录是只追加的，所以从 1 开始读到第一次读不到为止即可。
    """
    latest: tuple[int, dict[str, object]] | None = None
    for revision in range(1, _MAX_REVISION_PROBE + 1):
        try:
            record = reader.read(
                aggregate_kind=aggregate_kind,  # type: ignore[arg-type]
                record_id=record_id,
                revision=revision,
            )
        except ValueError:
            break
        latest = (record.revision, dict(record.payload))
    return latest


def _snapshot_identity_check(
    reader: RecordReader, *, snapshot_id: str, frozen_identity: str
) -> tuple[bool, str]:
    """核对源码快照的**内容身份**是否与冻结值一致。

    `SnapshotRef` **没有修订号**（只有 `source_snapshot_id` / `purpose` /
    `content_identity`），所以核对方式不是"按修订读"，而是：

    1. 读出该快照**最高存在的那一版**（记录只追加）；
    2. 比对记录里的 `content_identity` 与冻结值。

    第 2 步才是关键：记录存在但**身份不同**，说明源码内容已经变了，
    这次运行的依据**不是当初固定下来的那份**——这比"记录读不到"更隐蔽，
    必须单独报出来。
    """
    found = _latest_revision(
        reader, aggregate_kind="source_snapshot", record_id=snapshot_id
    )
    if found is None:
        return False, "the frozen source snapshot has no persisted record"
    _, payload = found
    stored = payload.get("content_identity")
    if stored != frozen_identity:
        return False, (
            "the persisted snapshot content identity differs from the frozen one: "
            f"{stored!r} != {frozen_identity!r}"
        )
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
    """核对 `PreparedRun` 里**能核对的**那几类冻结依据。

    覆盖的类别（2026-10-03 扩展后）：

    | 类别 | 核对方式 |
    | --- | --- |
    | `binding` / `environment` / `plan` | 按标识 + **准确修订**读记录 |
    | `case_revisions` | 按 `case_id` + **准确修订**读 `case` 记录 |
    | `rule_versions` | 按 `rule_id` + **准确修订**读 `rule_version` 记录 |
    | `template_versions` | 按 `template_id` + `version` **装载已安装模板资源** |
    | `source_snapshot` | 按 id 取最新修订，比对**内容身份**是否与冻结值一致 |

    模板不是记录（它装在包资源里），因此核对方式是"冻结的那个版本还在不在"，
    与"某修订号读不读得到"不同——这一点在结果里用 `record_id` 前缀
    `template:` 标出，避免两类核对被混读。

    源码快照的 `SnapshotRef` **没有修订号**，所以核对的是**内容身份**：
    记录存在但身份不同，说明源码内容已经变了，依据不再是当初固定下来的那份。

    仍然 `uncovered` 的见 `UNCOVERED_SOURCES`（项目修订、验收范围）。
    """
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

    # 源码快照：按 id 取最新修订并比对**内容身份**（`SnapshotRef` 没有修订号）。
    snapshot = prepared_run.snapshot
    snapshot_readable, snapshot_detail = _snapshot_identity_check(
        reader,
        snapshot_id=snapshot.source_snapshot_id,
        frozen_identity=snapshot.content_identity,
    )
    checks.append(
        BasisCheck(
            source_kind="source_snapshot",
            record_id=snapshot.source_snapshot_id,
            revision=0,
            readable=snapshot_readable,
            detail=snapshot_detail,
        )
    )

    return DriftReport(checks=tuple(checks), uncovered=UNCOVERED_SOURCES)


__all__ = [
    "UNCOVERED_SOURCES",
    "BasisCheck",
    "DriftReport",
    "check_frozen_basis",
]
