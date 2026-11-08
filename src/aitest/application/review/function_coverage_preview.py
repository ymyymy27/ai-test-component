"""计划覆盖预览：把冻结的覆盖契约投影成确认页可读的 `FunctionCoverage`。

合同来源：设计文档 03 第 4.4—4.6、5.5、6.1、8.2—8.3 节（确认页的覆盖收据
是计划冻结 `FunctionCoverage` 的预览，不是报告），以及架构文档《03-报告与
缺陷》第 2 节（`FunctionCoverage` 字段）。

本模块只做投影，不判断执行证据、不计算证据等级，也不写任何记录：

- 每个“功能 × 必要维度”都必须出现在结果里，顺序与契约一致；
- 有 claim 但尚未执行时只能得到 ``planned``——它表示“计划已承诺”，不是
  ``proven``，也不携带证据或核验引用；
- 没有 claim 是计划缺口，不能靠其他维度的 claim 补齐；
- ``unsupported`` / ``not_proven`` 的 claim 优先于 ``planned``，避免预览把
  已知能力缺口或未承诺维度显示成“将验证”。
"""

from __future__ import annotations

from datetime import UTC, datetime

from aitest.domain.planning.plans import (
    CoverageContract,
    validate_coverage_contract,
)
from aitest.domain.planning.understanding import CoverageClaim, CoverageClaimStatus
from aitest.domain.review.function_coverage import CoverageStatus, FunctionCoverage

#: 函数内任一必要维度没有 claim 时的缺口原因；确认页按此原文展示。
NO_CLAIM_GAP_REASON = "no coverage claim"


def build_plan_function_coverage(
    contract: CoverageContract, *, plan_revision: int
) -> tuple[FunctionCoverage, ...]:
    """按覆盖契约生成确认页用的计划覆盖投影。

    逐条规则（设计文档 03 第 4.5—4.6、6.1 节）：

    1. 先校验契约结构与 claim 归属：claim 的 ``function_id`` 必须存在，且
       ``dimension`` 必须属于该功能的 ``required_dimensions``；否则无法把
       claim 计入任何必要维度，直接拒绝而不是静默忽略；
    2. 对每个 ``FunctionRequirement`` 的每个 ``required_dimensions`` 项：

       | 匹配 claim | 维度状态 |
       | --- | --- |
       | 没有 | ``not_proven``（函数级 ``gap_reason = "no coverage claim"``） |
       | 任一 ``unsupported`` | ``unsupported`` |
       | 任一 ``not_proven`` | ``not_proven`` |
       | 其余（``proven``/``partial``） | ``planned`` |

    3. 每个功能生成一条 `FunctionCoverage`：``plan_revision`` 为入参，
       ``run_id`` 为空；``case_ids`` 取该功能全部匹配 claim 的用例并集；
       ``evidence_refs`` / ``verification_refs`` 为空——计划阶段还没有证据；
    4. 结果顺序保持 ``contract.function_requirements`` 顺序；没有功能时返回
       空元组（契约合法但尚无功能承诺，不能凭空造出一条“已覆盖”）。

    ``unsupported`` 先于 ``not_proven``：同一维度多个 claim 时，已登记的
    能力缺口是确定限制，不能被另一个未证明的 claim 降级成普通缺口。
    """

    if plan_revision < 1:
        raise ValueError("plan_revision must be >= 1")
    validate_coverage_contract(contract)

    claims_by_dimension: dict[tuple[str, str], list[CoverageClaim]] = {}
    for claim in contract.coverage_claims:
        claims_by_dimension.setdefault(
            (claim.function_id, claim.dimension), []
        ).append(claim)

    projected_at = datetime.now(UTC)
    projections: list[FunctionCoverage] = []
    for requirement in contract.function_requirements:
        dimensions: dict[str, CoverageStatus] = {}
        case_ids: set[str] = set()
        missing_claim = False
        for dimension in requirement.required_dimensions:
            claims = claims_by_dimension.get(
                (requirement.function_id, dimension), []
            )
            if not claims:
                dimensions[dimension] = CoverageStatus.NOT_PROVEN
                missing_claim = True
                continue
            for claim in claims:
                case_ids.update(claim.case_ids)
            statuses = {claim.status for claim in claims}
            if CoverageClaimStatus.UNSUPPORTED in statuses:
                dimensions[dimension] = CoverageStatus.UNSUPPORTED
            elif CoverageClaimStatus.NOT_PROVEN in statuses:
                dimensions[dimension] = CoverageStatus.NOT_PROVEN
            else:
                dimensions[dimension] = CoverageStatus.PLANNED
        projections.append(
            FunctionCoverage(
                coverage_id=(
                    f"plan-coverage:{plan_revision}:{requirement.function_id}"
                ),
                function_id=requirement.function_id,
                plan_revision=plan_revision,
                run_id=None,
                coverage_by_dimension=dimensions,
                updated_at=projected_at,
                case_ids=frozenset(case_ids),
                gap_reason=NO_CLAIM_GAP_REASON if missing_claim else None,
            )
        )
    return tuple(projections)
