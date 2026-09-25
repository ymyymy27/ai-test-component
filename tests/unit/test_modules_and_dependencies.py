import pytest

from aitest.domain.project.context import (
    Dependency,
    DependencyOrigin,
    ImplementationStatus,
    Module,
    ModuleDependencyGraph,
    affected_modules,
)


def _module(module_id: str) -> Module:
    return Module(
        module_id=module_id,
        project_id="p1",
        name=module_id.upper(),
        responsibility="职责",
        interface_note="接口说明",
    )


def test_module_requires_responsibility_and_interface_note() -> None:
    with pytest.raises(ValueError, match="responsibility"):
        Module("api", "p1", "API", "", "接口说明")
    with pytest.raises(ValueError, match="interface_note"):
        Module("api", "p1", "API", "职责", "  ")
    with pytest.raises(ValueError, match="revision"):
        Module("api", "p1", "API", "职责", "接口说明", revision=0)


def test_module_records_inputs_outputs_and_status() -> None:
    module = Module(
        "api",
        "p1",
        "API",
        "职责",
        "接口说明",
        inputs=("订单服务",),
        outputs=("订单查询接口",),
        implementation_status=ImplementationStatus.PARTIAL,
    )
    assert module.inputs == ("订单服务",)
    assert module.outputs == ("订单查询接口",)
    assert module.implementation_status is ImplementationStatus.PARTIAL


def test_module_rejects_empty_input_entries() -> None:
    with pytest.raises(ValueError, match="inputs"):
        Module("api", "p1", "API", "职责", "接口说明", inputs=("  ",))


def test_dependency_edge_points_from_consumer_to_provider() -> None:
    edge = Dependency(consumer_module_id="web", provider_module_id="api")
    assert edge.consumer_module_id == "web"
    assert edge.provider_module_id == "api"
    assert edge.origin is DependencyOrigin.REGISTERED
    assert not edge.is_inferred


def test_dependency_rejects_self_loop() -> None:
    with pytest.raises(ValueError, match="itself"):
        Dependency(consumer_module_id="api", provider_module_id="api")


def test_inferred_dependency_requires_a_source() -> None:
    with pytest.raises(ValueError, match="source"):
        Dependency(
            consumer_module_id="web",
            provider_module_id="api",
            origin=DependencyOrigin.INFERRED,
        )
    edge = Dependency(
        consumer_module_id="web",
        provider_module_id="api",
        origin=DependencyOrigin.INFERRED,
        source="静态导入分析",
    )
    assert edge.is_inferred


def test_graph_projects_provider_ids_for_regression() -> None:
    graph = ModuleDependencyGraph(
        project_id="p1",
        modules=(_module("web"), _module("api")),
        dependencies=(Dependency("web", "api"), Dependency("web", "api")),
    )
    assert graph.provider_ids() == {"web": ("api",)}
    assert graph.inferred_edges() == ()


def test_graph_rejects_unknown_module_and_foreign_project() -> None:
    with pytest.raises(ValueError, match="unknown module"):
        ModuleDependencyGraph(
            project_id="p1",
            modules=(_module("web"),),
            dependencies=(Dependency("web", "api"),),
        )
    foreign = Module("web", "other", "WEB", "职责", "接口说明")
    with pytest.raises(ValueError, match="match the graph project"):
        ModuleDependencyGraph(project_id="p1", modules=(foreign,))


def test_cycle_is_legal_and_propagation_deduplicates() -> None:
    """循环只影响传播遍历，不能阻止建立项目（P1-FR01、FR03）。"""
    graph = ModuleDependencyGraph(
        project_id="p1",
        modules=(_module("a"), _module("b"), _module("c")),
        dependencies=(
            Dependency("a", "b"),
            Dependency("b", "a"),
            Dependency("c", "b"),
        ),
    )
    providers = graph.provider_ids()
    assert affected_modules(frozenset({"a"}), providers) == {"a", "b", "c"}


def test_changed_module_is_kept_even_without_any_edge() -> None:
    graph = ModuleDependencyGraph(project_id="p1", modules=(_module("solo"),))
    assert affected_modules(frozenset({"solo"}), graph.provider_ids()) == {"solo"}


def test_inferred_edges_are_listed_separately() -> None:
    graph = ModuleDependencyGraph(
        project_id="p1",
        modules=(_module("web"), _module("api")),
        dependencies=(
            Dependency(
                "web",
                "api",
                origin=DependencyOrigin.INFERRED,
                source="静态导入分析",
            ),
        ),
    )
    assert [edge.provider_module_id for edge in graph.inferred_edges()] == ["api"]
