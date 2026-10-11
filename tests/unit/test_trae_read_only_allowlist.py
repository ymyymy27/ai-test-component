"""编辑器入口的只读白名单必须与合同 `READ_ACTIONS` 一致。

`integrations/trae/src/core-bridge.ts` 的 `READ_ONLY_ACTIONS` 是**手工复制**的合同值：
它决定面板能派发什么，是安全边界。手抄值会漂移——本会话已经修过一次同类缺陷
（MCP relay 的 `_QUERY_FIELDS` 与命令层支持面脱节）。

漂移的两个方向都危险：
- 合同新增只读动作而这里没跟上 → 入口静默缺能力；
- 这里多写了一个写动作 → 界面点击可能触发未授权写路径。

因此本测试要求二者**完全相等**，而不是"包含即可"。
"""

from __future__ import annotations

import pathlib
import re

from aitest.contracts.commands import READ_ACTIONS

BRIDGE = pathlib.Path("integrations/trae/src/core-bridge.ts")


def _typescript_allowlist() -> frozenset[str]:
    text = BRIDGE.read_text(encoding="utf-8")
    match = re.search(
        r"READ_ONLY_ACTIONS[^=]*=\s*new Set\(\[(.*?)\]\)",
        text,
        re.DOTALL,
    )
    assert match is not None, "core-bridge.ts 里找不到 READ_ONLY_ACTIONS 的字面声明"
    return frozenset(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_bridge_read_only_allowlist_matches_the_contract() -> None:
    allowlist = _typescript_allowlist()

    assert allowlist, "只读白名单不应为空——空集会让面板什么都派发不了"
    assert allowlist == frozenset(READ_ACTIONS), (
        "编辑器入口的只读白名单与 contracts.commands.READ_ACTIONS 不一致；"
        f"仅存在于 TS：{sorted(allowlist - READ_ACTIONS)}；"
        f"仅存在于合同：{sorted(frozenset(READ_ACTIONS) - allowlist)}"
    )


def test_bridge_read_only_allowlist_excludes_every_write_action() -> None:
    """再单独锁一遍"不得含写动作"：即使将来有人放宽上面那条相等断言。"""

    from aitest.contracts.commands import PHASE_ONE_ACTIONS

    writes = frozenset(PHASE_ONE_ACTIONS) - frozenset(READ_ACTIONS)
    assert not (_typescript_allowlist() & writes)
