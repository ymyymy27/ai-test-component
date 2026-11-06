"""Parse, validate and materialise `aitest.plan-proposal/1.0`.

The AI fills a template skeleton.  This module is the only place that turns
that proposal into domain `Case`/`AcceptanceScope` objects, so template
coverage and server-owned identity are checked once and cannot be skipped by
the panel or the onboarding helper.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from aitest.application.planning.draft import load_template, text_digest
from aitest.application.planning.serialization import (
    acceptance_scope_to_payload,
    case_to_payload,
)
from aitest.contracts.plan_proposal import PlanProposal, ProposalCase
from aitest.contracts.templates import TemplateItem, TemplatePack
from aitest.domain.planning.plans import (
    AcceptanceScope,
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseImportance,
    CaseLayer,
    CaseLink,
)
from aitest.domain.planning.templates import TemplateRef

PLAN_PROPOSAL_SCHEMA = "aitest.plan-proposal/1.0"


def plan_proposal_system_prompt() -> str:
    """Task instruction for structured plan generation.

    Only the output shape and task constraints are sent; project material is
    still projected separately and checked against the outbound policy.
    """

    return (
        "You draft one test plan for the project material supplied by the user.\n"
        "Return ONLY one JSON object, with no Markdown fence and no prose, matching:\n"
        "{\n"
        '  "schema_version": "aitest.plan-proposal/1.0",\n'
        '  "template_id": "<installed template id>",\n'
        '  "template_version": "<installed template version>",\n'
        '  "scope": {\n'
        '    "name": "<short scope name>",\n'
        '    "objective": "<what this scope verifies>",\n'
        '    "required_case_ids": [],\n'
        '    "excluded_case_ids": [],\n'
        '    "exclusion_reasons": [],\n'
        '    "dependency_closure_ids": [],\n'
        '    "applicability_exclusions": []\n'
        "  },\n"
        '  "cases": [{\n'
        '    "case_id": "<stable id>",\n'
        '    "template_item_id": "<exact item id from the template>",\n'
        '    "layer": "L1|L2|L3",\n'
        '    "objective": "<what this case checks>",\n'
        '    "preconditions": ["<condition>"],\n'
        '    "inputs": ["<input>"],\n'
        '    "steps": ["<step>"],\n'
        '    "expected": "<observable expected result>",\n'
        '    "verification_method": "<how the result is checked>",\n'
        '    "links": {\n'
        '      "acceptance_item_ids": ["<template item id>"],\n'
        '      "module_ids": [],\n'
        '      "critical_path_ids": [],\n'
        '      "no_critical_path_reason": "<reason when critical_path_ids is empty>"\n'
        "    },\n"
        '    "assertion_basis_text": "<why this assertion proves the acceptance item>",\n'
        '    "independent_verification": "<how expected values stay independent from execution>",\n'
        '    "mock_scope": [],\n'
        '    "importance": "P0|P1|P2"\n'
        "  }]\n"
        "}\n"
        "Rules:\n"
        "- Produce exactly one case per template item, using its exact template_item_id "
        "and layer.\n"
        "- Never invent template item IDs; never omit a template item.\n"
        "- Every case needs a non-empty expected, assertion_basis_text and "
        "independent_verification.\n"
        "- When critical_path_ids is empty, provide no_critical_path_reason.\n"
        "- Do not include project_id, revisions, digests, environment IDs, confirmation "
        "state, or publication state; the server owns those.\n"
    )


@dataclass(frozen=True, slots=True)
class ProposalIssue:
    """One field-level problem; `path` uses dotted/indexed form for the UI."""

    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


class PlanProposalError(ValueError):
    """Structured proposal rejection; never a partially materialised plan."""

    code = "PLAN_PROPOSAL_INVALID"

    def __init__(self, issues: tuple[ProposalIssue, ...], *, code: str | None = None) -> None:
        if not issues:
            raise ValueError("a proposal rejection requires at least one issue")
        self.issues = issues
        if code is not None:
            self.code = code
        super().__init__("; ".join(f"{issue.path}: {issue.message}" for issue in issues))

    def as_result(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": str(self),
            "issues": [issue.as_dict() for issue in self.issues],
        }


def strip_code_fence(text: str) -> str:
    """Remove one leading/trailing Markdown fence without changing JSON bytes.

    This is a parse tolerance, not a content repair: the remaining text must
    still be a complete JSON object.  Anything else is rejected.
    """

    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2 or not lines[0].strip().lower().startswith("```"):
        return stripped
    if lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def parse_plan_proposal(text: str) -> PlanProposal:
    """Strict JSON parse plus pydantic schema validation."""

    candidate = strip_code_fence(text)
    try:
        raw = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise PlanProposalError(
            (
                ProposalIssue(
                    "$",
                    f"response is not valid JSON (line {error.lineno}, column {error.colno})",
                ),
            ),
            code="PLAN_PROPOSAL_INVALID_JSON",
        ) from error
    if not isinstance(raw, dict):
        raise PlanProposalError(
            (ProposalIssue("$", "response must be a JSON object"),),
            code="PLAN_PROPOSAL_INVALID_JSON",
        )
    try:
        return PlanProposal.model_validate(raw)
    except ValidationError as error:
        issues = tuple(
            ProposalIssue(
                ".".join(str(part) for part in item["loc"]) or "$",
                str(item["msg"]),
            )
            for item in error.errors()
        )
        raise PlanProposalError(issues, code="PLAN_PROPOSAL_SCHEMA_INVALID") from error


def _template_items(pack: TemplatePack) -> dict[str, TemplateItem]:
    return {item.item_id: item for item in pack.items}


def _required_template_item_ids(pack: TemplatePack) -> set[str]:
    return set(pack.required_item_ids)


def _coverage_issues(proposal: PlanProposal, pack: TemplatePack) -> list[ProposalIssue]:
    issues: list[ProposalIssue] = []
    items = _template_items(pack)
    proposed = {case.template_item_id: case for case in proposal.cases}
    missing = sorted(set(items) - set(proposed))
    extra = sorted(set(proposed) - set(items))
    if missing:
        issues.append(
            ProposalIssue(
                "cases",
                "template items are not covered: " + ", ".join(missing),
            )
        )
    if extra:
        issues.append(
            ProposalIssue(
                "cases",
                "unknown template items: " + ", ".join(extra),
            )
        )
    for case in proposal.cases:
        item = items.get(case.template_item_id)
        if item is not None and case.layer != item.layer:
            issues.append(
                ProposalIssue(
                    f"cases[{case.case_id}].layer",
                    f"layer {case.layer} differs from template item {item.item_id} ({item.layer})",
                )
            )
    return issues


def _scope_issues(proposal: PlanProposal, pack: TemplatePack) -> list[ProposalIssue]:
    issues: list[ProposalIssue] = []
    case_ids = {case.case_id for case in proposal.cases}
    required_by_template = {
        case.case_id
        for case in proposal.cases
        if case.template_item_id in _required_template_item_ids(pack)
    }
    scope = proposal.scope

    for case_id in scope.required_case_ids:
        if case_id not in case_ids:
            issues.append(
                ProposalIssue(
                    "scope.required_case_ids",
                    f"unknown case: {case_id}",
                )
            )
    for case_id in scope.excluded_case_ids:
        if case_id not in case_ids:
            issues.append(
                ProposalIssue(
                    "scope.excluded_case_ids",
                    f"unknown case: {case_id}",
                )
            )
    excluded = set(scope.excluded_case_ids)
    missing_reasons = excluded - {case_id for case_id, _ in scope.exclusion_reasons}
    if missing_reasons:
        issues.append(
            ProposalIssue(
                "scope.exclusion_reasons",
                "excluded cases require a reason: " + ", ".join(sorted(missing_reasons)),
            )
        )
    if excluded & required_by_template:
        issues.append(
            ProposalIssue(
                "scope.excluded_case_ids",
                "a full run cannot exclude required template items: "
                + ", ".join(sorted(excluded & required_by_template)),
            )
        )
    for case_id, _reason in scope.applicability_exclusions:
        if case_id not in case_ids:
            issues.append(
                ProposalIssue(
                    "scope.applicability_exclusions",
                    f"unknown case: {case_id}",
                )
            )
    for case_id in scope.dependency_closure_ids:
        if case_id not in case_ids:
            issues.append(
                ProposalIssue(
                    "scope.dependency_closure_ids",
                    f"unknown case: {case_id}",
                )
            )

    if scope.required_case_ids:
        provided = set(scope.required_case_ids)
        if not required_by_template <= provided:
            issues.append(
                ProposalIssue(
                    "scope.required_case_ids",
                    "required template cases are missing: "
                    + ", ".join(sorted(required_by_template - provided)),
                )
            )
        if provided & excluded:
            issues.append(
                ProposalIssue(
                    "scope.required_case_ids",
                    "excluded cases cannot be required: "
                    + ", ".join(sorted(provided & excluded)),
                )
            )
        expected = case_ids - excluded
        if provided != expected:
            issues.append(
                ProposalIssue(
                    "scope.required_case_ids",
                    "every non-excluded case must be required; missing: "
                    + ", ".join(sorted(expected - provided))
                    + ("; unexpected: " + ", ".join(sorted(provided - expected))
                       if provided - expected else ""),
                )
            )
    return issues


def validate_plan_proposal(
    proposal: PlanProposal, *, pack: TemplatePack | None = None
) -> TemplatePack:
    """Validate coverage, template layers and scope consistency.

    Returns the loaded pack so callers do not load it twice.  Raises a
    structured `PlanProposalError` listing every issue found.
    """

    if pack is None:
        pack = load_template(
            TemplateRef(
                template_id=proposal.template_id,
                version=proposal.template_version,
            )
        )
    issues = [*_coverage_issues(proposal, pack), *_scope_issues(proposal, pack)]
    if issues:
        raise PlanProposalError(tuple(issues))
    return pack


def _required_case_ids(proposal: PlanProposal) -> frozenset[str]:
    all_ids = frozenset(case.case_id for case in proposal.cases)
    if not proposal.scope.required_case_ids:
        return all_ids - frozenset(proposal.scope.excluded_case_ids)
    return frozenset(proposal.scope.required_case_ids)


def _template_case_ids(proposal: PlanProposal, pack: TemplatePack) -> frozenset[str]:
    required_items = _required_template_item_ids(pack)
    return frozenset(
        case.case_id
        for case in proposal.cases
        if case.template_item_id in required_items
    )


def _basis_text(proposal_case: ProposalCase) -> tuple[str, str]:
    digest = text_digest(proposal_case.assertion_basis_text)
    if digest is None:
        raise PlanProposalError(
            (
                ProposalIssue(
                    f"cases[{proposal_case.case_id}].assertion_basis_text",
                    "assertion basis text must not be blank",
                ),
            )
        )
    return proposal_case.assertion_basis_text, digest


def materialize_plan_proposal(
    proposal: PlanProposal,
    *,
    project_id: str,
    environment_id: str,
    scope_id: str,
    pack: TemplatePack | None = None,
) -> tuple[AcceptanceScope, tuple[Case, ...]]:
    """Turn a validated proposal into domain objects.

    The server owns: revision 1, basis state `present_unconfirmed`, basis
    digest, environment link, project identity and template coverage.  The
    model cannot supply any of them.
    """

    if not project_id.strip() or not environment_id.strip() or not scope_id.strip():
        raise ValueError("project, environment and scope identities must not be blank")
    pack = validate_plan_proposal(proposal, pack=pack)
    cases: list[Case] = []
    for proposed in proposal.cases:
        basis_text, basis_digest = _basis_text(proposed)
        link = CaseLink(
            acceptance_item_ids=frozenset(proposed.links.acceptance_item_ids),
            module_ids=frozenset(proposed.links.module_ids),
            environment_ids=frozenset({environment_id}),
            critical_path_ids=frozenset(proposed.links.critical_path_ids),
            no_critical_path_reason=proposed.links.no_critical_path_reason,
        )
        cases.append(
            Case(
                case_id=proposed.case_id,
                revision=1,
                layer=CaseLayer(proposed.layer),
                objective=proposed.objective,
                preconditions=tuple(proposed.preconditions),
                inputs=tuple(proposed.inputs),
                steps=tuple(proposed.steps),
                expected=proposed.expected,
                verification_method=proposed.verification_method,
                links=link,
                assertion_basis=AssertionBasis(
                    1,
                    AssertionBasisState.PRESENT_UNCONFIRMED,
                    basis_text,
                    basis_digest,
                ),
                independent_verification=proposed.independent_verification,
                mock_scope=tuple(proposed.mock_scope),
                importance=CaseImportance(proposed.importance),
            )
        )
    scope = AcceptanceScope(
        scope_id=scope_id,
        revision=1,
        name=proposal.scope.name,
        required_case_ids=_required_case_ids(proposal),
        template_case_ids=_template_case_ids(proposal, pack),
        objective=proposal.scope.objective,
        excluded_case_ids=frozenset(proposal.scope.excluded_case_ids),
        exclusion_reasons=tuple(proposal.scope.exclusion_reasons),
        dependency_closure_ids=frozenset(proposal.scope.dependency_closure_ids),
        applicability_exclusions=tuple(proposal.scope.applicability_exclusions),
    )
    return scope, tuple(sorted(cases, key=lambda case: (case.case_id, case.revision)))


def proposal_preview(
    proposal: PlanProposal,
    *,
    project_id: str,
    environment_id: str,
    scope_id: str,
    pack: TemplatePack | None = None,
) -> dict[str, object]:
    """Editable preview payload for the panel; never writes a record."""

    scope, cases = materialize_plan_proposal(
        proposal,
        project_id=project_id,
        environment_id=environment_id,
        scope_id=scope_id,
        pack=pack,
    )
    resolved = validate_plan_proposal(proposal, pack=pack)
    return {
        "schema_version": "aitest.plan-proposal-preview/1.0",
        "template_id": proposal.template_id,
        "template_version": proposal.template_version,
        "template_name": resolved.name,
        "scope": acceptance_scope_to_payload(scope, project_id=project_id),
        "cases": [case_to_payload(case, project_id=project_id) for case in cases],
        "coverage": {
            "template_items": len(resolved.items),
            "covered_items": len(proposal.cases),
            "required_items": len(resolved.required_item_ids),
        },
        "environment_id": environment_id,
        "proposal": proposal.model_dump(mode="json"),
    }


def proposal_from_mapping(value: object) -> PlanProposal:
    """Validate a mapping that already came from the panel/onboarding helper."""

    if not isinstance(value, Mapping):
        raise PlanProposalError(
            (ProposalIssue("$", "proposal must be an object"),),
            code="PLAN_PROPOSAL_SCHEMA_INVALID",
        )
    try:
        return PlanProposal.model_validate(dict(value))
    except ValidationError as error:
        issues = tuple(
            ProposalIssue(
                ".".join(str(part) for part in item["loc"]) or "$",
                str(item["msg"]),
            )
            for item in error.errors()
        )
        raise PlanProposalError(issues, code="PLAN_PROPOSAL_SCHEMA_INVALID") from error


__all__ = [
    "PLAN_PROPOSAL_SCHEMA",
    "PlanProposalError",
    "ProposalIssue",
    "materialize_plan_proposal",
    "parse_plan_proposal",
    "plan_proposal_system_prompt",
    "proposal_preview",
    "proposal_from_mapping",
    "strip_code_fence",
    "validate_plan_proposal",
]
