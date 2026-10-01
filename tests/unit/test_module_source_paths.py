"""模块的源码范围字段（`DEC-006` 裁定为甲）。

`Module.source_paths` 用于把"实际变化的文件"映射到模块，进而推导回归影响面。
**空表示"未登记源码范围"**，不得解释成"该模块影响所有文件"。
"""

from __future__ import annotations

import pytest

from aitest.application.project.context import create_project
from aitest.application.project.serialization import (
    project_from_payload,
    project_to_payload,
)
from aitest.domain.project.context import Module

PROJECT_ID = "project-ticket"


def _module(**overrides: object) -> Module:
    base: dict[str, object] = {
        "module_id": "module-ticket",
        "project_id": PROJECT_ID,
        "name": "ticket core",
        "responsibility": "create and read tickets",
        "interface_note": "registered HTTP handlers",
    }
    return Module(**(base | overrides))  # type: ignore[arg-type]


def _payload(module: Module) -> dict[str, object]:
    project = create_project(
        project_id=PROJECT_ID,
        workspace_id="ws-1",
        name="ticket service",
        goal="verify",
        created_at_commit="commit-0",
        modules=(module,),
    )
    return project_to_payload(project)


# ------------------------------------------------------------------ 值对象


def test_source_paths_default_to_empty() -> None:
    assert _module().source_paths == ()


def test_source_paths_must_not_repeat_an_entry() -> None:
    with pytest.raises(ValueError, match="must not repeat"):
        _module(source_paths=("src/a", "src/a"))


def test_blank_source_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="source_paths"):
        _module(source_paths=("  ",))


# ------------------------------------------------------------------ 序列化


def test_registered_source_paths_round_trip() -> None:
    module = _module(source_paths=("src/ticket", "tests/ticket"))
    payload = _payload(module)
    assert payload["modules"][0]["source_paths"] == ["src/ticket", "tests/ticket"]

    restored = project_from_payload(payload)
    assert restored.modules[0] == module
    assert restored.modules[0].source_paths == ("src/ticket", "tests/ticket")


def test_unregistered_source_paths_omit_the_key() -> None:
    """未登记时**省略该键**，不写空列表。

    留一个空列表，下游很容易把它读成"该模块影响所有文件"——那是伪造影响面。
    """
    payload = _payload(_module())
    assert "source_paths" not in payload["modules"][0]

    restored = project_from_payload(payload)
    assert restored.modules[0].source_paths == ()


def test_a_record_written_before_the_field_still_loads() -> None:
    """该字段是**可选**的：本字段引入前落盘的模块记录必须照常还原。"""
    legacy = {
        "schema_version": "1.0",
        "local_project_id": PROJECT_ID,
        "project_id": PROJECT_ID,
        "workspace_id": "ws-1",
        "name": "ticket service",
        "goal": "verify",
        "revision": 1,
        "created_at_commit": "commit-0",
        "modules": [
            {
                "module_id": "module-ticket",
                "project_id": PROJECT_ID,
                "name": "ticket core",
                "responsibility": "create and read tickets",
                "interface_note": "registered HTTP handlers",
                "inputs": [],
                "outputs": [],
                "implementation_status": "unimplemented",
                "revision": 1,
            }
        ],
    }
    restored = project_from_payload(legacy)
    assert restored.modules[0].source_paths == ()
