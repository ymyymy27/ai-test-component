"""回归影响面：从**已冻结的模块依赖图**做反向传播。

检查文档 B-04 的完成条件要求"回归建议不再由调用方提供变更与模块映射，
用真实冻结来源/版本/环境/选定清单做映射和缺口"。本模块把其中**可以独立完成**的一步做掉：

- **映射来源**：从 `ModuleDependencyGraph`（经 `dependency_set` 记录落盘、按准确修订读回）
  取依赖边，而不是由调用方临时拼一个 dict；
- **反向传播**：变更模块本身与其全部上游消费者（含环）都算受影响。

**仍未闭合的一步**：`Module` 没有"模块 ↔ 源码路径"字段（见
`docs/文档-feix-a/B包/09-待解决问题清单.md` B-Q13），因此"实际变化的文件 → 变化的模块"
这一跳仍由调用方给出。本模块只保证**从模块到影响面**这一段用的是冻结事实。
"""

from __future__ import annotations

from collections.abc import Iterable

from aitest.application.planning.substrate import RecordReader
from aitest.application.project.persistence import load_dependency_graph
from aitest.domain.project.context import ModuleDependencyGraph


def graph_dependency_map(
    graph: ModuleDependencyGraph,
) -> dict[str, tuple[str, ...]]:
    """把依赖图的边列表折叠成 `消费者 -> 提供者` 映射。

    **同一对边只保留一次**：图上出现重复登记不该改变影响面。顺序固定，
    使同一张图在任何调用下得到同一个映射。
    """
    grouped: dict[str, set[str]] = {}
    for edge in graph.dependencies:
        grouped.setdefault(edge.consumer_module_id, set()).add(edge.provider_module_id)
    return {
        consumer: tuple(sorted(providers)) for consumer, providers in sorted(grouped.items())
    }


def affected_modules_from_graph(
    changed: Iterable[str], graph: ModuleDependencyGraph
) -> frozenset[str]:
    """按依赖图反向传播，返回受影响模块集合（含变更模块自身与环上的全部模块）。

    依赖图来自**冻结记录**，因此同一次回归请求在重放时得到同一个集合。
    """
    from aitest.application.planning.regression import affected_modules

    return affected_modules(frozenset(changed), graph_dependency_map(graph))


def affected_modules_from_store(
    changed: Iterable[str],
    *,
    project_id: str,
    reader: RecordReader,
    graph_revision: int,
) -> frozenset[str]:
    """按**准确修订**读回落盘的依赖图，再做反向传播。

    用显式修订而不是"读最新"：回归范围必须对应到被冻结的那张图，
    否则图一变，同一轮回归的结论就跟着变。
    """
    graph = load_dependency_graph(reader, project_id=project_id, revision=graph_revision)
    return affected_modules_from_graph(changed, graph)


__all__ = [
    "affected_modules_from_graph",
    "affected_modules_from_store",
    "graph_dependency_map",
]
