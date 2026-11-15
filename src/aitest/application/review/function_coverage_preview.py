"""计划覆盖预览：把冻结的覆盖契约投影成确认页可读的 `FunctionCoverage`。

计划阶段只承诺“将会验证”，因此维度状态只可能是 ``planned`` /
``unsupported`` / ``not_proven``；没有 claim 的函数级缺口统一为
``NO_CLAIM_GAP_REASON``。具体遍历与用例合并由
`domain.review.function_coverage.project_coverage` 完成。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from aitest.domain.planning.plans import (
    CoverageContract,
    FunctionRequirement,
    validate_coverage_contract,
)
from aitest.domain.planning.understanding import CoverageClaim, CoverageClaimStatus
from aitest.domain.review.function_coverage import (
    NO_CLAIM_GAP_REASON,
    CoverageStatus,
    FunctionCoverage,
    project_coverage,
)

__all__ = ["NO_CLAIM_GAP_REASON", "build_plan_function_coverage"]


def _resolve_dimension(
    _requirement: FunctionRequirement,
    _dimension: str,
    claims: Sequence[CoverageClaim],
    _case_ids: frozenset[str],
) -> tuple[CoverageStatus, tuple[str, ...]]:
    if not claims:
        return CoverageStatus.NOT_PROVEN, (NO_CLAIM_GAP_REASON,)
    statuses = {claim.status for claim in claims}
    if CoverageClaimStatus.UNSUPPORTED in statuses:
        return CoverageStatus.UNSUPPORTED, ()
    if CoverageClaimStatus.NOT_PROVEN in statuses:
        return CoverageStatus.NOT_PROVEN, ()
    return CoverageStatus.PLANNED, ()


def build_plan_function_coverage(
    contract: CoverageContract,
    *,
    plan_revision: int,
    projected_at: datetime | None = None,
) -> tuple[FunctionCoverage, ...]:
    """按覆盖契约生成确认页用的计划覆盖投影。"""

    if plan_revision < 1:
        raise ValueError("plan_revision must be >= 1")
    validate_coverage_contract(contract)
    moment = projected_at if projected_at is not None else datetime.now(UTC)
    return project_coverage(
        contract,
        projected_at=moment,
        plan_revision=plan_revision,
        run_id=None,
        coverage_id=lambda function_id: f"plan-coverage:{plan_revision}:{function_id}",
        resolve_dimension=_resolve_dimension,
    )
