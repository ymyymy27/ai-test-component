"""Derive the execution grant and per-case basis links from one plan confirmation.

设计文档 04：一次受控计划发布（``publish_plan`` challenge）同时证明计划、
执行许可范围和每条用例断言依据。计划记录先按既有受控写路径保存；本模块只做
**可重放的派生**：

1. 为每条用例追加 ``case_link``，引用计划记录上的 ``approval_confirmation_id``；
2. 最后创建 ``plan_execution_grant``，其范围摘要已在同一计划确认挑战中冻结。

派生记录都以稳定业务意图幂等：重传同一计划确认会读回原记录，不会重复写入。
许可最后创建，因此依据未全部落盘时不会出现“有许可、无依据”的可用状态。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from aitest.application.approval_service import _digest
from aitest.application.execution.plan_grant import (
    PlanExecutionGrantService,
    normalize_grant_scope,
)
from aitest.application.planning.basis_confirmation import BasisConfirmationService
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import plan_coverage_contract_from_payload
from aitest.application.planning.substrate import RecordReader, read_scoped_record


class PlanConfirmationError(ValueError):
    code = "PLAN_CONFIRMATION_INVALID"


def _stable_identity(prefix: str, *parts: object) -> str:
    return prefix + "-" + _digest(list(parts))[7:]


@dataclass(frozen=True, slots=True)
class PlanConfirmationService:
    reader: RecordReader
    basis: BasisConfirmationService
    grants: PlanExecutionGrantService

    def derive(
        self,
        *,
        project_id: str,
        request_id: str,
        intent_id: str,
        plan_id: str,
        plan_record_revision: int,
        cases: Sequence[Mapping[str, object]],
    ) -> Mapping[str, object]:
        """从已保存的受控计划发布派生许可与逐条依据；重放幂等。

        许可范围只从计划记录里冻结的 `approval_parameters.plan_grant` 读取，
        不从调用方参数再取一次；调用方无法在确认后改写许可。
        """

        if (
            not isinstance(project_id, str)
            or not project_id.strip()
            or not isinstance(request_id, str)
            or not request_id.strip()
            or not isinstance(intent_id, str)
            or not intent_id.strip()
            or not isinstance(plan_id, str)
            or not plan_id.strip()
            or type(plan_record_revision) is not int
            or plan_record_revision < 1
        ):
            raise PlanConfirmationError("plan confirmation needs exact project and plan identity")
        try:
            saved = read_scoped_record(
                self.reader,
                project_id=project_id,
                aggregate_kind="plan",
                record_id=plan_id,
                revision=plan_record_revision,
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise PlanConfirmationError("the exact confirmed plan is unavailable") from error
        raw = saved.payload
        confirmation_id = raw.get("approval_confirmation_id")
        commit = raw.get("approval_commit_seq")
        if (
            raw.get("project_id") != project_id
            or raw.get("plan_id") != plan_id
            or raw.get("approval_action") != "publish_plan"
            or not isinstance(confirmation_id, str)
            or not confirmation_id.strip()
            or not isinstance(commit, str)
            or not commit.strip()
        ):
            raise PlanConfirmationError(
                "plan confirmation differs from its controlled publication"
            )

        scope_id, scope_revision = raw.get("scope_id"), raw.get("scope_revision")
        if (
            not isinstance(scope_id, str)
            or not scope_id.strip()
            or type(scope_revision) is not int
            or scope_revision < 1
        ):
            raise PlanConfirmationError("the confirmed plan has no exact scope identity")
        parameters = raw.get("approval_parameters")
        frozen_grant = (
            parameters.get("plan_grant") if isinstance(parameters, Mapping) else None
        )
        if not isinstance(frozen_grant, Mapping):
            raise PlanConfirmationError("the confirmed plan has no frozen execution grant")
        try:
            saved_scope = read_scoped_record(
                self.reader,
                project_id=project_id,
                aggregate_kind="acceptance_scope",
                record_id=scope_id,
                revision=scope_revision,
            )
            contract = plan_coverage_contract_from_payload(raw)
            normalized = normalize_grant_scope(
                frozen_grant, require_understanding=contract is None
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise PlanConfirmationError("the confirmed plan scope or grant is malformed") from error
        scope_digest = payload_digest(dict(saved_scope.payload))
        understanding = (
            contract.understanding_revision if contract is not None
            else normalized["understanding_revision"]
        )
        if type(understanding) is not int or understanding < 1:
            raise PlanConfirmationError("the confirmed plan has no exact understanding revision")
        declared = normalized.get("understanding_revision")
        if contract is not None and declared is not None and declared != understanding:
            raise PlanConfirmationError(
                "grant understanding revision differs from the confirmed coverage contract"
            )

        frozen = raw.get("cases")
        if (
            not isinstance(frozen, list)
            or any(
                not isinstance(item, Mapping)
                or not isinstance(item.get("case_id"), str)
                or type(item.get("revision")) is not int
                for item in frozen
            )
        ):
            raise PlanConfirmationError("the confirmed plan has no readable case summaries")
        frozen_cases = {(item.get("case_id"), item.get("revision")) for item in frozen}
        provided_cases: set[tuple[object, object]] = set()
        material: list[tuple[str, int, int, str]] = []
        for item in cases:
            if not isinstance(item, Mapping):
                raise PlanConfirmationError("confirmed plan cases must be declared objects")
            case_id = item.get("case_id")
            case_revision = item.get("revision")
            basis = item.get("assertion_basis")
            basis_revision = basis.get("revision") if isinstance(basis, Mapping) else None
            basis_digest = basis.get("text_digest") if isinstance(basis, Mapping) else None
            if (
                not isinstance(case_id, str)
                or not case_id.strip()
                or type(case_revision) is not int
                or case_revision < 1
                or type(basis_revision) is not int
                or basis_revision < 1
                or not isinstance(basis_digest, str)
                or not basis_digest.strip()
            ):
                raise PlanConfirmationError(
                    "confirmed plan case has no exact assertion basis material"
                )
            provided_cases.add((case_id, case_revision))
            material.append((case_id, case_revision, basis_revision, basis_digest))
        if len(provided_cases) != len(material) or provided_cases != frozen_cases:
            raise PlanConfirmationError(
                "derived basis must cover exactly the confirmed plan's frozen cases"
            )

        basis_ids: list[str] = []
        basis_refs: list[dict[str, object]] = []
        for case_id, case_revision, basis_revision, basis_digest in material:
            result = self.basis.confirm_with_plan(
                request_id=_stable_identity("basis-request", request_id, case_id),
                intent_id=_stable_identity("basis-intent", project_id, confirmation_id, case_id),
                project_id=project_id,
                case_id=case_id,
                case_revision=case_revision,
                basis_revision=basis_revision,
                basis_text_digest=basis_digest,
                plan_confirmation_id=confirmation_id,
                plan_id=plan_id,
                plan_revision=plan_record_revision,
            )
            basis_ids.append(str(result["confirmation_id"]))
            basis_refs.append(
                {
                    key: result[key]
                    for key in (
                        "confirmation_id",
                        "case_id",
                        "basis_revision",
                        "confirmed_at_commit",
                    )
                }
            )

        grant = self.grants.create(
            project_id=project_id,
            intent_id=_stable_identity("grant-intent", project_id, intent_id),
            request_id=_stable_identity("grant-request", request_id, intent_id),
            parameters=dict(normalized)
            | {
                "plan_id": plan_id,
                "plan_revision": plan_record_revision,
                "scope_digest": scope_digest,
                "understanding_revision": understanding,
            },
        )
        return {
            "plan_confirmation_id": confirmation_id,
            "approval_commit_seq": commit,
            "plan_record_revision": plan_record_revision,
            "plan_digest": payload_digest(dict(raw)),
            "scope_digest": scope_digest,
            "grant_id": grant["grant_id"],
            "grant_scope_digest": grant["grant_scope_digest"],
            "basis_confirmation_ids": basis_ids,
            "basis_confirmations": basis_refs,
            "basis_digests": [
                {
                    "case_id": case_id,
                    "case_revision": case_revision,
                    "basis_revision": basis_revision,
                    "basis_text_digest": basis_digest,
                }
                for case_id, case_revision, basis_revision, basis_digest in material
            ],
        }


__all__ = ["PlanConfirmationError", "PlanConfirmationService"]
