"""用例"断言依据已确认"的唯一权威核对（读已保存准备 + 受控确认链）。

架构 03 §13 要求 `effective_assertion_basis_state` 由**核心派生**，并显式冻结每份准确
Case/依据修订与**有效 ConfirmationRecord 集合**；`CORE-001` §3.1 进一步要求依据确认由
现有证明核对真实挑战/交互链，**不读冻结 `confirmed` 标签当权威**。

问题关闭（FR15）与报告生成（FR14）都需要这一项，因此只在这里实现一次：

1. 按运行意图读回**已保存准备**并校验其原始回执（`load_saved_preparation` +
   `validate_preparation_origin`）；
2. 取出该用例的冻结依据条目，要求状态为 `confirmed` 且带确认引用；
3. 逐条经 `SavedCaseBasisReader` 按仓储修订读回 `case_link` 确认记录并交给
   `BasisConfirmationProof`（生产为受控审批服务）核对来源。

任何一步失败都返回 `False`（**不确认**），并保留缺口；不伪造、不用陈旧标签顶替。
"""

from __future__ import annotations

from typing import Any

from aitest.application.planning.basis_proof import SavedCaseBasisReader
from aitest.application.planning.preparation import preparation_identity_digest
from aitest.application.planning.preparation_origin import (
    load_saved_preparation,
    validate_preparation_origin,
)
from aitest.application.planning.substrate import RecordReader


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
        self._workspace_id = workspace_id
        self._bases = SavedCaseBasisReader(records, basis_proof)  # type: ignore[arg-type]
        self._override = override

    def __call__(self, project_id: str, run_intent_id: str, case_id: str) -> bool:
        if self._override is not None:
            return bool(self._override(project_id, run_intent_id, case_id))
        entry = self._entry(project_id, run_intent_id, case_id)
        if entry is None or entry.assertion_basis_state.value != "confirmed":
            return False
        if not entry.confirmation_refs:
            return False
        for reference in entry.confirmation_refs:
            try:
                self._bases.read(project_id=project_id, confirmation_id=reference.confirmation_id)
            except (OSError, KeyError, TypeError, ValueError):
                continue
            return True
        return False

    def revision(self, project_id: str, run_intent_id: str, case_id: str) -> int | None:
        """冻结准备里该用例的依据修订（断言结果必须引用**已发布**的依据修订）。

        D-02：断言结果的期望值来源是已发布的依据；没有依据修订就没有可引用的依据，
        此时不产出结果（调用方如实按未验证处理）。
        """

        entry = self._entry(project_id, run_intent_id, case_id)
        if entry is None:
            return None
        revision = entry.basis_revision
        return revision if type(revision) is int and revision >= 1 else None

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
        return next(
            (item for item in prepared.assertion_bases if item.case_id == case_id), None
        )


__all__ = ["SavedCaseBasisCheck"]
