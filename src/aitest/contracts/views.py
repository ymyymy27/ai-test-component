"""DTOs serialize core facts; views must not calculate business outcomes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .responses import Response as Response

__all__ = [
    "CoverageDTO",
    "CoverageSummaryDTO",
    "DecisiveFailureRef",
    "Response",
]


class DecisiveFailureRef(BaseModel):
    """决定性失败的最小引用；细节按引用读取，不在列表里展开。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    step_id: str
    attempt_id: str


class CoverageSummaryDTO(BaseModel):
    """同一份覆盖事实的 selected / required 双摘要（架构 06 §5）。"""

    model_config = ConfigDict(extra="forbid")

    scope_kind: Literal["selected", "required"]
    denominator: int = Field(ge=0)
    executed_count: int = Field(ge=0)
    reused_count: int = Field(ge=0)
    verified_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    unverified_count: int = Field(ge=0)
    #: 缺失来源时保留 None 并说明原因，不补零。
    case_ids_revision: str | None = None
    source_commit: str | None = None
    policy_version: str | None = None


class CoverageDTO(BaseModel):
    """覆盖计数：selected/required 双摘要 + 决定性失败 + 来源修订（架构 03 §11）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["aitest.coverage/1.0"] = "aitest.coverage/1.0"
    selected_summary: CoverageSummaryDTO
    required_summary: CoverageSummaryDTO
    #: 决定性失败按 (case_id, step_id, attempt_id) 去重；来源缺失时为 None。
    decisive_failure_count: int | None = None
    decisive_failures: tuple[DecisiveFailureRef, ...] = ()
    case_ids_revision: str | None = None
    source_commit: str | None = None
    policy_version: str | None = None
    unknown_reason: str | None = None
