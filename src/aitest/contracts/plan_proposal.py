"""Structured AI plan proposal (`aitest.plan-proposal/1.0` and `1.1`).

The model only fills a template skeleton; it never supplies server-owned
identity, revisions, digests, environment links or publication state.  This
module is intentionally a *contract* (pydantic only): parsing, template
coverage and materialisation live in `application/planning/proposal.py`.

`1.1` adds the project-understanding link (`understanding_revision`), the
selected goal intents and the model-proposed coverage claims.  The server still
owns `claim_id`, `status`, `plan_revision`, environment links and every digest;
a `1.0` proposal must not carry any `1.1` field (half-new payloads rejected).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PLAN_PROPOSAL_SCHEMA_V10: Final = "aitest.plan-proposal/1.0"
PLAN_PROPOSAL_SCHEMA_V11: Final = "aitest.plan-proposal/1.1"

#: Fields introduced by schema 1.1; their mere presence in a 1.0 payload is a
#: version error even when the value equals the 1.0 default.
_V11_FIELDS = frozenset({"understanding_revision", "goal_intent_ids", "coverage_claims"})


def _require_nonblank(values: list[str], name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} must not contain blank values")


class ProposalCaseLink(BaseModel):
    """Links proposed by the model; the server injects the environment."""

    model_config = ConfigDict(extra="forbid")

    acceptance_item_ids: list[str] = Field(min_length=1)
    module_ids: list[str] = Field(default_factory=list)
    critical_path_ids: list[str] = Field(default_factory=list)
    no_critical_path_reason: str | None = None

    @model_validator(mode="after")
    def references(self) -> ProposalCaseLink:
        _require_nonblank(self.acceptance_item_ids, "acceptance_item_ids")
        _require_nonblank(self.module_ids, "module_ids")
        _require_nonblank(self.critical_path_ids, "critical_path_ids")
        if self.critical_path_ids and self.no_critical_path_reason is not None:
            raise ValueError(
                "a reason for empty critical paths must not accompany a non-empty set"
            )
        if not self.critical_path_ids:
            reason = self.no_critical_path_reason
            if reason is None or not reason.strip():
                raise ValueError(
                    "empty critical paths require an explicit applicability reason"
                )
        return self


class ProposalCase(BaseModel):
    """One case drafted from one template item."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=200)
    template_item_id: str = Field(min_length=1, max_length=200)
    layer: Literal["L1", "L2", "L3"]
    objective: str = Field(min_length=1, max_length=4000)
    preconditions: list[str] = Field(min_length=1)
    inputs: list[str] = Field(default_factory=list)
    steps: list[str] = Field(min_length=1)
    expected: str = Field(min_length=1, max_length=8000)
    verification_method: str = Field(min_length=1, max_length=4000)
    links: ProposalCaseLink
    assertion_basis_text: str = Field(min_length=1, max_length=8000)
    independent_verification: str = Field(min_length=1, max_length=4000)
    mock_scope: list[str] = Field(default_factory=list)
    importance: Literal["P0", "P1", "P2"] = "P1"

    @model_validator(mode="after")
    def nonblank_lists(self) -> ProposalCase:
        _require_nonblank(self.preconditions, "preconditions")
        _require_nonblank(self.inputs, "inputs")
        _require_nonblank(self.steps, "steps")
        _require_nonblank(self.mock_scope, "mock_scope")
        return self


class ProposalScope(BaseModel):
    """Acceptance scope content the model may propose."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=8000)
    #: Empty means "derive from the cases"; a concrete list must cover them.
    required_case_ids: list[str] = Field(default_factory=list)
    excluded_case_ids: list[str] = Field(default_factory=list)
    exclusion_reasons: list[tuple[str, str]] = Field(default_factory=list)
    dependency_closure_ids: list[str] = Field(default_factory=list)
    applicability_exclusions: list[tuple[str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def reasons(self) -> ProposalScope:
        _require_nonblank(self.required_case_ids, "required_case_ids")
        _require_nonblank(self.excluded_case_ids, "excluded_case_ids")
        _require_nonblank(self.dependency_closure_ids, "dependency_closure_ids")
        for label, entries in (
            ("exclusion", self.exclusion_reasons),
            ("applicability exclusion", self.applicability_exclusions),
        ):
            seen: set[str] = set()
            for case_id, reason in entries:
                if not case_id.strip() or not reason.strip():
                    raise ValueError(f"{label} requires case_id and reason")
                if case_id in seen:
                    raise ValueError(f"duplicate {label} reason for {case_id}")
                seen.add(case_id)
        return self


class ProposalCoverageClaim(BaseModel):
    """One model-proposed ``intent × function × dimension`` claim.

    Only the linkage is model content.  `claim_id`, `status`, `plan_revision`,
    evidence and verification references are server-owned and therefore
    forbidden here (`extra="forbid"`), so a model cannot claim coverage proof.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_id: str = Field(min_length=1, max_length=200)
    function_id: str = Field(min_length=1, max_length=200)
    dimension: str = Field(min_length=1, max_length=200)
    case_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def nonblank_case_ids(self) -> ProposalCoverageClaim:
        _require_nonblank(self.case_ids, "case_ids")
        return self


class PlanProposal(BaseModel):
    """A complete, editable plan proposal.  Never a published plan."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "aitest.plan-proposal/1.0", "aitest.plan-proposal/1.1"
    ] = PLAN_PROPOSAL_SCHEMA_V10
    template_id: str = Field(min_length=1, max_length=200)
    template_version: str = Field(min_length=1, max_length=64)
    scope: ProposalScope
    cases: list[ProposalCase] = Field(min_length=1)
    #: 1.1 only: the exact understanding revision the proposal was drafted
    #: against.  Required by schema 1.1, absent (and rejected) in 1.0.
    understanding_revision: int | None = Field(default=None, ge=1)
    #: 1.1 only: the chosen `TestIntent` identities; non-empty and unique.
    goal_intent_ids: list[str] = Field(default_factory=list)
    #: 1.1 only: model-proposed coverage claims.
    coverage_claims: list[ProposalCoverageClaim] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def reject_mixed_versions(cls, data: object) -> object:
        """A 1.0 payload must not carry any 1.1 field (even at its default)."""

        if not isinstance(data, Mapping):
            return data
        version = data.get("schema_version", PLAN_PROPOSAL_SCHEMA_V10)
        present = _V11_FIELDS.intersection(data)
        if version == PLAN_PROPOSAL_SCHEMA_V10 and present:
            raise ValueError(
                "schema_version 1.0 must not contain 1.1 fields: "
                + ", ".join(sorted(present))
            )
        return data

    @model_validator(mode="after")
    def unique_identities(self) -> PlanProposal:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("duplicate case IDs")
        item_ids = [case.template_item_id for case in self.cases]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("duplicate template item IDs")
        if self.schema_version == PLAN_PROPOSAL_SCHEMA_V11:
            self._validate_v11(case_ids)
        return self

    def _validate_v11(self, case_ids: list[str]) -> None:
        if self.understanding_revision is None:
            raise ValueError("schema 1.1 requires understanding_revision")
        if not self.goal_intent_ids:
            raise ValueError("schema 1.1 requires at least one goal intent")
        _require_nonblank(self.goal_intent_ids, "goal_intent_ids")
        if len(self.goal_intent_ids) != len(set(self.goal_intent_ids)):
            raise ValueError("goal_intent_ids must be unique")
        known_cases = set(case_ids)
        goal_intents = set(self.goal_intent_ids)
        seen: set[tuple[str, str, str]] = set()
        for index, claim in enumerate(self.coverage_claims):
            if claim.intent_id not in goal_intents:
                raise ValueError(
                    f"coverage_claims[{index}].intent_id is not in goal_intent_ids: "
                    f"{claim.intent_id}"
                )
            missing = [case_id for case_id in claim.case_ids if case_id not in known_cases]
            if missing:
                raise ValueError(
                    f"coverage_claims[{index}].case_ids reference cases outside this "
                    "proposal: " + ", ".join(missing)
                )
            key = (claim.intent_id, claim.function_id, claim.dimension)
            if key in seen:
                raise ValueError(
                    "duplicate coverage claim for intent × function × dimension: "
                    f"{claim.intent_id} × {claim.function_id} × {claim.dimension}"
                )
            seen.add(key)

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Keep the 1.0 wire shape byte-compatible with the previous contract."""

        payload = super().model_dump(**kwargs)
        if payload.get("schema_version") == PLAN_PROPOSAL_SCHEMA_V10:
            for name in _V11_FIELDS:
                payload.pop(name, None)
        return payload


__all__ = [
    "PLAN_PROPOSAL_SCHEMA_V10",
    "PLAN_PROPOSAL_SCHEMA_V11",
    "PlanProposal",
    "ProposalCase",
    "ProposalCaseLink",
    "ProposalCoverageClaim",
    "ProposalScope",
]
