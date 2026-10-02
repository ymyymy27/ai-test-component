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

from aitest.application.planning.substrate import (
    AggregateKind,
    RecordReader,
    StagedRevision,
    UnitOfWork,
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
    task_from_payload,
    task_to_payload,
)
from aitest.domain.project.context import (
    Delivery,
    EnvironmentRef,
    LocalProject,
    LocalProjectBinding,
    ModuleDependencyGraph,
    Task,
)

#: 依赖图的记录标识前缀；一个项目一份当前依赖图。
_DEPENDENCY_GRAPH_PREFIX = "graph:"


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
    """
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
    record = reader.read(
        aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
    )
    payload = dict(record.payload)
    # 项目校验：跨项目的记录不得被当成同一条读到。
    stored_project = payload.get("project_id")
    if stored_project is not None and stored_project != project_id:
        raise ValueError(
            f"{aggregate_kind} {record_id} belongs to another project"
        )
    return payload


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


def load_project(
    reader: RecordReader, *, project_id: str, revision: int
) -> LocalProject:
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
) -> StagedRevision:
    """保存一份交付说明的某个修订。

    交付说明自身不带项目字段，项目由调用方显式给出——记录需要项目范围才可查询。
    落盘形状把**自述与验证事实分列**，不给"把自述当验证事实"留通道。
    """
    if not project_id.strip():
        raise ValueError("project_id must not be empty")
    return _stage_and_commit(
        project_id=project_id,
        aggregate_kind="delivery",
        record_id=delivery.delivery_id,
        expected_revision=expected_revision,
        payload=delivery_to_payload(delivery),
        unit_of_work=unit_of_work,
    )


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
    "load_task",
    "save_binding",
    "save_delivery",
    "save_dependency_graph",
    "save_environment",
    "save_project",
    "save_task",
]
