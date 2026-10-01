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
from dataclasses import dataclass

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


#: 源码范围里出现这些写法时按"未登记"处理，不做任何匹配。
_UNREGISTERED_SCOPES = frozenset({""})


def _normalise_source_path(path: str) -> str:
    """把源码路径归一成平台中立的相对写法。

    分隔符统一成 `/`、去掉首尾斜杠与 `./` 前缀。
    **不用 `pathlib`**：同一个相对路径在 POSIX 与 Windows 下会被判成不同对象，
    而这类差异在 Windows 本机测不出来（仓库既有约定：判路径要避免平台相关行为）。
    """
    text = path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def _covers(changed: str, scope: str) -> bool:
    """`scope` 是否覆盖 `changed`：按**路径段**判前缀，不做字符串前缀。

    字符串前缀会把 `src/ticket-old` 误判成落在 `src/ticket` 之内。
    """
    return changed == scope or changed.startswith(f"{scope}/")


@dataclass(frozen=True, slots=True)
class RegressionScope:
    """一次回归的影响面。

    `unregistered_modules` 是**未登记源码范围**的模块（`source_paths` 为空）。
    它们**不参与匹配**，也不被当成"影响全部"；调用方必须显式处理，
    否则要么漏掉回归、要么把影响面说大。两种都会伪造完整的影响面（`DEC-006`）。
    """

    directly_changed: frozenset[str]
    affected_modules: frozenset[str]
    unregistered_modules: tuple[str, ...] = ()


def changed_modules(
    changed_files: Iterable[str], graph: ModuleDependencyGraph
) -> frozenset[str]:
    """把**实际变化的文件**映射到直接受影响的模块。

    只按模块登记的 `source_paths` 匹配；未登记的模块不在此结果里
    （见 `RegressionScope.unregistered_modules`）。
    """
    scopes: list[tuple[str, str]] = []
    for module in graph.modules:
        for raw in module.source_paths:
            scope = _normalise_source_path(raw)
            if scope not in _UNREGISTERED_SCOPES:
                scopes.append((module.module_id, scope))

    hit: set[str] = set()
    for raw in changed_files:
        changed = _normalise_source_path(raw)
        if changed in _UNREGISTERED_SCOPES:
            continue
        for module_id, scope in scopes:
            if _covers(changed, scope):
                hit.add(module_id)
    return frozenset(hit)


def regression_scope(
    changed_files: Iterable[str], graph: ModuleDependencyGraph
) -> RegressionScope:
    """一次回归的完整影响面：**变化文件 → 直接模块 → 反向传播**。

    与 `affected_modules_from_graph()` 的区别是入口不同：这里从**文件**开始，
    因此第一次把"变化文件 → 模块"这一跳落在 B 侧（`DEC-006` 裁定为甲）。
    """
    if not graph.modules:
        raise ValueError("a dependency graph without modules cannot scope a regression")
    direct = changed_modules(changed_files, graph)
    unregistered = tuple(
        sorted(
            module.module_id
            for module in graph.modules
            if not any(
                _normalise_source_path(raw) not in _UNREGISTERED_SCOPES
                for raw in module.source_paths
            )
        )
    )
    return RegressionScope(
        directly_changed=direct,
        affected_modules=affected_modules_from_graph(sorted(direct), graph),
        unregistered_modules=unregistered,
    )


__all__ = [
    "RegressionScope",
    "affected_modules_from_graph",
    "affected_modules_from_store",
    "changed_modules",
    "graph_dependency_map",
    "regression_scope",
]
