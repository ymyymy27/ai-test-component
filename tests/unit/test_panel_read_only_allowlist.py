"""共享面板只能派发只读动作（一期 P1-AC26/27 的入口边界）。

面板是**展示与只读查询**入口：写动作必须经受控用户入口并携带授权，界面点击不等于
用户确认（`docs/项目文档/一期/设计文档/01-面板与工作台.md`、`05-Trae接入.md`）。
本文件把该边界锁在源码上：任何写动作名出现在面板派发路径都视为回归。

与 `test_trae_read_only_allowlist.py` 同一口径：那份锁 Trae 桥的白名单，这份锁面板的
实际派发调用与动作表。
"""

from __future__ import annotations

import pathlib
import re

from aitest.contracts.commands import PHASE_ONE_ACTIONS, READ_ACTIONS

PANEL = pathlib.Path("src/aitest/resources/panel/src/index.ts")

#: 面板自有的 UI 名字（不是协议动作）：宿主职责或纯界面动作。
_UI_ONLY = frozenset({"ensure-core", "refresh-core", "back"})

_DISPATCH_LITERAL = re.compile(r'this\.dispatch(?:Scoped)?\(\s*"([^"]+)"')
_READ_ONLY_TABLE = re.compile(r"const entries[^;]*?=\s*\[(.*?)\];", re.DOTALL)
_TABLE_ACTION = re.compile(r'\[\s*"[^"]*"\s*,\s*"([^"]+)"\s*,')


def _panel_source() -> str:
    assert PANEL.is_file(), f"面板源码缺失：{PANEL}"
    return PANEL.read_text(encoding="utf-8")


def _entries_table_actions(source: str) -> set[str]:
    """只读入口表里的动作名；不扫描其它界面表（详情/阶段标题不是协议动作）。"""

    match = _READ_ONLY_TABLE.search(source)
    assert match is not None, "面板应有一个只读入口表（const entries）"
    return set(_TABLE_ACTION.findall(match.group(1)))


def test_panel_dispatches_only_read_actions() -> None:
    source = _panel_source()
    dispatched = set(_DISPATCH_LITERAL.findall(source))
    dispatched |= _entries_table_actions(source)
    assert dispatched, "面板应至少声明一个只读派发入口"

    writes = frozenset(PHASE_ONE_ACTIONS) - frozenset(READ_ACTIONS)
    illegal = sorted(dispatched & writes)
    assert not illegal, f"面板不得派发写动作：{illegal}"

    unknown = sorted(dispatched - frozenset(READ_ACTIONS) - _UI_ONLY)
    assert not unknown, (
        "面板派发的动作必须是合同 READ_ACTIONS 成员或面板自有 UI 名字；"
        f"未登记：{unknown}"
    )


def test_panel_source_never_names_a_write_action() -> None:
    """再单独锁一遍：写动作名不得出现在面板源码里（含注释与提示文案）。"""

    source = _panel_source()
    writes = frozenset(PHASE_ONE_ACTIONS) - frozenset(READ_ACTIONS)
    present = sorted(name for name in writes if name in source)
    assert not present, f"面板源码出现写动作名：{present}"


def test_panel_scopes_project_queries_to_the_host_context() -> None:
    """项目作用域查询只能带宿主声明的项目身份：源码里必须有该守卫。"""

    source = _panel_source()
    assert "PROJECT_NOT_SELECTED" in source, "缺少未选择项目时的显式拒绝"
    assert "projectId" in source, "派发消息必须携带宿主声明的项目身份"
    assert "projectContext" in source, "项目上下文必须来自宿主消息"
