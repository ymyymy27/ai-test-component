"""Structured AI plan proposal (`aitest.plan-proposal/1.0`).

The model only fills a template skeleton; it never supplies server-owned
identity, revisions, digests, environment links or publication state.  This
module is intentionally a *contract* (pydantic only): parsing, template
coverage and materialisation live in `application/planning/proposal.py`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class PlanProposal(BaseModel):
    """A complete, editable plan proposal.  Never a published plan."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["aitest.plan-proposal/1.0"] = "aitest.plan-proposal/1.0"
    template_id: str = Field(min_length=1, max_length=200)
    template_version: str = Field(min_length=1, max_length=64)
    scope: ProposalScope
    cases: list[ProposalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_identities(self) -> PlanProposal:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("duplicate case IDs")
        item_ids = [case.template_item_id for case in self.cases]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("duplicate template item IDs")
        return self


__all__ = [
    "PlanProposal",
    "ProposalCase",
    "ProposalCaseLink",
    "ProposalScope",
]
