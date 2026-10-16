"""用例"断言依据已确认"的唯一权威核对（读已保存准备 + 受控确认链）。

架构 03 §13 要求 `effective_assertion_basis_state` 由**核心派生**，并显式冻结每份准确
Case/依据修订与**有效 ConfirmationRecord 集合**；`CORE-001` §3.1 进一步要求依据确认由
现有证明核对真实挑战/交互链，**不读冻结 `confirmed` 标签当权威**。

问题关闭（FR15）与报告生成（FR14）都需要这一项，因此只在这里实现一次：

1. 按运行意图读回**已保存准备**并校验其原始回执（`load_saved_preparation` +
   `validate_preparation_origin`）；
2. 读取准确冻结 Case，按已核对的运行修订链取当前有效依据，不以冻结标签判确认；
3. 汇集原准备与准确运行修订的确认引用，经 `SavedCaseBasisReader` 读回并交给
   `BasisConfirmationProof`（生产为受控审批服务）核对来源。

任何一步失败都返回 `False`（**不确认**），并保留缺口；不伪造、不用陈旧标签顶替。
"""

from __future__ import annotations

from typing import Any

from aitest.application.execution.frozen_plan import FrozenRunPlanReader
from aitest.application.execution.runtime_revision import SavedRuntimeRevisionReader
from aitest.application.planning.basis_proof import SavedCaseBasisReader
from aitest.application.planning.draft import text_digest
from aitest.application.planning.preparation import preparation_identity_digest
from aitest.application.planning.preparation_origin import (
    load_saved_preparation,
    validate_preparation_origin,
)
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.substrate import RecordReader
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.planning.plans import AssertionBasisState, effective_assertion_basis_state


class SavedCaseBasisCheck:
    """`(project_id, run_intent_id, case_id) -> bool` 的权威实现。"""

    def __init__(
        self,
        *,
        reader: RecordReader,
        records: object,
        workspace_id: str,
        basis_proof: object | None,
        override: Any | None = None,
    ) -> None:
        self._reader = reader
        self._records = records
        self._workspace_id = workspace_id
        self._bases = SavedCaseBasisReader(records, basis_proof)  # type: ignore[arg-type]
        self._override = override

    def __call__(self, project_id: str, run_intent_id: str, case_id: str) -> bool:
        if self._override is not None:
            return bool(self._override(project_id, run_intent_id, case_id))
        return self._confirmed(project_id, run_intent_id, case_id)

    def for_facts(self, facts: ExecutionFacts, case_id: str) -> bool:
        if self._override is not None:
            return bool(self._override(facts.project_id, facts.run.intent_id, case_id))
        return self._confirmed(facts.project_id, facts.run.intent_id, case_id, facts=facts)

    def _confirmed(
        self,
        project_id: str,
        run_intent_id: str,
        case_id: str,
        *,
        facts: ExecutionFacts | None = None,
    ) -> bool:
        try:
            case, candidates = self._material(project_id, run_intent_id, case_id, facts=facts)
            confirmations = []
            for identity, expected in candidates.items():
                actual = self._bases.read(project_id=project_id, confirmation_id=identity)
                actual_ref = {
                    name: getattr(actual, name)
                    for name in (
                        "confirmation_id",
                        "case_id",
                        "basis_revision",
                        "confirmed_at_commit",
                    )
                }
                if any(payload_digest(item) != payload_digest(actual_ref) for item in expected):
                    return False
                confirmations.append(actual)
            return (
                effective_assertion_basis_state(
                    case.assertion_basis,
                    confirmations,
                    case_id=case_id,
                )
                is AssertionBasisState.CONFIRMED
            )
        except (OSError, KeyError, TypeError, ValueError):
            return False

    def revision(
        self,
        project_id: str,
        run_intent_id: str,
        case_id: str,
        *,
        facts: ExecutionFacts | None = None,
    ) -> int | None:
        """准确冻结/有效运行修订里该用例的依据修订，断言结果不得猜测修订。

        D-02：断言结果的期望值来源是已发布的依据；没有依据修订就没有可引用的依据，
        此时不产出结果（调用方如实按未验证处理）。
        """

        if self._override is not None:
            getter = getattr(self._override, "revision", None)
            return getter(project_id, run_intent_id, case_id) if callable(getter) else None
        try:
            case, _ = self._material(project_id, run_intent_id, case_id, facts=facts)
        except (OSError, KeyError, TypeError, ValueError):
            return None
        revision = case.assertion_basis.revision
        return revision if type(revision) is int and revision >= 1 else None

    def text(self, facts: ExecutionFacts, case_id: str) -> str | None:
        if self._override is not None:
            getter = getattr(self._override, "text", None)
            return (
                getter(facts.project_id, facts.run.intent_id, case_id) if callable(getter) else None
            )
        try:
            case, _ = self._material(facts.project_id, facts.run.intent_id, case_id, facts=facts)
        except (OSError, KeyError, TypeError, ValueError):
            return None
        return str(case.assertion_basis.text)

    def _material(
        self,
        project_id: str,
        run_intent_id: str,
        case_id: str,
        *,
        facts: ExecutionFacts | None = None,
    ) -> tuple[Any, dict[str, list[dict[str, object]]]]:
        prepared = self._preparation(project_id, run_intent_id)
        frozen = FrozenRunPlanReader(self._records).read(prepared)  # type: ignore[arg-type]
        cases = frozen.cases
        candidates: dict[str, list[dict[str, object]]] = {}
        for entry in prepared.assertion_bases:
            if entry.case_id == case_id:
                for reference in entry.confirmation_refs:
                    candidates.setdefault(reference.confirmation_id, []).append(
                        reference.model_dump(mode="json"),
                    )
        if facts is not None:
            if (facts.project_id, facts.run.intent_id, facts.run.origin_workspace_id) != (
                project_id,
                run_intent_id,
                self._workspace_id,
            ):
                raise ValueError("effective basis runtime ownership differs")
            if facts.runtime_revision_refs:
                reader = SavedRuntimeRevisionReader(self._records)  # type: ignore[arg-type]
                cases = reader.read_effective_cases(
                    facts=facts,
                    plan=frozen.plan,
                    initial_cases=frozen.cases,
                )
                for reference in facts.runtime_revision_refs:
                    revision = reader.read_record(project_id=project_id, reference=reference)
                    if (revision.run_id, revision.origin_workspace_id) != (
                        facts.run_id,
                        self._workspace_id,
                    ):
                        raise ValueError("effective confirmation belongs to another runtime")
                    for identity in revision.confirmation_ids:
                        candidates.setdefault(identity, [])
        case = next((item for item in cases if item.case_id == case_id), None)
        if case is None:
            raise ValueError("effective assertion basis case is absent")
        if case.assertion_basis.state is AssertionBasisState.MISSING:
            raise ValueError("effective assertion basis is missing")
        if text_digest(case.assertion_basis.text) != case.assertion_basis.text_digest:
            raise ValueError("effective assertion basis text differs from its digest")
        return case, candidates

    def _preparation(self, project_id: str, run_intent_id: str) -> Any:
        receipt = self._reader.find_preparation_by_intent(intent_id=run_intent_id)
        if receipt is None or getattr(receipt, "cancelled", False):
            raise ValueError("original preparation is unavailable")
        request = receipt.request
        prepared_run_id = "prepared-" + preparation_identity_digest(
            project_id=project_id,
            client_id=request.client_id,
            prepare_request_id=request.prepare_request_id,
        )
        prepared, _ = load_saved_preparation(
            reader=self._reader,
            project_id=project_id,
            workspace_id=self._workspace_id,
            prepared_run_id=prepared_run_id,
        )
        validate_preparation_origin(prepared, reader=self._reader)
        return prepared

    def _entry(self, project_id: str, run_intent_id: str, case_id: str) -> Any | None:
        if self._override is not None:
            return None
        receipt = self._reader.find_preparation_by_intent(intent_id=run_intent_id)
        if receipt is None or getattr(receipt, "cancelled", False):
            return None
        request = getattr(receipt, "request", None)
        if request is None:
            return None
        prepared_run_id = "prepared-" + preparation_identity_digest(
            project_id=project_id,
            client_id=request.client_id,
            prepare_request_id=request.prepare_request_id,
        )
        try:
            prepared, _digest = load_saved_preparation(
                reader=self._reader,
                project_id=project_id,
                workspace_id=self._workspace_id,
                prepared_run_id=prepared_run_id,
            )
            validate_preparation_origin(prepared, reader=self._reader)
        except (OSError, KeyError, TypeError, ValueError):
            return None
        return next((item for item in prepared.assertion_bases if item.case_id == case_id), None)


__all__ = ["SavedCaseBasisCheck"]
