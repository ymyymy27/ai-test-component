"""跨包共用的运行词汇表。

`RunTier` 原先在 `domain/planning/plans.py` 与 `domain/execution/runs.py` 各定义
一次，值相同但成员顺序不同。两份枚举的成员不是同一个对象，`is` 比较会静默为
假（`Run.tier` 用执行侧、`DecisionFacts.tier` 用计划侧）。这里只保留一份。
"""

from __future__ import annotations

from enum import StrEnum


class RunTier(StrEnum):
    """档位：决定检查范围与结论上限（架构文档《01-项目与计划》第 4 节）。"""

    QUICK = "quick"
    ON_DEMAND = "on_demand"
    FULL = "full"


__all__ = ["RunTier"]
