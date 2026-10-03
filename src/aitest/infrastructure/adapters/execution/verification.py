"""同业务对象独立只读核验。

每一次核验都重新打开文件句柄（独立只读路径），流式重算 SHA256 与
期望值比对；不使用调用方的缓存句柄、内存副本或对象存储的自身读路径。
核验事实只陈述重新读取到的结果，不产生业务结论。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from aitest.application.ports import VerificationRequest
from aitest.contracts.verification import (
    VerificationFact,
    VerificationState,
)
from aitest.domain.evidence.evidence import (
    Verification,
    VerificationObservation,
)

_HASH_BLOCK = 64 * 1024


class IndependentFileVerifier:
    """VerificationPort 的本地只读实现。"""

    method = "fresh_file_read"

    def verify(self, path: Path, expected_sha256: str) -> VerificationFact:
        target = Path(path)
        if not target.exists():
            return VerificationFact(
                state=VerificationState.MISSING,
                method=self.method,
                sha256="",
                size=0,
                detail=f"对象不存在: {target}",
            )
        digest = hashlib.sha256()
        size = 0
        try:
            # 全新独立句柄：不接受任何已打开句柄或内存缓冲。
            with target.open("rb") as handle:
                while block := handle.read(_HASH_BLOCK):
                    digest.update(block)
                    size += len(block)
        except OSError as error:
            return VerificationFact(
                state=VerificationState.UNREADABLE,
                method=self.method,
                sha256="",
                size=0,
                detail=str(error),
            )
        observed = digest.hexdigest()
        if observed != expected_sha256:
            return VerificationFact(
                state=VerificationState.MISMATCH,
                method=self.method,
                sha256=observed,
                size=size,
                detail="重算摘要与期望不一致",
            )
        return VerificationFact(
            state=VerificationState.VERIFIED,
            method=self.method,
            sha256=observed,
            size=size,
        )


class ReadOnlyBusinessQueryPort(Protocol):
    def read_business_object(
        self,
        *,
        business_object_id: str,
        target_deployment_ref: str,
    ) -> Mapping[str, object] | None: ...


class BusinessVerificationAdapter:
    """Independently read the same business object and compare key facts."""

    method = "read_only_business_query"

    def __init__(self, query_port: ReadOnlyBusinessQueryPort) -> None:
        self._query_port = query_port

    def verify(
        self,
        request: VerificationRequest,
        *,
        expected_facts: Mapping[str, object] | None = None,
    ) -> Verification:
        expected = expected_facts if expected_facts is not None else request.expected_facts
        if not expected:
            return self._fact(
                request,
                VerificationObservation.NO_RESULT,
                gap_ids=("expected_facts_missing",),
            )
        try:
            observed = self._query_port.read_business_object(
                business_object_id=request.business_object_id,
                target_deployment_ref=request.target_deployment_ref,
            )
        except Exception:  # noqa: BLE001
            return self._fact(
                request,
                VerificationObservation.QUERY_ERROR,
                gap_ids=("independent_query_error",),
            )
        if observed is None:
            return self._fact(
                request,
                VerificationObservation.NO_RESULT,
                gap_ids=("business_object_not_found",),
            )
        mismatched = tuple(
            sorted(
                key
                for key, expected_value in expected.items()
                if observed.get(key) != expected_value
            )
        )
        return self._fact(
            request,
            (
                VerificationObservation.MISMATCHED
                if mismatched
                else VerificationObservation.MATCHED
            ),
            actual_result_ref=_mapping_digest(observed),
            gap_ids=tuple(f"business_fact_mismatch:{key}" for key in mismatched),
        )

    @staticmethod
    def _fact(
        request: VerificationRequest,
        observation: VerificationObservation,
        *,
        actual_result_ref: str | None = None,
        gap_ids: tuple[str, ...] = (),
    ) -> Verification:
        return Verification(
            verification_id=(
                f"verification:{request.verification_of}:{request.business_object_id}"
            ),
            verification_of=request.verification_of,
            business_object_id=request.business_object_id,
            query_method=request.query_method,
            observation=observation,
            query_interval=request.query_interval,
            deadline_condition=request.deadline_condition,
            target_deployment_ref=request.target_deployment_ref,
            actual_result_ref=actual_result_ref,
            evidence_refs=request.evidence_refs,
            gap_ids=gap_ids,
        )


def _mapping_digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "BusinessVerificationAdapter",
    "IndependentFileVerifier",
    "ReadOnlyBusinessQueryPort",
]
