"""Canonical publication input uses the existing domain gates and exact saved materials."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields

from pydantic import TypeAdapter

from aitest.application.approval_service import _digest
from aitest.application.execution.plan_grant import normalize_grant_scope
from aitest.application.planning.basis_approval import SavedBasisApprovalResolver
from aitest.application.planning.plan_builder import build_plan
from aitest.application.planning.proposal import (
    validate_coverage_contract_against_understanding,
)
from aitest.application.planning.publish import _plan_payload, payload_digest
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    acceptance_scope_to_payload,
    case_from_payload,
    case_to_payload,
    coverage_contract_from_payload,
    coverage_contract_to_payload,
)
from aitest.application.ports import ApprovalRecords
from aitest.application.project.context import ContextGap, blocking_gaps
from aitest.application.project.understanding import understanding_from_payload
from aitest.domain.approvals import ApprovalMaterialRef, ApprovalRequired
from aitest.domain.planning.plans import RunDriver, RunTier, validate_plan_publication
from aitest.domain.planning.rules import RuleDraft, RuleVersion, validate_draft_publication
from aitest.domain.planning.templates import TemplateRef


@dataclass(frozen=True, slots=True)
class PublicationContent:
    aggregate_kind: str
    record_id: str
    parameters: Mapping[str, object]
    payload: Mapping[str, object]
    materials: tuple[ApprovalMaterialRef, ...] = ()


def _refs(raw: object, identity_key: str, revision_key: str) -> list[dict[str, object]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ApprovalRequired("publication references must be a finite list")
    result = []
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {identity_key, revision_key}:
            raise ApprovalRequired("publication reference has unknown fields")
        identity, revision = item[identity_key], item[revision_key]
        if (
            not isinstance(identity, str)
            or not identity
            or (
                (revision_key == "revision" and (type(revision) is not int or revision < 1))
                or (revision_key == "version" and (not isinstance(revision, str) or not revision))
            )
        ):
            raise ApprovalRequired("publication reference identity/revision is unverified")
        result.append(dict(item))
    if len({(r[identity_key], r[revision_key]) for r in result}) != len(result):
        raise ApprovalRequired("publication references must be unique")
    return sorted(result, key=lambda r: str(r[identity_key]))


def publication_content(
    action: str,
    project: str,
    parameters: Mapping[str, object],
    records: ApprovalRecords | None,
    workspace: str,
) -> PublicationContent:
    """Produce one immutable business record; never infer that a caller approved it."""
    controls = {"project_revision", "expected_revision"}
    gaps = parameters.get("context_gaps", [])
    if not isinstance(gaps, list) or any(
        not isinstance(gap, Mapping) or set(gap) - {f.name for f in fields(ContextGap)}
        for gap in gaps
    ):
        raise ApprovalRequired("publication has blocking or unverified context gaps")
    gap_records = tuple(
        TypeAdapter(ContextGap).validate_json(json.dumps(dict(gap)), strict=True) for gap in gaps
    )
    if blocking_gaps(gap_records):
        raise ApprovalRequired("publication has required blocking context gaps")
    gaps = [TypeAdapter(ContextGap).dump_python(gap, mode="json") for gap in gap_records]
    if action == "publish_rules":
        if set(parameters) - (controls | {"draft", "context_gaps"}) or "draft" not in parameters:
            raise ApprovalRequired("rule publication input has unknown or missing fields")
        raw = parameters["draft"]
        if not isinstance(raw, Mapping) or set(raw) - {f.name for f in fields(RuleDraft)}:
            raise ApprovalRequired("rule publication has unknown draft fields")
        draft = TypeAdapter(RuleDraft).validate_json(json.dumps(dict(raw)), strict=True)
        validate_draft_publication(draft, confirmation_id="pending")
        canonical = TypeAdapter(RuleDraft).dump_python(draft, mode="json")
        body = {
            key: canonical[key]
            for key in (
                "rule_id",
                "revision",
                "scope",
                "text",
                "source",
                "steps",
                "evidence_requirements",
                "unknown_extension_fields",
            )
        }
        body.update(project_id=project, status="published")
        return PublicationContent(
            "rule_version",
            draft.rule_id,
            {key: parameters[key] for key in controls} | {"draft": canonical, "context_gaps": gaps},
            body,
        )
    if action != "publish_plan":
        raise ApprovalRequired("this action has no publication adapter")
    if set(parameters) - (
        controls
        | {
            "plan_id",
            "revision",
            "scope",
            "cases",
            "rule_revisions",
            "template_versions",
            "run_tier",
            "initial_driver",
            "context_gaps",
            "plan_grant",
            "coverage_contract",
            "understanding_ref",
        }
    ):
        raise ApprovalRequired("plan publication input has unknown fields")
    raw_grant = parameters.get("plan_grant")
    plan_grant: dict[str, object] | None = None
    if raw_grant is not None:
        if not isinstance(raw_grant, Mapping):
            raise ApprovalRequired("plan grant scope must be a declared object")
        try:
            plan_grant = normalize_grant_scope(raw_grant, require_understanding=False)
        except (TypeError, ValueError) as error:
            raise ApprovalRequired("plan grant scope cannot be verified") from error
    raw_contract = parameters.get("coverage_contract")
    coverage_contract = None
    understanding = None
    understanding_ref: dict[str, object] | None = None
    if raw_contract is not None:
        if not isinstance(raw_contract, Mapping) or records is None:
            raise ApprovalRequired("coverage contract needs its exact saved understanding")
        try:
            coverage_contract = coverage_contract_from_payload(raw_contract)
        except (TypeError, ValueError, KeyError) as error:
            raise ApprovalRequired("coverage contract cannot be verified") from error
        raw_ref = parameters.get("understanding_ref")
        if (
            not isinstance(raw_ref, Mapping)
            or set(raw_ref) != {"understanding_id", "revision"}
            or not isinstance(raw_ref.get("understanding_id"), str)
            or not raw_ref["understanding_id"].strip()
            or type(raw_ref.get("revision")) is not int
            or raw_ref["revision"] < 1
        ):
            raise ApprovalRequired("coverage contract needs an exact understanding reference")
        understanding_id = raw_ref["understanding_id"]
        understanding_revision = raw_ref["revision"]
        try:
            saved_understanding = records.read(
                aggregate_kind="project_understanding",
                record_id=understanding_id,
                revision=understanding_revision,
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ApprovalRequired("the exact project understanding is unavailable") from error
        saved_payload = getattr(saved_understanding, "payload", None)
        if (
            getattr(saved_understanding, "aggregate_kind", None),
            getattr(saved_understanding, "record_id", None),
            getattr(saved_understanding, "revision", None),
        ) != ("project_understanding", understanding_id, understanding_revision) or not isinstance(
            saved_payload, Mapping
        ):
            raise ApprovalRequired("project understanding envelope cannot be verified")
        try:
            understanding = understanding_from_payload(
                saved_payload, project_id=project, workspace_id=workspace
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ApprovalRequired("the saved project understanding is malformed") from error
        understanding_ref = {
            "understanding_id": understanding_id,
            "revision": understanding_revision,
        }
    plan_id, revision, raw_scope, raw_cases = (
        parameters.get("plan_id"),
        parameters.get("revision"),
        parameters.get("scope"),
        parameters.get("cases"),
    )
    if (
        not isinstance(plan_id, str)
        or not plan_id
        or type(revision) is not int
        or revision < 1
        or not isinstance(raw_scope, Mapping)
        or not isinstance(raw_cases, list)
        or not raw_cases
    ):
        raise ApprovalRequired("plan publication inputs cannot be verified")
    scope = acceptance_scope_from_payload(raw_scope)
    scope_payload = acceptance_scope_to_payload(scope, project_id=project)
    if dict(raw_scope) != scope_payload:
        raise ApprovalRequired("plan scope owner or declared fields differ")
    if any(not isinstance(raw, Mapping) for raw in raw_cases):
        raise ApprovalRequired("plan cases must contain only declared objects")
    cases = tuple(sorted((case_from_payload(raw) for raw in raw_cases), key=lambda c: c.case_id))
    case_payloads = [case_to_payload(case, project_id=project) for case in cases]
    if any(not isinstance(raw, Mapping) for raw in raw_cases) or (
        sorted((dict(raw) for raw in raw_cases), key=lambda raw: str(raw.get("case_id")))
        != case_payloads
    ):
        raise ApprovalRequired("plan case owner or declared fields differ")
    if len({case.case_id for case in cases}) != len(cases):
        raise ApprovalRequired("plan cases must be unique")
    if records is None:
        raise ApprovalRequired("plan publication needs exact saved material reads")
    material_reader = SavedBasisApprovalResolver(records, workspace)
    materials = []
    for kind, identity, record_revision, expected in [
        ("acceptance_scope", scope.scope_id, scope.revision, scope_payload),
        *(
            ("case", case.case_id, case.revision, payload)
            for case, payload in zip(cases, case_payloads, strict=True)
        ),
    ]:
        saved = material_reader._read(kind, identity, record_revision, project)
        if saved != expected:
            raise ApprovalRequired("plan input differs from exact saved scope or case")
        materials.append(ApprovalMaterialRef(kind, identity, record_revision, _digest(saved)))
    rule_refs = _refs(parameters.get("rule_revisions"), "rule_id", "revision")
    rules = []
    for ref in rule_refs:
        rule_identity, rule_record_revision = ref["rule_id"], ref["revision"]
        assert isinstance(rule_identity, str) and type(rule_record_revision) is int
        saved = material_reader._read("rule_version", rule_identity, rule_record_revision, project)
        if saved.get("status") != "published":
            raise ApprovalRequired("plan rule is not a published material")
        rule = {
            key: saved.get(key)
            for key in (
                "rule_id",
                "revision",
                "scope",
                "text",
                "steps",
                "evidence_requirements",
                "source",
            )
        }
        rule.update(
            confirmation_id=payload_digest(saved),
            digest=payload_digest(saved),
            record_revision=rule_record_revision,
        )
        rules.append(TypeAdapter(RuleVersion).validate_json(json.dumps(rule), strict=True))
        materials.append(
            ApprovalMaterialRef("rule_version", rule_identity, rule_record_revision, _digest(saved))
        )
    template_refs = _refs(parameters.get("template_versions"), "template_id", "version")
    raw_tier, raw_driver = (
        parameters.get("run_tier", "full"),
        parameters.get("initial_driver", "planned"),
    )
    if not isinstance(raw_tier, str) or not isinstance(raw_driver, str):
        raise ApprovalRequired("plan tier/driver must use declared enum values")
    tier, driver = RunTier(raw_tier), RunDriver(raw_driver)
    if coverage_contract is not None and understanding is not None:
        try:
            validate_coverage_contract_against_understanding(
                coverage_contract,
                understanding,
                plan_revision=revision,
                require_all_functions=tier is RunTier.FULL,
            )
        except (TypeError, ValueError, KeyError) as error:
            raise ApprovalRequired(
                "coverage contract differs from the saved understanding"
            ) from error
    plan = build_plan(
        plan_id=plan_id,
        revision=revision,
        scope=scope,
        cases=cases,
        project_id=project,
        rule_versions=rules,
        template_refs=[
            TemplateRef(str(r["template_id"]), str(r["version"])) for r in template_refs
        ],
        run_tier=tier,
        initial_driver=driver,
        coverage_contract=coverage_contract,
    )
    validate_plan_publication(plan, cases)
    canonical = {key: parameters[key] for key in controls} | {
        "plan_id": plan_id,
        "revision": revision,
        "scope": scope_payload,
        "cases": case_payloads,
        "rule_revisions": rule_refs,
        "template_versions": template_refs,
        "run_tier": tier.value,
        "initial_driver": driver.value,
        "context_gaps": gaps,
    }
    if plan_grant is not None:
        canonical["plan_grant"] = plan_grant
    if coverage_contract is not None:
        canonical["coverage_contract"] = coverage_contract_to_payload(coverage_contract)
        assert understanding_ref is not None
        canonical["understanding_ref"] = understanding_ref
    return PublicationContent(
        "plan", plan_id, canonical, _plan_payload(plan, cases, project_id=project), tuple(materials)
    )
