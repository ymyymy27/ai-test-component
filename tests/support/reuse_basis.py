"""Synthetic domain guard inputs, never actual environment or source attestation."""

from aitest.domain.execution.reuse import (
    ReuseCondition,
    ReuseConditionKind,
    ReuseConditionState,
    ReuseIdentity,
    WholeCaseReuseBasis,
)


def verified_reuse_basis(case="case", mapping=(("s1", "attempt-s1"),)):
    identity = ReuseIdentity(
        "project", case, "source", "dynamic", "scope", "input", "rules", "adapters",
        "case-content", "basis", "consumed-dependencies",
    )
    return WholeCaseReuseBasis(
        "source-run", "source-snapshot", "sha256:" + "a" * 64, mapping,
        identity, identity,
        tuple(ReuseCondition(kind, ReuseConditionState.VERIFIED) for kind in ReuseConditionKind),
    )
