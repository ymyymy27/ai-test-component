"""回归影响面：从**落盘的依赖图**反向传播。

依据：`docs/一期工程检查-B包.md` 第 3.2 节 B-04（"回归建议不再由调用方提供变更与模块映射"）；
`docs/文档-feix-a/B包/09-待解决问题清单.md` B-Q13（"变化文件 → 模块"这一跳仍缺字段，未闭合）。

本模块覆盖可以独立完成的那一段：**从模块到影响面**用冻结记录，不用调用方临时拼的 dict。
"""

from __future__ import annotations

from aitest.application.planning.regression import affected_modules
from aitest.application.planning.regression_graph import (
    affected_modules_from_graph,
    affected_modules_from_store,
    graph_dependency_map,
)
from aitest.application.project.context import create_project, register_graph
from aitest.application.project.persistence import save_dependency_graph
from aitest.domain.project.context import (
    Dependency,
    ImplementationStatus,
    Module,
    ModuleDependencyGraph,
)
from tests.support.memory_substrate import MemoryReader, MemoryStore, MemoryUnitOfWork

PROJECT_ID = "project-ticket"


def _modules() -> tuple[Module, ...]:
    return (
        Module(
            module_id="module-api",
            project_id=PROJECT_ID,
            name="api",
            responsibility="expose handlers",
            interface_note="http",
        ),
        Module(
            module_id="module-core",
            project_id=PROJECT_ID,
            name="core",
            responsibility="business rules",
            interface_note="python api",
        ),
        Module(
            module_id="module-store",
            project_id=PROJECT_ID,
            name="store",
            responsibility="persist",
            interface_note="sql",
            implementation_status=ImplementationStatus.COMPLETE,
        ),
    )


def _graph(
    edges: tuple[tuple[str, str], ...] = (
        ("module-core", "module-store"),
        ("module-api", "module-core"),
    ),
) -> ModuleDependencyGraph:
    return register_graph(
        project_id=PROJECT_ID,
        modules=_modules(),
        dependencies=tuple(
            Dependency(consumer_module_id=consumer, provider_module_id=provider)
            for consumer, provider in edges
        ),
    )


# ------------------------------------------------------------------ 映射


def test_edges_fold_into_a_deterministic_consumer_map() -> None:
    graph = _graph()
    mapping = graph_dependency_map(graph)
    assert mapping == {
        "module-api": ("module-core",),
        "module-core": ("module-store",),
    }


def test_duplicate_edges_do_not_change_the_mapping() -> None:
    """同一对边重复登记不该改变影响面。"""
    graph = _graph(
        (
            ("module-core", "module-store"),
            ("module-core", "module-store"),
            ("module-api", "module-core"),
        )
    )
    assert graph_dependency_map(graph) == {
        "module-api": ("module-core",),
        "module-core": ("module-store",),
    }


# ------------------------------------------------------------------ 反向传播


def test_a_provider_change_reaches_every_transitive_consumer() -> None:
    graph = _graph()
    assert affected_modules_from_graph(["module-store"], graph) == frozenset(
        {"module-store", "module-core", "module-api"}
    )


def test_a_leaf_change_affects_only_itself() -> None:
    graph = _graph()
    assert affected_modules_from_graph(["module-api"], graph) == frozenset({"module-api"})


def test_the_changed_module_itself_is_always_included() -> None:
    graph = _graph()
    assert "module-core" in affected_modules_from_graph(["module-core"], graph)


def test_a_cycle_terminates_and_covers_the_whole_cycle() -> None:
    """环上的模块全部受影响，且传播必须终止。"""
    graph = _graph(
        (
            ("module-core", "module-store"),
            ("module-store", "module-core"),
            ("module-api", "module-core"),
        )
    )
    assert affected_modules_from_graph(["module-store"], graph) == frozenset(
        {"module-store", "module-core", "module-api"}
    )


def test_graph_based_result_matches_the_dict_based_one() -> None:
    """与既有 `affected_modules()` 结论一致：换来源不换语义。"""
    graph = _graph()
    assert affected_modules_from_graph(["module-store"], graph) == affected_modules(
        frozenset({"module-store"}),
        graph_dependency_map(graph),
    )


# ------------------------------------------------------------------ 读落盘的图


def test_affected_modules_come_from_the_stored_graph_revision() -> None:
    """按**准确修订**读回落盘的依赖图，再做传播。"""
    store = MemoryStore()
    unit_of_work = MemoryUnitOfWork(store)
    reader = MemoryReader(store)

    create_project(
        project_id=PROJECT_ID,
        workspace_id="ws-1",
        name="ticket service",
        goal="verify",
        created_at_commit="commit-0",
        modules=_modules(),
    )
    save_dependency_graph(_graph(), unit_of_work=unit_of_work)  # type: ignore[arg-type]

    assert affected_modules_from_store(
        ["module-store"], project_id=PROJECT_ID, reader=reader, graph_revision=1
    ) == frozenset({"module-store", "module-core", "module-api"})


def test_a_missing_graph_revision_is_explicit() -> None:
    """图修订不存在时明确报错，**不退化成"没有影响面"**。"""
    store = MemoryStore()
    unit_of_work = MemoryUnitOfWork(store)
    reader = MemoryReader(store)
    save_dependency_graph(_graph(), unit_of_work=unit_of_work)  # type: ignore[arg-type]

    import pytest

    with pytest.raises(ValueError, match="unknown revision"):
        affected_modules_from_store(
            ["module-store"], project_id=PROJECT_ID, reader=reader, graph_revision=2
        )
