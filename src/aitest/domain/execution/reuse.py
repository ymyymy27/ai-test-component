"""Legal whole-case reuse requires a complete, current basis, not inheritance.

Applications supply facts from exact material reads and current observations.
These types do not authenticate observations or grant execution/closure authority.
"""

import re
from dataclasses import dataclass, fields
from enum import StrEnum

from aitest.domain.json_material import require_json_text


def _text(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("reuse identity requires nonempty text")
    require_json_text(value)


@dataclass(frozen=True, slots=True)
class ReuseIdentity:
    project_id: str
    case_id: str
    source_content_identity: str | None
    environment_dynamic_digest: str | None
    check_scope_digest: str | None
    entry_input_digest: str | None
    rules_digest: str | None
    adapter_digest: str | None
    case_content_digest: str | None
    assertion_basis_digest: str | None
    dependency_digest: str | None

    def __post_init__(self) -> None:
        _text(self.project_id)
        _text(self.case_id)
        for field in fields(self)[2:]:
            value = getattr(self, field.name)
            if value is not None:
                _text(value)


class ReuseConditionKind(StrEnum):
    SOURCE_CURRENT = "source_current"
    SOURCE_VERIFIED = "source_verified"
    DEPENDENCIES_VALID = "dependencies_valid"
    EVIDENCE_READABLE = "evidence_readable"
    BASIS_CONFIRMED = "basis_confirmed"
    VERIFICATION_VALID = "verification_valid"


class ReuseConditionState(StrEnum):
    VERIFIED = "verified"
    INVALID = "invalid"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ReuseCondition:
    kind: ReuseConditionKind
    state: ReuseConditionState

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ReuseConditionKind) or not isinstance(
            self.state, ReuseConditionState
        ):
            raise ValueError("reuse conditions require actual known enums")


@dataclass(frozen=True, slots=True)
class WholeCaseReuseBasis:
    source_run_id: str
    source_snapshot_id: str
    source_snapshot_digest: str
    source_attempt_by_step: tuple[tuple[str, str], ...]
    source: ReuseIdentity
    target: ReuseIdentity
    conditions: tuple[ReuseCondition, ...]

    def __post_init__(self) -> None:
        _text(self.source_run_id)
        _text(self.source_snapshot_id)
        if not isinstance(self.source_snapshot_digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.source_snapshot_digest
        ):
            raise ValueError("reuse requires an exact source snapshot digest")
        if not isinstance(self.source, ReuseIdentity) or not isinstance(self.target, ReuseIdentity):
            raise ValueError("reuse requires typed source and target identities")
        if type(self.source_attempt_by_step) is not tuple:
            raise ValueError("reuse step mapping must be immutable")
        for pair in self.source_attempt_by_step:
            if type(pair) is not tuple or len(pair) != 2:
                raise ValueError("reuse step mapping requires exact pairs")
            for identity in pair:
                _text(identity)
        if (
            len({pair[0] for pair in self.source_attempt_by_step})
            != len(self.source_attempt_by_step)
            or len({pair[1] for pair in self.source_attempt_by_step})
            != len(self.source_attempt_by_step)
        ):
            raise ValueError("reuse Step and Attempt identities must be unique")
        if type(self.conditions) is not tuple or any(
            not isinstance(condition, ReuseCondition) for condition in self.conditions
        ):
            raise ValueError("reuse conditions must be immutable typed facts")
        if len({item.kind for item in self.conditions}) != len(self.conditions):
            raise ValueError("reuse conditions must have unique kinds")

    @property
    def denial_reasons(self) -> tuple[str, ...]:
        """Missing/unknown basis never becomes matching by None == None."""
        reasons = []
        for field in fields(ReuseIdentity):
            source = getattr(self.source, field.name)
            target = getattr(self.target, field.name)
            if source is None or target is None:
                reasons.append(field.name + "_unverified")
            elif source != target:
                reasons.append(field.name + "_changed")
        conditions = {item.kind: item.state for item in self.conditions}
        for kind in ReuseConditionKind:
            if conditions.get(kind) is not ReuseConditionState.VERIFIED:
                reasons.append(kind.value + "_unverified")
        if not self.source_attempt_by_step:
            reasons.append("source_attempts_missing")
        return tuple(reasons)
