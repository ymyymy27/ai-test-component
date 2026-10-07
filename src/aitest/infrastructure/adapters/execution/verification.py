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

from aitest.application.ports import (
    CapturedBusinessVerification,
    ReadOnlyBusinessQueryPort,
    VerificationRequest,
)
from aitest.contracts.verification import (
    VerificationFact,
    VerificationState,
)
from aitest.domain.evidence.evidence import (
    Verification,
    VerificationObservation,
)
from aitest.domain.evidence.verification import compare_business_fields
from aitest.domain.execution.assertions import (
    freeze_json_value,
    json_equal,
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
        return self.capture(request, expected_facts=expected_facts).verification

    def capture(
        self,
        request: VerificationRequest,
        *,
        expected_facts: Mapping[str, object] | None = None,
    ) -> CapturedBusinessVerification:
        try:
            expected = freeze_json_value(request.expected_facts)
            override = freeze_json_value(expected_facts) if expected_facts is not None else expected
            if not isinstance(expected, dict):
                raise ValueError("business expected facts require an object")
        except (TypeError, ValueError, RecursionError, RuntimeError):
            return CapturedBusinessVerification(
                self._fact(
                    request, VerificationObservation.NO_RESULT, gap_ids=("expected_facts_invalid",)
                )
            )
        if not json_equal(override, expected):
            return CapturedBusinessVerification(
                self._fact(
                    request, VerificationObservation.NO_RESULT, gap_ids=("expected_facts_conflict",)
                )
            )
        if not expected:
            return CapturedBusinessVerification(
                self._fact(
                    request,
                    VerificationObservation.NO_RESULT,
                    gap_ids=("expected_facts_missing",),
                )
            )
        try:
            observed = self._query_port.read_business_object(
                business_object_id=request.business_object_id,
                target_deployment_ref=request.target_deployment_ref,
            )
        except Exception:  # noqa: BLE001
            return CapturedBusinessVerification(
                self._fact(
                    request,
                    VerificationObservation.QUERY_ERROR,
                    gap_ids=("independent_query_error",),
                )
            )
        if observed is None:
            return CapturedBusinessVerification(
                self._fact(
                    request,
                    VerificationObservation.NO_RESULT,
                    gap_ids=("business_object_not_found",),
                )
            )
        try:
            if not isinstance(observed, Mapping) or any(
                not isinstance(key, str) for key in observed
            ):
                raise ValueError("business observation requires JSON fields")
            # Freeze the same exact JSON material for comparison and its evidence digest.
            frozen_observed = freeze_json_value(observed)
            if not isinstance(frozen_observed, dict):
                raise ValueError("business observation requires an object")
            actual_ref = _mapping_digest(frozen_observed)
        except (TypeError, ValueError, RecursionError, RuntimeError):
            return CapturedBusinessVerification(
                self._fact(
                    request,
                    VerificationObservation.QUERY_ERROR,
                    gap_ids=("independent_query_material_invalid",),
                )
            )
        observation, gaps = compare_business_fields(frozen_observed, expected)
        return CapturedBusinessVerification(
            self._fact(
                request,
                observation,
                actual_result_ref=actual_ref,
                gap_ids=gaps,
            ),
            frozen_observed,
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
    encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "BusinessVerificationAdapter",
    "IndependentFileVerifier",
    "ReadOnlyBusinessQueryPort",
]
