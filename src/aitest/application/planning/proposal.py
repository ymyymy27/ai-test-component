"""Parse, validate and materialise `aitest.plan-proposal/1.0` and `1.1`.

The AI fills a template skeleton.  This module is the only place that turns
that proposal into domain `Case`/`AcceptanceScope`/`CoverageContract` objects,
so template coverage, claim linkage and server-owned identity are checked once
and cannot be skipped by the panel or the onboarding helper.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from aitest.application.planning.draft import load_template, text_digest
from aitest.application.planning.serialization import (
    acceptance_scope_to_payload,
    case_to_payload,
    coverage_contract_to_payload,
)
from aitest.application.project.understanding import understanding_summary
from aitest.application.review.function_coverage_preview import (
    build_plan_function_coverage,
)
from aitest.contracts.plan_proposal import (
    PLAN_PROPOSAL_SCHEMA_V10,
    PLAN_PROPOSAL_SCHEMA_V11,
    PlanProposal,
    ProposalCase,
    ProposalCoverageClaim,
    ProposalTemplateDisposition,
)
from aitest.contracts.templates import TemplateItem, TemplatePack
from aitest.domain.planning.plans import (
    AcceptanceScope,
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseImportance,
    CaseLayer,
    CaseLink,
    CoverageContract,
    FunctionRequirement,
    TemplateApplicability,
    TemplateCoverage,
    TemplateDisposition,
    validate_coverage_contract,
)
from aitest.domain.planning.templates import TemplateRef
from aitest.domain.planning.understanding import (
    CoverageClaim,
    CoverageClaimStatus,
    ProjectUnderstanding,
    TestIntent,
)
from aitest.domain.review.function_coverage import FunctionCoverage

#: Task marker kept for existing callers (`output_schema`).  Parsing accepts
#: both versions; `plan_proposal_system_prompt()` decides which to request.
PLAN_PROPOSAL_SCHEMA = PLAN_PROPOSAL_SCHEMA_V10
PLAN_PROPOSAL_SCHEMAS = frozenset(
    {PLAN_PROPOSAL_SCHEMA_V10, PLAN_PROPOSAL_SCHEMA_V11}
)


def plan_proposal_system_prompt() -> str:
    """Task instruction for structured plan generation.

    Only the output shape and task constraints are sent; project material is
    still projected separately and checked against the outbound policy.
    """

    return (
        "You draft one test plan for the project material supplied by the user.\n"
        "Return ONLY one JSON object, with no Markdown fence and no prose, matching:\n"
        "{\n"
        '  "schema_version": "aitest.plan-proposal/1.1",\n'
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
        '    "template_item_id": "<exact item id, or null for an AI-added check>",\n'
        '    "origin": "template|ai|user",\n'
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
        '    "importance": "P0|P1|P2",\n'
        '    "data_mutation_units": 1\n'
        "  }],\n"
        '  "template_dispositions": [{\n'
        '    "template_item_id": "<exact item id from the template>",\n'
        '    "applicability": "applicable|not_applicable|unknown",\n'
        '    "coverage": "covered|uncovered|deferred",\n'
        '    "reason": "<why this disposition is true>",\n'
        '    "evidence": ["<source or project fact>"]\n'
        "  }],\n"
        '  "understanding_revision": <understanding revision supplied with the material>,\n'
        '  "goal_intent_ids": ["<supplied test intent id>"],\n'
        '  "coverage_claims": [{\n'
        '    "intent_id": "<one of goal_intent_ids>",\n'
        '    "function_id": "<business function id from the supplied understanding>",\n'
        '    "dimension": "<required dimension of that function>",\n'
        '    "case_ids": ["<case_id from cases above>"]\n'
        "  }]\n"
        "}\n"
        "Rules:\n"
        "- Produce exactly one disposition per template item; never silently omit one.\n"
        "- A template case uses its exact template_item_id and layer; only "
        "applicable+covered items may carry cases.\n"
        "- AI-added checks use template_item_id null, origin ai, and must fill a "
        "coverage gap through coverage_claims.\n"
        "- Every case needs a non-empty expected, assertion_basis_text and "
        "independent_verification.\n"
        "- data_mutation_units is the declared upper bound of business objects/rows "
        "the case may change; 0 means read-only. The plan grant budget is checked "
        "against their sum.\n"
        "- When critical_path_ids is empty, provide no_critical_path_reason.\n"
        "- When the material contains a project-understanding revision with test "
        "intents, use schema_version 1.1, copy that revision into "
        "understanding_revision, and include the selected intent IDs in "
        "goal_intent_ids.\n"
        "- goal_intent_ids must be non-empty and unique; every coverage_claims "
        "intent_id must be one of them.\n"
        "- Every coverage claim must use a function_id and dimension from the "
        "supplied understanding, and its case_ids must all exist in cases above. "
        "List each intent × function × dimension at most once.\n"
        "- Without a supplied understanding revision, use schema_version 1.0 and "
        "omit understanding_revision, goal_intent_ids and coverage_claims. Never "
        "mix 1.0 with 1.1 fields.\n"
        "- Do not include claim_id, claim status, plan_revision, project_id, "
        "revisions, digests, environment IDs, confirmation state, or publication "
        "state; the server owns those.\n"
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
    items = _template_items(pack)
    if proposal.schema_version == PLAN_PROPOSAL_SCHEMA_V10:
        proposed = {
            case.template_item_id: case
            for case in proposal.cases
            if case.template_item_id is not None
        }
        missing = sorted(set(items) - set(proposed))
        extra = sorted(set(proposed) - set(items))
        issues: list[ProposalIssue] = []
        if missing:
            issues.append(
                ProposalIssue("cases", "template items are not covered: " + ", ".join(missing))
            )
        if extra:
            issues.append(
                ProposalIssue("cases", "unknown template items: " + ", ".join(extra))
            )
        for case in proposal.cases:
            item = items.get(str(case.template_item_id))
            if item is not None and case.layer != item.layer:
                issues.append(
                    ProposalIssue(
                        f"cases[{case.case_id}].layer",
                        f"layer {case.layer} differs from template item {item.item_id} "
                        f"({item.layer})",
                    )
                )
        return issues

    issues = []
    dispositions: dict[str, ProposalTemplateDisposition] = {}
    for index, disposition in enumerate(proposal.template_dispositions):
        if disposition.template_item_id not in items:
            issues.append(
                ProposalIssue(
                    f"template_dispositions[{index}].template_item_id",
                    f"unknown template item: {disposition.template_item_id}",
                )
            )
            continue
        if disposition.template_item_id in dispositions:
            issues.append(
                ProposalIssue(
                    f"template_dispositions[{index}].template_item_id",
                    f"duplicate disposition: {disposition.template_item_id}",
                )
            )
            continue
        dispositions[disposition.template_item_id] = disposition
    cases_by_item: dict[str, list[str]] = {}
    claim_case_ids = {
        case_id for claim in proposal.coverage_claims for case_id in claim.case_ids
    }
    for case in proposal.cases:
        if case.origin == "template":
            item = items.get(case.template_item_id or "")
            if item is None:
                issues.append(
                    ProposalIssue(
                        f"cases[{case.case_id}].template_item_id",
                        "unknown template item: " + str(case.template_item_id),
                    )
                )
                continue
            if case.layer != item.layer:
                issues.append(
                    ProposalIssue(
                        f"cases[{case.case_id}].layer",
                        f"layer {case.layer} differs from template item {item.item_id} "
                        f"({item.layer})",
                    )
                )
            cases_by_item.setdefault(item.item_id, []).append(case.case_id)
        elif case.case_id not in claim_case_ids:
            issues.append(
                ProposalIssue(
                    f"cases[{case.case_id}]",
                    "AI/user-added cases must fill a coverage gap via coverage_claims",
                )
            )
    for item_id in items:
        found = dispositions.get(item_id)
        if found is None:
            issues.append(
                ProposalIssue(
                    "template_dispositions",
                    f"missing disposition for template item: {item_id}",
                )
            )
            continue
        applicability = str(found.applicability)
        coverage = str(found.coverage)
        case_ids = cases_by_item.get(item_id, [])
        if applicability == "applicable" and coverage == "covered" and not case_ids:
            issues.append(
                ProposalIssue(
                    "template_dispositions",
                    f"applicable+covered item has no case: {item_id}",
                )
            )
        if applicability == "applicable" and coverage != "covered" and case_ids:
            issues.append(
                ProposalIssue(
                    "template_dispositions",
                    f"applicable+{coverage} item must not carry cases: {item_id}",
                )
            )
        if applicability in {"not_applicable", "unknown"} and case_ids:
            issues.append(
                ProposalIssue(
                    "template_dispositions",
                    f"{applicability} item must not carry cases: {item_id}",
                )
            )
        if applicability == "not_applicable" and not found.reason.strip():
            issues.append(
                ProposalIssue(
                    "template_dispositions",
                    f"not_applicable item needs a reason: {item_id}",
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
    if proposal.schema_version == PLAN_PROPOSAL_SCHEMA_V11:
        covered_items = {
            disposition.template_item_id
            for disposition in proposal.template_dispositions
            if disposition.applicability == "applicable"
            and disposition.coverage == "covered"
        }
    else:
        covered_items = _required_template_item_ids(pack)
    return frozenset(
        case.case_id
        for case in proposal.cases
        if case.template_item_id in covered_items
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
    functions_by_case: dict[str, set[str]] = {}
    for claim in proposal.coverage_claims:
        for case_id in claim.case_ids:
            functions_by_case.setdefault(case_id, set()).add(claim.function_id)
    cases: list[Case] = []
    for proposed in proposal.cases:
        basis_text, basis_digest = _basis_text(proposed)
        link = CaseLink(
            acceptance_item_ids=frozenset(proposed.links.acceptance_item_ids),
            module_ids=frozenset(proposed.links.module_ids),
            environment_ids=frozenset({environment_id}),
            critical_path_ids=frozenset(proposed.links.critical_path_ids),
            no_critical_path_reason=proposed.links.no_critical_path_reason,
            function_ids=frozenset(functions_by_case.get(proposed.case_id, set())),
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
                template_item_id=proposed.template_item_id,
                data_mutation_units=proposed.data_mutation_units,
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


def _coverage_issue(path: str, message: str) -> ProposalIssue:
    return ProposalIssue(path, message)


def _coverage_claim_id(plan_revision: int, claim: ProposalCoverageClaim) -> str:
    """Server-owned claim identity; the model never supplies it."""

    return (
        f"plan-claim:{plan_revision}:{claim.intent_id}:"
        f"{claim.function_id}:{claim.dimension}"
    )


def _domain_template_dispositions(
    proposal: PlanProposal,
) -> tuple[TemplateDisposition, ...]:
    return tuple(
        TemplateDisposition(
            template_item_id=item.template_item_id,
            applicability=TemplateApplicability(item.applicability),
            coverage=TemplateCoverage(item.coverage),
            reason=item.reason,
            evidence=tuple(item.evidence),
        )
        for item in proposal.template_dispositions
    )


def _coverage_contract_id(
    understanding: ProjectUnderstanding, plan_revision: int
) -> str:
    """Server-owned contract identity bound to the understanding revision."""

    return (
        f"plan-coverage-contract:{plan_revision}:"
        f"{understanding.understanding_id}:{understanding.revision}"
    )


def materialize_coverage_contract(
    proposal: PlanProposal,
    understanding: ProjectUnderstanding,
    *,
    plan_revision: int,
    template_dispositions: Sequence[TemplateDisposition] | None = None,
) -> CoverageContract:
    """Turn a 1.1 proposal into a domain `CoverageContract`.

    The model supplies only `intent_id`/`function_id`/`dimension`/`case_ids`.
    This function re-checks every linkage against the referenced understanding
    (rather than trusting the proposal schema alone), generates `claim_id` and
    `contract_id`, sets every claim to ``not_proven`` and binds the caller's
    `plan_revision`.  `template_dispositions` stay a caller input; without them
    the contract has none (it must not invent applicability decisions).

    `critical_path_ids` stay empty here: critical-path selection is a separate
    plan concern, not a proposal field.
    """

    if type(plan_revision) is not int or plan_revision < 1:
        raise PlanProposalError(
            (_coverage_issue("plan_revision", "plan_revision must be >= 1"),)
        )

    issues: list[ProposalIssue] = []
    if proposal.schema_version != PLAN_PROPOSAL_SCHEMA_V11:
        issues.append(
            _coverage_issue(
                "schema_version",
                "coverage materialisation requires aitest.plan-proposal/1.1",
            )
        )
    if proposal.understanding_revision is None:
        issues.append(
            _coverage_issue(
                "understanding_revision",
                "schema 1.1 requires understanding_revision",
            )
        )
    elif proposal.understanding_revision != understanding.revision:
        issues.append(
            _coverage_issue(
                "understanding_revision",
                f"proposal uses revision {proposal.understanding_revision}, "
                f"but the supplied understanding is revision {understanding.revision}",
            )
        )

    if not proposal.goal_intent_ids:
        issues.append(
            _coverage_issue("goal_intent_ids", "at least one goal intent is required")
        )
    elif any(not intent_id.strip() for intent_id in proposal.goal_intent_ids):
        issues.append(
            _coverage_issue("goal_intent_ids", "goal intent IDs must not be blank")
        )
    if len(proposal.goal_intent_ids) != len(set(proposal.goal_intent_ids)):
        issues.append(_coverage_issue("goal_intent_ids", "goal intent IDs must be unique"))

    intents = {intent.intent_id: intent for intent in understanding.test_intents}
    functions = {
        function.function_id: function
        for function in understanding.catalog.functions
    }
    case_ids = {case.case_id for case in proposal.cases}
    goal_ids = list(dict.fromkeys(proposal.goal_intent_ids))
    selected_intents: list[TestIntent] = []
    for intent_id in goal_ids:
        intent = intents.get(intent_id)
        if intent is None:
            issues.append(
                _coverage_issue(
                    "goal_intent_ids",
                    f"unknown test intent in the supplied understanding: {intent_id}",
                )
            )
            continue
        selected_intents.append(intent)
        missing_functions = sorted(intent.function_ids - set(functions))
        if missing_functions:
            issues.append(
                _coverage_issue(
                    "goal_intent_ids",
                    f"test intent {intent_id} references unknown functions: "
                    + ", ".join(missing_functions),
                )
            )

    seen_claims: set[tuple[str, str, str]] = set()
    for index, claim in enumerate(proposal.coverage_claims):
        path = f"coverage_claims[{index}]"
        if claim.intent_id not in goal_ids:
            issues.append(
                _coverage_issue(
                    f"{path}.intent_id",
                    f"claim intent is not one of goal_intent_ids: {claim.intent_id}",
                )
            )
            continue
        intent = intents.get(claim.intent_id)
        if intent is None:
            issues.append(
                _coverage_issue(
                    f"{path}.intent_id",
                    f"unknown test intent in the supplied understanding: "
                    f"{claim.intent_id}",
                )
            )
            continue
        function = functions.get(claim.function_id)
        if function is None:
            issues.append(
                _coverage_issue(
                    f"{path}.function_id",
                    f"unknown business function in the supplied understanding: "
                    f"{claim.function_id}",
                )
            )
            continue
        if claim.function_id not in intent.function_ids:
            issues.append(
                _coverage_issue(
                    f"{path}.function_id",
                    f"function {claim.function_id} is not linked to intent "
                    f"{claim.intent_id}",
                )
            )
            continue
        if claim.dimension not in function.required_dimensions:
            issues.append(
                _coverage_issue(
                    f"{path}.dimension",
                    f"dimension {claim.dimension} is not required for "
                    f"{claim.function_id}",
                )
            )
        missing_cases = [
            case_id for case_id in claim.case_ids if case_id not in case_ids
        ]
        if missing_cases:
            issues.append(
                _coverage_issue(
                    f"{path}.case_ids",
                    "claim references cases outside this proposal: "
                    + ", ".join(missing_cases),
                )
            )
        key = (claim.intent_id, claim.function_id, claim.dimension)
        if key in seen_claims:
            issues.append(
                _coverage_issue(
                    path,
                    "duplicate coverage claim for intent × function × dimension: "
                    f"{claim.intent_id} × {claim.function_id} × {claim.dimension}",
                )
            )
        seen_claims.add(key)

    if issues:
        raise PlanProposalError(tuple(issues))

    requirements: list[FunctionRequirement] = []
    seen_functions: set[str] = set()
    for intent in selected_intents:
        for function_id in sorted(intent.function_ids):
            if function_id in seen_functions:
                continue
            seen_functions.add(function_id)
            requirements.append(
                FunctionRequirement(
                    function_id=function_id,
                    required_dimensions=functions[function_id].required_dimensions,
                )
            )

    claims = tuple(
        CoverageClaim(
            claim_id=_coverage_claim_id(plan_revision, claim),
            intent_id=claim.intent_id,
            function_id=claim.function_id,
            dimension=claim.dimension,
            plan_revision=plan_revision,
            status=CoverageClaimStatus.NOT_PROVEN,
            case_ids=frozenset(claim.case_ids),
        )
        for claim in proposal.coverage_claims
    )
    contract = CoverageContract(
        contract_id=_coverage_contract_id(understanding, plan_revision),
        understanding_revision=understanding.revision,
        function_requirements=tuple(requirements),
        template_dispositions=tuple(template_dispositions or ()),
        coverage_claims=claims,
    )
    validate_coverage_contract(contract)
    return contract


def validate_coverage_contract_against_understanding(
    contract: CoverageContract,
    understanding: ProjectUnderstanding,
    *,
    plan_revision: int,
    require_all_functions: bool,
) -> None:
    """发布前核对覆盖契约确实来自这份 understanding，而不是面板自报。

    结构校验之外，逐项核对功能、必要维度、测试意图与用例归属；``full``
    还要求契约覆盖 understanding 目录里的全部业务功能，避免只挑少量
    功能就宣称完整覆盖。
    """

    try:
        validate_coverage_contract(contract)
    except ValueError as error:
        raise PlanProposalError((_coverage_issue("coverage_contract", str(error)),)) from error
    if contract.understanding_revision != understanding.revision:
        raise PlanProposalError(
            (
                _coverage_issue(
                    "coverage_contract.understanding_revision",
                    "contract revision differs from the saved understanding",
                ),
            )
        )
    if require_all_functions:
        if understanding.unknown_areas:
            raise PlanProposalError(
                (
                    _coverage_issue(
                        "understanding.unknown_areas",
                        "unknown project areas block full validation",
                    ),
                )
            )
        blocking_gaps = [
            gap.gap_id for gap in understanding.capability_gaps if gap.blocks_full
        ]
        if blocking_gaps:
            raise PlanProposalError(
                (
                    _coverage_issue(
                        "understanding.capability_gaps",
                        "unsupported capabilities block full validation: "
                        + ", ".join(blocking_gaps),
                    ),
                )
            )
    functions = {
        function.function_id: function for function in understanding.catalog.functions
    }
    intents = {intent.intent_id: intent for intent in understanding.test_intents}
    contracted = {
        requirement.function_id: requirement
        for requirement in contract.function_requirements
    }
    if require_all_functions:
        missing = sorted(set(functions) - set(contracted))
        extra = sorted(set(contracted) - set(functions))
        if missing or extra:
            raise PlanProposalError(
                (
                    _coverage_issue(
                        "coverage_contract.function_requirements",
                        "full coverage must list every business function: "
                        f"missing={missing} extra={extra}",
                    ),
                )
            )
    for requirement in contract.function_requirements:
        function = functions.get(requirement.function_id)
        if function is None:
            raise PlanProposalError(
                (
                    _coverage_issue(
                        "coverage_contract.function_requirements",
                        f"unknown business function: {requirement.function_id}",
                    ),
                )
            )
        if tuple(sorted(requirement.required_dimensions)) != tuple(
            sorted(function.required_dimensions)
        ):
            raise PlanProposalError(
                (
                    _coverage_issue(
                        "coverage_contract.function_requirements",
                        f"required dimensions differ for {requirement.function_id}",
                    ),
                )
            )
    for claim in contract.coverage_claims:
        intent = intents.get(claim.intent_id)
        if intent is None or claim.function_id not in intent.function_ids:
            raise PlanProposalError(
                (
                    _coverage_issue(
                        "coverage_contract.coverage_claims",
                        f"claim intent/function linkage is unknown: {claim.claim_id}",
                    ),
                )
            )
        if claim.dimension not in functions[claim.function_id].required_dimensions:
            raise PlanProposalError(
                (
                    _coverage_issue(
                        "coverage_contract.coverage_claims",
                        f"claim dimension is not required: {claim.claim_id}",
                    ),
                )
            )
        if claim.plan_revision != plan_revision:
            raise PlanProposalError(
                (
                    _coverage_issue(
                        "coverage_contract.coverage_claims",
                        f"claim plan revision differs: {claim.claim_id}",
                    ),
                )
            )


def _coverage_preview_entry(coverage: FunctionCoverage) -> dict[str, object]:
    """One confirmation-page coverage row; it is a projection, not a report."""

    return {
        "function_id": coverage.function_id,
        "overall_status": coverage.overall_coverage.value,
        "dimensions": {
            dimension: status.value
            for dimension, status in coverage.coverage_by_dimension.items()
        },
        "gap_reason": coverage.gap_reason,
    }


def proposal_preview(
    proposal: PlanProposal,
    *,
    project_id: str,
    environment_id: str,
    scope_id: str,
    pack: TemplatePack | None = None,
    understanding: ProjectUnderstanding | None = None,
) -> dict[str, object]:
    """Editable preview payload for the panel; never writes a record.

    A 1.1 proposal with its understanding also gets a `coverage_preview` built
    from the materialised contract.  Without both, the list stays empty: the
    preview must not imply complete function × dimension coverage.
    """

    scope, cases = materialize_plan_proposal(
        proposal,
        project_id=project_id,
        environment_id=environment_id,
        scope_id=scope_id,
        pack=pack,
    )
    resolved = validate_plan_proposal(proposal, pack=pack)
    coverage_preview: list[dict[str, object]] = []
    coverage_contract: dict[str, object] | None = None
    understanding_ref: dict[str, object] | None = None
    goal_candidates: list[object] = []
    unknown_count = 0
    capability_gap_count = 0
    disposition_summary: dict[str, object] | None = None
    if (
        proposal.schema_version == PLAN_PROPOSAL_SCHEMA_V11
        and understanding is not None
    ):
        contract = materialize_coverage_contract(
            proposal,
            understanding,
            plan_revision=1,
            template_dispositions=_domain_template_dispositions(proposal),
        )
        coverage_preview = [
            _coverage_preview_entry(item)
            for item in build_plan_function_coverage(contract, plan_revision=1)
        ]
        coverage_contract = coverage_contract_to_payload(contract)
        understanding_ref = {
            "understanding_id": understanding.understanding_id,
            "revision": understanding.revision,
        }
        summary = understanding_summary(understanding)
        raw_goals = summary.get("goal_candidates", [])
        goal_candidates = list(raw_goals) if isinstance(raw_goals, list) else []
        raw_unknown = summary.get("unknown_count", 0)
        raw_gaps = summary.get("capability_gap_count", 0)
        unknown_count = raw_unknown if type(raw_unknown) is int else 0
        capability_gap_count = raw_gaps if type(raw_gaps) is int else 0
        disposition_summary = {
            "applicable": sum(
                1 for item in proposal.template_dispositions
                if item.applicability == "applicable"
            ),
            "not_applicable": sum(
                1 for item in proposal.template_dispositions
                if item.applicability == "not_applicable"
            ),
            "unknown": sum(
                1 for item in proposal.template_dispositions
                if item.applicability == "unknown"
            ),
            "covered": sum(
                1 for item in proposal.template_dispositions
                if item.coverage == "covered"
            ),
            "uncovered": sum(
                1 for item in proposal.template_dispositions
                if item.coverage == "uncovered"
            ),
            "deferred": sum(
                1 for item in proposal.template_dispositions
                if item.coverage == "deferred"
            ),
            "items": [
                {
                    "template_item_id": item.template_item_id,
                    "applicability": item.applicability,
                    "coverage": item.coverage,
                    "reason": item.reason,
                }
                for item in proposal.template_dispositions
            ],
        }
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
        "coverage_preview": coverage_preview,
        "coverage_contract": coverage_contract,
        "understanding_ref": understanding_ref,
        "goal_candidates": goal_candidates,
        "template_disposition_summary": disposition_summary,
        "unknown_count": unknown_count,
        "capability_gap_count": capability_gap_count,
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
    "PLAN_PROPOSAL_SCHEMA_V11",
    "PLAN_PROPOSAL_SCHEMAS",
    "PlanProposalError",
    "ProposalIssue",
    "materialize_coverage_contract",
    "materialize_plan_proposal",
    "parse_plan_proposal",
    "plan_proposal_system_prompt",
    "proposal_preview",
    "proposal_from_mapping",
    "strip_code_fence",
    "validate_coverage_contract_against_understanding",
    "validate_plan_proposal",
]
