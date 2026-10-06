"""项目上下文的落盘编排：把已建好的上下文对象存成不可变记录。

分工：`application/project/context.py` 负责**建对象**（领域守卫与缺口检测），
本模块负责**把对象落盘**。分开的理由是事务边界不同——建对象不需要事务，
落盘需要，而"建"不应该被迫持有排他写锁。

记录约定（类别取值与 `substrate.AggregateKind` 一致）：

| 对象 | 记录类别 | 记录标识 |
| --- | --- | --- |
| `LocalProject`（模块随项目一起） | `project` | `local_project_id` |
| `LocalProjectBinding` | `binding` | `binding_id` |
| `EnvironmentRef` | `environment` | `environment_id` |
| `ModuleDependencyGraph` | `dependency_set` | `graph:<project_id>` |

三条硬约束：

1. **不自动覆盖**：`expected_revision` 是调用方**看到过**的修订。传 `None` 表示
   "我认定这是新建"，此时当前已有记录就抛 `ConcurrentEditError` 并带上当前修订；
   要追加新修订就把看到的修订号传进来。与架构文档《01-项目与计划》第 8 节末一致。
2. **一次短事务**：每个 `save_*` 经 `substrate.transaction()` 自带 `open` / `commit`，
   调用时**不得已有打开的事务**。这样做是为了让"建对象"与"落盘"的边界在调用点可见；
   事务上下文的生命周期就是排他写锁的生命周期，调用方不需要自己写收尾。
3. **只经端口**：本模块只用 `UnitOfWork` / `RecordReader` 两个窄协议，
   不直接读写任何业务文件（端口实现归 A）。
"""

from __future__ import annotations

from collections.abc import Mapping

from aitest.application.planning.substrate import (
    AggregateKind,
    RecordReader,
    StagedRevision,
    UnitOfWork,
    read_scoped_record,
    transaction,
)
from aitest.application.project.serialization import (
    binding_from_payload,
    binding_to_payload,
    delivery_from_payload,
    delivery_to_payload,
    dependency_graph_from_payload,
    dependency_graph_to_payload,
    environment_from_payload,
    environment_to_payload,
    project_from_payload,
    project_to_payload,
    source_manifest_from_payload,
    source_manifest_to_payload,
    task_from_payload,
    task_to_payload,
)
from aitest.domain.project.context import (
    Delivery,
    EnvironmentRef,
    LocalProject,
    LocalProjectBinding,
    ModuleDependencyGraph,
    SourceManifest,
    Task,
    source_content_identity,
)

#: 依赖图的记录标识前缀；一个项目一份当前依赖图。
_DEPENDENCY_GRAPH_PREFIX = "graph:"

#: 源码快照的用途取值（`AB-001` 第 11.2 节）：只有这两个。
_SNAPSHOT_PURPOSES: frozenset[str] = frozenset({"analysis", "prepare"})


def dependency_graph_record_id(project_id: str) -> str:
    """依赖图的稳定记录标识。"""
    if not project_id.strip():
        raise ValueError("project_id must not be empty")
    return f"{_DEPENDENCY_GRAPH_PREFIX}{project_id}"


def _stage_and_commit(
    *,
    project_id: str,
    aggregate_kind: AggregateKind,
    record_id: str,
    expected_revision: int | None,
    payload: dict[str, object],
    unit_of_work: UnitOfWork,
) -> StagedRevision:
    """一次短事务：在**事务上下文**里暂存并提交。

    用 `transaction()` 而不是 `open()` / `try`…`finally`：真实底座的 `open()`
    会取工作空间级排他写锁，收尾一旦靠调用方的记性，抛异常或提前返回就会把锁
    留在这个进程里。上下文对象负责收尾（见 `substrate.Transaction`）。

    写入前先核对**正文自己的项目**与本次写入项目一致（检查项 B-12）：正文带
    `project_id` 就不是可选项，而是这条记录归属谁的唯一凭据；正文不带则该记录
    归属未知，同样拒绝——不允许"没写项目"被读成"哪个项目都能写"。
    """
    _verify_payload_project(
        payload,
        project_id=project_id,
        aggregate_kind=aggregate_kind,
        record_id=record_id,
    )
    with transaction(unit_of_work, project_id) as tx:
        staged = tx.stage_record(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            expected_revision=expected_revision,
            payload=payload,
        )
        tx.commit()
    return staged


def _load_payload(
    reader: RecordReader,
    *,
    project_id: str,
    aggregate_kind: AggregateKind,
    record_id: str,
    revision: int,
) -> dict[str, object]:
    record = read_scoped_record(
        reader,
        project_id=project_id,
        aggregate_kind=aggregate_kind,
        record_id=record_id,
        revision=revision,
    )
    payload = dict(record.payload)
    _verify_payload_project(
        payload,
        project_id=project_id,
        aggregate_kind=aggregate_kind,
        record_id=record_id,
    )
    return payload


def _verify_payload_project(
    payload: Mapping[str, object],
    *,
    project_id: str,
    aggregate_kind: AggregateKind,
    record_id: str,
) -> None:
    """项目归属校验：**缺失与不符都拒绝**，不把"没有项目"当成"任何项目都能读"。

    检查项 B-12：过去只在 `stored_project is not None` 时比较，于是正文里没有项目字段的
    记录（例如 `Delivery`）可以被**任意项目**读回；写入侧也只看 `command.project_id` 非空，
    不看正文/引用/策略自己的项目。这里把两侧合成一条规则：

    - 正文带 `project_id` 且与本次访问的项目不同 → 拒绝（跨项目）；
    - 正文**不带** `project_id` → 拒绝并说明这是归属未知的旧记录，
      需要显式迁移或由调用方判为阻塞（与检查文档"旧记录归属未知应显式阻塞或迁移"一致）。
    """
    stored_project = payload.get("project_id")
    if stored_project is None:
        raise ValueError(
            f"{aggregate_kind} {record_id} carries no project_id, so its owning project "
            "is unknown; it cannot be read through a project-scoped lookup"
        )
    if stored_project != project_id:
        raise ValueError(f"{aggregate_kind} {record_id} belongs to another project")


# ------------------------------------------------------------------ 写入


def save_project(
    project: LocalProject,
    *,
    unit_of_work: UnitOfWork,
    expected_revision: int | None = None,
) -> StagedRevision:
    """保存项目（模块随项目一起落盘）。"""
    return _stage_and_commit(
        project_id=project.project_id,
        aggregate_kind="project",
        record_id=project.local_project_id,
        expected_revision=expected_revision,
        payload=project_to_payload(project),
        unit_of_work=unit_of_work,
    )


def save_binding(
    binding: LocalProjectBinding,
    *,
    unit_of_work: UnitOfWork,
    expected_revision: int | None = None,
) -> StagedRevision:
    """保存项目绑定；`git` / `plain` 形态的差异由序列化层负责省略不适用键。"""
    return _stage_and_commit(
        project_id=binding.project_id,
        aggregate_kind="binding",
        record_id=binding.binding_id,
        expected_revision=expected_revision,
        payload=binding_to_payload(binding),
        unit_of_work=unit_of_work,
    )


def save_environment(
    environment: EnvironmentRef,
    *,
    project_id: str,
    unit_of_work: UnitOfWork,
    expected_revision: int | None = None,
) -> StagedRevision:
    """保存环境引用。

    `EnvironmentRef` 自身不带项目，项目由调用方显式给出——记录需要项目范围才可查询。
    """
    if not project_id.strip():
        raise ValueError("project_id must not be empty")
    return _stage_and_commit(
        project_id=project_id,
        aggregate_kind="environment",
        record_id=environment.environment_id,
        expected_revision=expected_revision,
        payload=environment_to_payload(environment, project_id=project_id),
        unit_of_work=unit_of_work,
    )


def save_dependency_graph(
    graph: ModuleDependencyGraph,
    *,
    unit_of_work: UnitOfWork,
    expected_revision: int | None = None,
) -> StagedRevision:
    """保存模块依赖图（一个项目一份当前图，修订随变更递增）。"""
    return _stage_and_commit(
        project_id=graph.project_id,
        aggregate_kind="dependency_set",
        record_id=dependency_graph_record_id(graph.project_id),
        expected_revision=expected_revision,
        payload=dependency_graph_to_payload(graph),
        unit_of_work=unit_of_work,
    )


# ------------------------------------------------------------------ 读取


def save_source_snapshot(
    manifest: SourceManifest,
    *,
    project_id: str,
    snapshot_id: str,
    purpose: str,
    unit_of_work: UnitOfWork,
    expected_revision: int | None = None,
) -> StagedRevision:
    """把一份**源码内容身份**固定成 `source_snapshot` 记录（检查文档 B-04）。

    这是"实际来源 → 正式快照"的**落盘那一跳**：`SourceManifest` 是内容身份，
    记录是它的不可变固定事实；`content_identity` 随记录一起落盘，
    读取方据此核对"这份快照的身份没被改过"，而不是重新扫描目录。

    `snapshot_id` 由调用方给出（同一逻辑快照重新固定必须产生**新** id，不复用）；
    `purpose` 只允许 `analysis` / `prepare`（`AB-001` 第 11.2 节）。
    """
    if not project_id.strip():
        raise ValueError("project_id must not be empty")
    if not snapshot_id.strip():
        raise ValueError("snapshot_id must not be empty")
    if purpose not in _SNAPSHOT_PURPOSES:
        raise ValueError(
            f"unknown snapshot purpose: {purpose!r} (expected one of {_SNAPSHOT_PURPOSES})"
        )
    return _stage_and_commit(
        project_id=project_id,
        aggregate_kind="source_snapshot",
        record_id=snapshot_id,
        expected_revision=expected_revision,
        payload=source_manifest_to_payload(
            manifest, project_id=project_id, snapshot_id=snapshot_id, purpose=purpose
        ),
        unit_of_work=unit_of_work,
    )


def load_source_snapshot(
    reader: RecordReader, *, project_id: str, snapshot_id: str, revision: int
) -> SourceManifest:
    """按**准确修订**读回一份源码内容身份。

    记录里的 `content_identity` 会与按当前 payload 重算的结果比对：
    **不一致即报错**——那说明记录的内容身份与字节对不上，
    继续读下去等于拿一份自相矛盾的依据去做核对。
    """
    payload = _load_payload(
        reader,
        project_id=project_id,
        aggregate_kind="source_snapshot",
        record_id=snapshot_id,
        revision=revision,
    )
    manifest = source_manifest_from_payload(payload)
    stored_identity = payload.get("content_identity")
    recomputed = source_content_identity(manifest)
    if stored_identity != recomputed:
        raise ValueError(
            f"source_snapshot {snapshot_id} revision {revision} carries a content "
            f"identity that does not match its bytes: {stored_identity!r} != {recomputed!r}"
        )
    return manifest


def load_project(reader: RecordReader, *, project_id: str, revision: int) -> LocalProject:
    """按**准确修订**读回项目；没有"读最新"入口（实施方案第 3 节）。

    记录标识与 `create_project()` 写入时一致（`local_project_id = project_id`）；
    两者若将来分叉，`tests/unit/test_project_persistence.py` 的往返测试会先失败。
    """
    payload = _load_payload(
        reader,
        project_id=project_id,
        aggregate_kind="project",
        record_id=project_id,
        revision=revision,
    )
    return project_from_payload(payload)


def load_binding(
    reader: RecordReader, *, project_id: str, binding_id: str, revision: int
) -> LocalProjectBinding:
    payload = _load_payload(
        reader,
        project_id=project_id,
        aggregate_kind="binding",
        record_id=binding_id,
        revision=revision,
    )
    return binding_from_payload(payload)


def load_environment(
    reader: RecordReader, *, project_id: str, environment_id: str, revision: int
) -> EnvironmentRef:
    payload = _load_payload(
        reader,
        project_id=project_id,
        aggregate_kind="environment",
        record_id=environment_id,
        revision=revision,
    )
    return environment_from_payload(payload)


def load_dependency_graph(
    reader: RecordReader, *, project_id: str, revision: int
) -> ModuleDependencyGraph:
    payload = _load_payload(
        reader,
        project_id=project_id,
        aggregate_kind="dependency_set",
        record_id=dependency_graph_record_id(project_id),
        revision=revision,
    )
    return dependency_graph_from_payload(payload)


# ------------------------------------------------------------------ 任务与交付


def save_task(
    task: Task,
    *,
    unit_of_work: UnitOfWork,
    expected_revision: int | None = None,
) -> StagedRevision:
    """保存一个任务的某个修订。

    验收项（`AcceptanceItem`）随任务一起落盘，不单独建记录：它在领域里没有独立身份、
    始终属于某个任务（与 `module` 随项目一起落盘同一处理方式）。
    """
    return _stage_and_commit(
        project_id=task.project_id,
        aggregate_kind="task",
        record_id=task.task_id,
        expected_revision=expected_revision,
        payload=task_to_payload(task),
        unit_of_work=unit_of_work,
    )


def save_delivery(
    delivery: Delivery,
    *,
    project_id: str,
    unit_of_work: UnitOfWork,
    expected_revision: int | None = None,
    reader: RecordReader | None = None,
    task_revision: int = 1,
) -> StagedRevision:
    """保存一份交付说明的某个修订。

    交付说明自身不带项目字段，项目由调用方显式给出——记录需要项目范围才可查询。
    落盘形状把**自述与验证事实分列**，不给"把自述当验证事实"留通道。

    **验证范围不接受调用方自填**（检查项 B-13）：`verified_in_scope` 只能由执行事实
    派生，"提测者说已验证"不是证据。过去 `save_delivery` 照收 `verified_in_scope`，
    读回 `Delivery.is_verified` 直接为真，等于让提测者自己宣布测试通过。这里改为拒绝：
    要声明什么还没验证，写 `unverified_scope`（那本来就是声明字段）；已验证范围由后续
    的执行/核验事实投影产生，不从这里写入。
    """
    if not project_id.strip():
        raise ValueError("project_id must not be empty")
    if delivery.verified_in_scope:
        raise ValueError(
            "verified_in_scope is derived from execution facts and must not be "
            "self-declared on save; record what is not verified in unverified_scope "
            f"instead (got {list(delivery.verified_in_scope)!r})"
        )
    if reader is None:
        raise ValueError("delivery requires an authoritative task reader")
    # 准确任务修订与交付写入处于同一短事务，不查询另一项目或猜一个任务。
    with transaction(unit_of_work, project_id) as tx:
        task = load_task(
            reader,
            project_id=project_id,
            task_id=delivery.task_id,
            revision=task_revision,
        )
        if task.project_id != project_id:
            raise ValueError("delivery task belongs to another project")
        payload = delivery_to_payload(delivery, project_id=project_id)
        payload["task_revision"] = task_revision
        staged = tx.stage_record(
            aggregate_kind="delivery",
            record_id=delivery.delivery_id,
            expected_revision=expected_revision,
            payload=payload,
        )
        tx.commit()
        return staged


def load_task(reader: RecordReader, *, project_id: str, task_id: str, revision: int) -> Task:
    payload = _load_payload(
        reader,
        project_id=project_id,
        aggregate_kind="task",
        record_id=task_id,
        revision=revision,
    )
    return task_from_payload(payload)


def load_delivery(
    reader: RecordReader, *, project_id: str, delivery_id: str, revision: int
) -> Delivery:
    payload = _load_payload(
        reader,
        project_id=project_id,
        aggregate_kind="delivery",
        record_id=delivery_id,
        revision=revision,
    )
    return delivery_from_payload(payload)


__all__ = [
    "dependency_graph_record_id",
    "load_binding",
    "load_delivery",
    "load_dependency_graph",
    "load_environment",
    "load_project",
    "load_source_snapshot",
    "load_task",
    "save_binding",
    "save_delivery",
    "save_dependency_graph",
    "save_environment",
    "save_project",
    "save_source_snapshot",
    "save_task",
]
