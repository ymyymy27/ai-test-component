"""C-01/C-05：复用引用记录的合同形状、严格校验与"不授予资格"边界。

合同见[架构01 复用、回归与模型任务矩阵](../../docs/项目文档/一期/架构文档/01-项目与计划.md)：
ReuseReference 登记 target_run/case_id、source_run/step/attempt、来源计划与源码/环境/
入口参数/规则/适配器修订、证据引用、validity_checked_at_commit 及验证依据；它是已有
运行记录的引用关系。本文件只测记录形状与校验，并证明记录本身不产生 R。
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from aitest.contracts.reuse_reference import (
    SCHEMA_VERSION,
    ReuseReference,
    parse_reuse_reference,
    reuse_reference_digest,
    reuse_reference_payload,
)
from aitest.domain.execution.reuse import (
    ReuseCondition,
    ReuseConditionKind,
    ReuseConditionState,
    ReuseIdentity,
    WholeCaseReuseBasis,
)

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64


def reference(**overrides: object) -> ReuseReference:
    values: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "target_run_id": "run-target",
        "case_id": "case-1",
        "source_run_id": "run-source",
        "source_step_id": "step-1",
        "source_attempt_id": "attempt-1",
        "source_plan_revision_digest": _DIGEST_A,
        "source_content_identity": "sha256:" + "c" * 64,
        "environment_revision_digest": _DIGEST_B,
        "entry_input_digest": "sha256:" + "d" * 64,
        "rules_revision_digest": "sha256:" + "e" * 64,
        "adapter_digest": "sha256:" + "f" * 64,
        "evidence_refs": ("evidence:attempt-1:stdout:0",),
        "validity_checked_at_commit": "42",
        "verification_basis": ("business-query-1",),
    }
    values.update(overrides)
    return ReuseReference(**values)  # type: ignore[arg-type]


def _payload() -> dict[str, object]:
    return deepcopy(reference().model_dump(mode="json"))


def test_contract_shape_round_trips_through_canonical_bytes() -> None:
    original = reference()
    parsed = parse_reuse_reference(_payload())
    assert parsed == original
    assert reuse_reference_payload(parsed) == reuse_reference_payload(original)
    assert reuse_reference_digest(parsed) == reuse_reference_digest(original)
    assert reuse_reference_payload(original).startswith(b'{"adapter_digest"')


def test_unknown_or_missing_fields_are_rejected() -> None:
    extra = _payload() | {"grants_reuse": True}
    with pytest.raises(ValidationError):
        parse_reuse_reference(extra)
    missing = _payload()
    del missing["source_attempt_id"]
    with pytest.raises(ValidationError):
        parse_reuse_reference(missing)


@pytest.mark.parametrize(
    "field",
    [
        "source_plan_revision_digest",
        "environment_revision_digest",
        "entry_input_digest",
        "rules_revision_digest",
    ],
)
@pytest.mark.parametrize("value", ["", "sha256:xyz", "sha256:" + "A" * 64, 42, None])
def test_revision_digests_must_be_exact_sha256(field: str, value: object) -> None:
    payload = _payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        parse_reuse_reference(payload)


def test_adapter_digest_is_optional_but_never_approximate() -> None:
    assert reference(adapter_digest=None).adapter_digest is None
    with pytest.raises(ValidationError):
        reference(adapter_digest="sha256:short")


@pytest.mark.parametrize("commit", ["0", "01", "abc", "1.0", True, 1, "", "-1"])
def test_validity_commit_must_be_a_canonical_positive_sequence(commit: object) -> None:
    with pytest.raises(ValidationError):
        reference(validity_checked_at_commit=commit)


@pytest.mark.parametrize(
    "overrides",
    [
        {"evidence_refs": ()},
        {"evidence_refs": ("evidence:1", "evidence:1")},
        {"evidence_refs": ("",)},
        {"verification_basis": ("same", "same")},
        {"verification_basis": (" ",)},
    ],
)
def test_reference_identities_must_be_nonempty_and_unique(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        reference(**overrides)


@pytest.mark.parametrize("field", ["target_run_id", "case_id", "source_step_id"])
def test_identities_are_strict_nonempty_text(field: str) -> None:
    for value in ("", 1, None, "  "):
        with pytest.raises(ValidationError):
            reference(**{field: value})


def test_payload_must_be_a_mapping() -> None:
    with pytest.raises(ValueError):
        parse_reuse_reference(["not", "a", "mapping"])  # type: ignore[arg-type]


def _identity() -> ReuseIdentity:
    return ReuseIdentity(
        project_id="project-1",
        case_id="case-1",
        source_content_identity="sha256:" + "c" * 64,
        environment_dynamic_digest=_DIGEST_B,
        check_scope_digest="sha256:" + "1" * 64,
        entry_input_digest="sha256:" + "d" * 64,
        rules_digest="sha256:" + "e" * 64,
        adapter_digest="sha256:" + "f" * 64,
        case_content_digest="sha256:" + "2" * 64,
        assertion_basis_digest="sha256:" + "3" * 64,
        dependency_digest="sha256:" + "4" * 64,
    )


def test_saved_reference_alone_never_grants_reuse() -> None:
    """记录再完整，R 仍由纯领域在全部条件 VERIFIED 时派生；未核实条件保持拒绝。"""
    conditions = tuple(
        ReuseCondition(
            kind,
            ReuseConditionState.UNKNOWN
            if kind is ReuseConditionKind.VERIFICATION_VALID
            else ReuseConditionState.VERIFIED,
        )
        for kind in ReuseConditionKind
    )
    basis = WholeCaseReuseBasis(
        source_run_id="run-source",
        source_snapshot_id="step-revision-1",
        source_snapshot_digest="sha256:" + "9" * 64,
        source_attempt_by_step=(("step-1", "attempt-1"),),
        source=_identity(),
        target=_identity(),
        conditions=conditions,
    )
    record = reference()
    assert record.case_id == basis.target.case_id
    assert record.source_attempt_id == "attempt-1"
    assert "verification_valid_unverified" in basis.denial_reasons
    assert "grants_reuse" not in ReuseReference.model_fields
