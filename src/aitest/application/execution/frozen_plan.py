"""Exact original plan/scope/cases shared by execution and history consumers."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from aitest.application.planning.publish import _plan_payload, payload_digest
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    acceptance_scope_to_payload,
    case_content_digest,
    case_from_payload,
    case_to_payload,
)
from aitest.application.ports import RecordRepository
from aitest.contracts.prepared_run import PreparedRun
from aitest.domain.planning.plans import Case, Plan


@dataclass(frozen=True, slots=True)
class FrozenRunPlan:
    plan: Plan
    cases: tuple[Case, ...]


class FrozenRunPlanReader:
    def __init__(self, records: RecordRepository) -> None:
        self.records = records

    def _read(self, project: str, kind: str, identity: str, revision: int) -> dict[str, Any]:
        if type(revision) is not int or revision < 1:
            raise ValueError("frozen plan requires an exact warehouse revision")
        saved = self.records.read(aggregate_kind=kind, record_id=identity, revision=revision)
        raw = getattr(saved, "payload", None)
        if (
            (getattr(saved, "aggregate_kind", None), getattr(saved, "record_id", None),
             getattr(saved, "revision", None)) != (kind, identity, revision)
            or type(getattr(saved, "revision", None)) is not int
            or not isinstance(raw, Mapping)
            or raw.get("project_id") != project
        ):
            raise ValueError("frozen plan material envelope or project differs")
        return dict(raw)

    def read(self, prepared: PreparedRun) -> FrozenRunPlan:
        project, ref = prepared.project_id, prepared.plan_revision
        raw = self._read(project, "plan", ref.revision_id, ref.revision_no)
        if payload_digest(raw) != ref.digest or raw.get("plan_id") != ref.revision_id:
            raise ValueError("frozen plan content differs from its exact reference")
        if prepared.scope_id is None:
            raise ValueError("frozen plan has no original scope identity")
        scope_raw = self._read(
            project, "acceptance_scope", prepared.scope_id, prepared.acceptance_scope_revision,
        )
        scope = acceptance_scope_from_payload(scope_raw)
        if (
            payload_digest(scope_raw) != payload_digest(acceptance_scope_to_payload(
                scope, project_id=project,
            ))
            or (scope.scope_id, scope.revision)
            != (prepared.scope_id, prepared.acceptance_scope_revision)
            or (raw.get("scope_id"), raw.get("scope_revision"))
            != (scope.scope_id, scope.revision)
        ):
            raise ValueError("frozen plan scope differs from its exact saved body")
        # This status is a read projection of an already published record. It is
        # never a substitute for the caller's separate controlled-origin checks.
        plan = TypeAdapter(Plan).validate_json(json.dumps({
            "plan_id": ref.revision_id, "revision": raw["revision"],
            "record_revision": ref.revision_no,
            "scope": json.loads(TypeAdapter(type(scope)).dump_json(scope)),
            "case_revisions": raw["case_revisions"], "rule_revisions": raw["rule_revisions"],
            "template_versions": raw["template_versions"], "run_tier": raw["run_tier"],
            "initial_driver": raw["initial_driver"], "status": "published",
            "confirmation_id": raw["approval_commit_seq"],
        }, allow_nan=False), strict=True)
        for name, references in (
            ("case_revisions", prepared.case_revisions),
            ("rule_revisions", prepared.rule_versions),
            ("template_versions", prepared.template_versions),
        ):
            if payload_digest(raw[name]) != payload_digest([
                item.model_dump(mode="json") for item in references
            ]):
                raise ValueError("frozen plan references differ from the original preparation")
        cases = []
        for case_ref in plan.case_revisions:
            body = self._read(project, "case", case_ref.case_id, case_ref.revision)
            case = case_from_payload(body)
            if (
                (case.case_id, case.revision) != (case_ref.case_id, case_ref.revision)
                or payload_digest(body) != payload_digest(case_to_payload(case, project_id=project))
                or case_content_digest(case, project_id=project) != case_ref.digest
            ):
                raise ValueError("frozen plan case differs from its exact saved body")
            cases.append(case)
        # Preserve the published case order, which may differ from the order of
        # its references; order is part of the original publication digest.
        by_id = {case.case_id: case for case in cases}
        ordered = tuple(by_id[item["case_id"]] for item in raw["cases"])
        if len(ordered) != len(cases) or len({case.case_id for case in ordered}) != len(cases):
            raise ValueError("frozen plan case summaries do not cover its unique case references")
        expected = _plan_payload(plan, ordered, project_id=project)
        if payload_digest({key: raw.get(key) for key in expected}) != payload_digest(expected):
            raise ValueError("frozen plan projection differs from its original saved material")
        return FrozenRunPlan(plan, tuple(cases))
