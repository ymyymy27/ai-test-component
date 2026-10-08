"""Frozen warehouse identity and canonical JSON boundaries; synthetic records."""

from copy import deepcopy
from dataclasses import replace

import pytest

from aitest.application.execution.frozen_plan import FrozenRunPlanReader
from aitest.application.planning.publish import _plan_payload, payload_digest
from aitest.application.planning.serialization import (
    acceptance_scope_to_payload,
    case_content_digest,
    case_to_payload,
)
from aitest.application.ports import CommittedRecord
from aitest.contracts.prepared_run import CaseRevisionRef
from tests.support.prepared_run_factory import build_scenario


@pytest.fixture
def saved():
    scenario = build_scenario("plain")
    project = scenario.prepared_run.project_id
    cases = {case.case_id: case for case in scenario.cases}
    plan = replace(scenario.plan, case_revisions=tuple(replace(
        ref, digest=case_content_digest(cases[ref.case_id], project_id=project),
    ) for ref in scenario.plan.case_revisions))
    raw = {**_plan_payload(plan, scenario.cases, project_id=project),
           "approval_commit_seq": "saved-publication"}
    prepared = scenario.prepared_run.model_copy(update={
        "scope_id": plan.scope.scope_id,
        "case_revisions": tuple(CaseRevisionRef(
            case_id=ref.case_id, revision=ref.revision, digest=ref.digest,
        ) for ref in plan.case_revisions),
        "plan_revision": scenario.prepared_run.plan_revision.model_copy(update={
            "revision_id": plan.plan_id, "revision_no": 1, "digest": payload_digest(raw),
        }),
    })
    records = {
        ("plan", plan.plan_id, 1): CommittedRecord("plan", plan.plan_id, 1, raw),
        ("acceptance_scope", plan.scope.scope_id, plan.scope.revision): CommittedRecord(
            "acceptance_scope", plan.scope.scope_id, plan.scope.revision,
            acceptance_scope_to_payload(plan.scope, project_id=project),
        ),
    }
    records.update({("case", case.case_id, case.revision): CommittedRecord(
        "case", case.case_id, case.revision, case_to_payload(case, project_id=project),
    ) for case in scenario.cases})

    class Repository:
        calls = []

        def read(self, *, aggregate_kind, record_id, revision):
            key = aggregate_kind, record_id, revision
            self.calls.append(key)
            return records[key]

        def current_revision(self, **kwargs):
            pytest.fail("frozen reads cannot look up latest records")

    return prepared, records, Repository(), scenario


def test_reads_original_warehouse_revision_and_preserves_body_version(saved):
    prepared, records, repo, scenario = saved
    key = "plan", prepared.plan_revision.revision_id, 1
    raw = deepcopy(records[key].payload)
    raw["revision"] = 7
    records[key] = replace(records[key], payload=raw)
    prepared = prepared.model_copy(update={"plan_revision": prepared.plan_revision.model_copy(
        update={"digest": payload_digest(raw)},
    )})
    result = FrozenRunPlanReader(repo).read(prepared)
    assert result.plan.revision == 7 and result.plan.record_revision == 1
    assert result.cases == scenario.cases
    assert len(repo.calls) == len(set(repo.calls)) == 2 + len(result.cases)


@pytest.mark.parametrize("kind", ["plan", "acceptance_scope", "case"])
@pytest.mark.parametrize("change", ["owner", "identity", "envelope", "boolean_revision"])
def test_rejects_cross_scope_and_noncanonical_record_types(saved, kind, change):
    prepared, records, repo, _ = saved
    key = next(key for key in records if key[0] == kind)
    record = records[key]
    raw = deepcopy(record.payload)
    if change == "owner":
        raw["project_id"] = "another-project"
    elif change == "identity":
        raw[{"plan": "plan_id", "acceptance_scope": "scope_id", "case": "case_id"}[kind]] = (
            "another-body"
        )
    elif change == "envelope":
        record = replace(record, record_id="another-envelope")
    else:
        raw["revision"] = True
    records[key] = replace(record, payload=raw)
    with pytest.raises(ValueError):
        FrozenRunPlanReader(repo).read(prepared)


@pytest.mark.parametrize("change", ["scope", "cases", "rules", "templates", "summary"])
def test_self_consistent_plan_digest_cannot_substitute_other_frozen_material(saved, change):
    prepared, records, repo, _ = saved
    key = "plan", prepared.plan_revision.revision_id, 1
    raw = deepcopy(records[key].payload)
    if change == "scope":
        raw["scope_id"] = "another-scope"
    elif change == "summary":
        raw["cases"][0]["independent_verification"] = "replaced-method"
    else:
        raw[{"cases": "case_revisions", "rules": "rule_revisions",
             "templates": "template_versions"}[change]] = []
    records[key] = replace(records[key], payload=raw)
    prepared = prepared.model_copy(update={"plan_revision": prepared.plan_revision.model_copy(
        update={"digest": payload_digest(raw)},
    )})
    with pytest.raises(ValueError):
        FrozenRunPlanReader(repo).read(prepared)
