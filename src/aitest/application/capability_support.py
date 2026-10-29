"""Capability support marks (P1 capability/template list §3).

A capability is only marked ``supported`` when three independent facts exist:
a registered adapter, at least one template that exercises the capability, and a
verified AC evidence row. Missing template or AC evidence yields ``unverified``;
a missing adapter yields ``unsupported``. The mark never upgrades itself from a
label alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CapabilityStatus = Literal["supported", "unverified", "unsupported"]


@dataclass(frozen=True, slots=True)
class CapabilitySupport:
    capability: str
    adapter_version: str | None
    template_ids: tuple[str, ...]
    ac_evidence_verified: bool
    status: CapabilityStatus
    reason: str

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("capability must not be empty")
        if not self.reason.strip():
            raise ValueError("reason must not be empty")


def evaluate_capability_support(
    *,
    capability: str,
    adapter_version: str | None,
    template_ids: tuple[str, ...] = (),
    ac_evidence_verified: bool = False,
) -> CapabilitySupport:
    """Derive the support mark from adapter, template and AC evidence facts."""

    templates = tuple(sorted(set(template_ids)))
    if adapter_version is None:
        return CapabilitySupport(
            capability=capability,
            adapter_version=None,
            template_ids=templates,
            ac_evidence_verified=ac_evidence_verified,
            status="unsupported",
            reason="no registered adapter for this capability",
        )
    if not templates:
        return CapabilitySupport(
            capability=capability,
            adapter_version=adapter_version,
            template_ids=(),
            ac_evidence_verified=ac_evidence_verified,
            status="unverified",
            reason="no template exercises this capability yet",
        )
    if not ac_evidence_verified:
        return CapabilitySupport(
            capability=capability,
            adapter_version=adapter_version,
            template_ids=templates,
            ac_evidence_verified=False,
            status="unverified",
            reason="no verified AC evidence for this capability",
        )
    return CapabilitySupport(
        capability=capability,
        adapter_version=adapter_version,
        template_ids=templates,
        ac_evidence_verified=True,
        status="supported",
        reason="adapter, template and verified AC evidence are all present",
    )


__all__ = ["CapabilityStatus", "CapabilitySupport", "evaluate_capability_support"]
