"""Phase-one selection and scope guards; no I/O or framework dependencies.

运行词汇表（档位 / 驱动 / 结论上限）的语义唯一来源。
合同层在 `aitest.contracts.prepared_run` 中声明对应枚举，值集合由
`tests/contracts/test_run_vocabulary.py` 锁定一致。
"""

from dataclasses import dataclass
from enum import StrEnum


class RunTier(StrEnum):
    """档位：决定检查范围与结论上限（架构文档《01-项目与计划》第 4 节）。"""

    QUICK = "quick"
    ON_DEMAND = "on_demand"
    FULL = "full"


class RunDriver(StrEnum):
    """驱动：决定推进方式。运行中只允许 planned -> stepwise。"""

    PLANNED = "planned"
    STEPWISE = "stepwise"


class ConclusionCeiling(StrEnum):
    """结论上限：只由档位派生，不由覆盖率或驱动方式决定。"""

    PARTIAL = "partial"
    PASSABLE = "passable"


def conclusion_ceiling_for(tier: RunTier) -> ConclusionCeiling:
    """Return the only admissible conclusion ceiling for a run tier.

    `on_demand` 即使把冻结范围全部选上也仍为 `partial`；需要 `passable`
    时必须新建一次 `full` 运行。
    """
    if tier == RunTier.FULL:
        return ConclusionCeiling.PASSABLE
    if tier in (RunTier.QUICK, RunTier.ON_DEMAND):
        return ConclusionCeiling.PARTIAL
    raise ValueError(f"unmapped run tier: {tier}")


def narrow_driver(current: RunDriver, requested: RunDriver) -> RunDriver:
    if current == RunDriver.STEPWISE and requested == RunDriver.PLANNED:
        raise ValueError("driver cannot expand authorization")
    return requested


@dataclass(frozen=True, slots=True)
class AcceptanceScope:
    scope_id: str
    revision: int
    name: str
    required_case_ids: frozenset[str]
    template_case_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not self.scope_id.strip() or not self.name.strip() or self.revision < 1:
            raise ValueError("scope requires identity, name and published revision")
        if not self.template_case_ids <= self.required_case_ids:
            raise ValueError("template requirements must be included in required cases")

    def validate_selection(self, mode: RunTier, selected: frozenset[str]) -> None:
        if not selected:
            raise ValueError("empty selection is not not_applicable")
        if mode == RunTier.FULL and not self.required_case_ids <= selected:
            raise ValueError("full selection must include all required cases")
