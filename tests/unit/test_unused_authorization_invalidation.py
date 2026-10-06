"""Real file transactions; original action/source validation is an explicit fixture.

The full origin/confirmation tests cover that producer separately. These tests
exercise exact stored states, the current index and rollback without external calls.
"""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.execution.authorization import (
    ExecutionAuthorizationService,
    SavedExecutionAuthorizationResolver,
)
from aitest.application.execution.authorization_index import (
    authorization_index_id,
    index_payload,
    read_authorization_index,
    stage_authorization_index,
)
from aitest.application.execution.start_identity import execution_start_fingerprint
from aitest.domain.execution.authorization import AuthorizationState, ResolvedExecutionAction
from aitest.domain.execution.runs import ConsumedCondition, ConsumedOutput
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.unit.test_serial_runner import _attempt, _request


@pytest.fixture
def indexed_authority(tmp_path, monkeypatch):
    unit = FileUnitOfWork(tmp_path)
    workspace, project, run = "fixture-workspace", "project-1", "run-1"
    resolver = SavedExecutionAuthorizationResolver(unit.repo, workspace)
    service = ExecutionAuthorizationService(
        unit=unit,
        records=unit.repo,
        reader=Mock(),
        workspace_id=workspace,
        resolver=resolver,
        approvals=Mock(),
        source=Mock(),
        environment=Mock(),
        action_resolver=None,
        controlled_writes=Mock(),
    )
    actions = {}
    for identity, step, output, condition in [
        ("own", "step-1", None, None),
        ("alternate", "step-1", None, None),
        ("output", "step-2", "old-upstream", None),
        ("condition", "step-3", None, "invalidated-downstream"),
        ("independent", "step-4", "unchanged-upstream", None),
    ]:
        authorization = replace(
            _request().authorization_ref,
            authorization_id=identity,
            intent_id="intent-" + identity,
            step_id=step,
        )
        request = replace(
            _request(),
            attempt_id="attempt-" + identity,
            intent_id=authorization.intent_id,
            step_id=step,
            authorization_ref=authorization,
            expected_plan_revision_ref=authorization.plan_revision_ref,
        )
        attempt = replace(
            _attempt(),
            attempt_id=request.attempt_id,
            intent_id=request.intent_id,
            step_id=step,
            authorization_ref=authorization,
            expected_plan_revision_ref=request.expected_plan_revision_ref,
            consumed_outputs=(ConsumedOutput(output, "sha256:output", "value"),) if output else (),
            consumed_conditions=(
                ConsumedCondition(condition, "condition-fact", "sha256:condition"),
            )
            if condition
            else (),
        )
        actions[identity] = ResolvedExecutionAction(attempt, request, "environment", "source")
    index = authorization_index_id(workspace, project, run)

    def origin(owner, identity):
        assert owner == project
        action = actions[identity]
        return {
            "schema_version": "aitest.action-authorization/1.1",
            "authorization_index_id": index,
            "parameters": {"execution_action_id": "resolved-" + identity, "record_revision": 1},
        }, action

    monkeypatch.setattr(service, "_origin", origin)
    monkeypatch.setattr(service, "_current_action", Mock())
    monkeypatch.setattr(resolver, "resolve", Mock())
    monkeypatch.setattr(resolver, "read", lambda owner, identity: ({}, actions["own"]))
    unit.begin("seed-current-authority", project)
    for identity in actions:
        unit.stage_record(
            aggregate_kind="execution_authorization",
            record_id="authorization-state:" + identity,
            expected_revision=0,
            payload=service._state_payload(project, identity, AuthorizationState.UNUSED, None),
        )
    stage_authorization_index(unit, workspace, project, run, 0, tuple(sorted(actions)))
    unit.commit("seed-current-authority")
    action = actions["own"]
    attempt = replace(
        action.attempt, intent_digest=execution_start_fingerprint(action.attempt, action.request)
    )
    return unit, service, actions, attempt


def test_new_attempt_revokes_other_unused_authority_for_the_same_step(indexed_authority):
    unit, service, _, attempt = indexed_authority
    unit.begin("new-attempt", "project-1")
    service.stage_occupation(project_id="project-1", attempt=attempt)
    unit.commit("new-attempt")
    assert service._state("project-1", "alternate") == (2, AuthorizationState.REVOKED, None)
    assert service._state("project-1", "own") == (
        2,
        AuthorizationState.OCCUPIED,
        attempt.attempt_id,
    )
    assert read_authorization_index(unit.repo, service.workspace_id, "project-1", "run-1")[1] == (
        "condition",
        "independent",
        "output",
    )


def test_replacement_revokes_exact_outputs_and_conditions_and_keeps_independent_branch(
    indexed_authority,
):
    unit, service, _, attempt = indexed_authority
    unit.begin("replace-upstream", "project-1")
    service.stage_occupation(
        project_id="project-1",
        attempt=attempt,
        superseded_attempt_ids=("old-upstream", "invalidated-downstream"),
    )
    unit.commit("replace-upstream")
    for identity in ("alternate", "output", "condition"):
        assert service._state("project-1", identity) == (2, AuthorizationState.REVOKED, None)
    assert service._state("project-1", "independent") == (1, AuthorizationState.UNUSED, None)
    assert read_authorization_index(unit.repo, service.workspace_id, "project-1", "run-1") == (
        2,
        ("independent",),
    )


@pytest.mark.parametrize(
    "target", ["authorization-state:own", "authorization-state:condition", "index"]
)
def test_occupation_and_affected_revocation_roll_back_together(
    indexed_authority, monkeypatch, target
):
    unit, service, _, attempt = indexed_authority
    before = unit.current_commit_sequence()
    original = unit.stage_record

    def fail(**kwargs):
        if kwargs["record_id"] == target or (
            target == "index" and kwargs["record_id"].startswith("authorization-index-")
        ):
            raise OSError("injected authorization update failure")
        return original(**kwargs)

    monkeypatch.setattr(unit, "stage_record", fail)
    unit.begin("failing-occupation", "project-1")
    with pytest.raises(OSError, match="injected"):
        service.stage_occupation(
            project_id="project-1",
            attempt=attempt,
            superseded_attempt_ids=("old-upstream", "invalidated-downstream"),
        )
    unit.rollback("failing-occupation")
    assert unit.current_commit_sequence() == before
    for identity in ("own", "alternate", "output", "condition", "independent"):
        assert service._state("project-1", identity) == (1, AuthorizationState.UNUSED, None)
    assert read_authorization_index(unit.repo, service.workspace_id, "project-1", "run-1")[0] == 1


def test_runtime_content_revocation_is_current_and_idempotent(indexed_authority):
    unit, service, _, _ = indexed_authority
    unit.begin("runtime-change", "project-1")
    service.stage_revoke_affected(
        project_id="project-1",
        run_id="run-1",
        superseded_attempt_ids=("old-upstream",),
        changed_step_ids=("step-3",),
    )
    unit.commit("runtime-change")
    assert service._state("project-1", "output") == (2, AuthorizationState.REVOKED, None)
    assert service._state("project-1", "condition") == (2, AuthorizationState.REVOKED, None)
    before = unit.current_commit_sequence()
    unit.begin("repeat-runtime-change", "project-1")
    service.stage_revoke_affected(
        project_id="project-1",
        run_id="run-1",
        superseded_attempt_ids=("old-upstream",),
        changed_step_ids=("step-3",),
    )
    unit.rollback("repeat-runtime-change")
    assert unit.current_commit_sequence() == before
    assert read_authorization_index(unit.repo, service.workspace_id, "project-1", "run-1")[1] == (
        "alternate",
        "independent",
        "own",
    )


@pytest.mark.parametrize("change", ["duplicates", "owner", "unknown", "envelope", "missing"])
def test_uncertain_unused_index_blocks_before_any_state_write(
    indexed_authority, monkeypatch, change
):
    unit, service, _, attempt = indexed_authority
    original = unit.repo.read

    def read(**kwargs):
        saved = original(**kwargs)
        if kwargs["record_id"].startswith("authorization-index-"):
            raw = dict(saved.payload)
            if change == "duplicates":
                raw["authorization_ids"] += [raw["authorization_ids"][0]]
            if change == "owner":
                raw["run_id"] = "foreign"
            if change == "unknown":
                raw["unknown"] = True
            if change == "missing":
                raw.pop("authorization_ids")
            return replace(
                saved, payload=raw, revision=True if change == "envelope" else saved.revision
            )
        return saved

    monkeypatch.setattr(unit.repo, "read", read)
    before = unit.current_commit_sequence()
    unit.begin("uncertain-index", "project-1")
    with pytest.raises(ValueError, match="index|identities"):
        service.stage_occupation(project_id="project-1", attempt=attempt)
    unit.rollback("uncertain-index")
    assert unit.current_commit_sequence() == before
    assert service._state("project-1", "own") == (1, AuthorizationState.UNUSED, None)


def test_current_index_rejects_unknown_or_duplicate_identities():
    with pytest.raises(ValueError, match="unique"):
        index_payload("workspace", "project", "run", ("same", "same"))
    with pytest.raises(ValueError, match="verified"):
        index_payload("workspace", "project", "run", ("",))


def test_legacy_unused_authority_requires_new_consent(indexed_authority, monkeypatch):
    unit, service, _, attempt = indexed_authority
    original = service._origin

    def legacy(project, identity):
        grant, action = original(project, identity)
        grant.pop("authorization_index_id")
        grant["schema_version"] = "aitest.action-authorization/1.0"
        return grant, action

    monkeypatch.setattr(service, "_origin", legacy)
    before = unit.current_commit_sequence()
    with pytest.raises(ValueError, match="legacy authorization"):
        service.validate_new(project_id="project-1", attempt=attempt)
    assert unit.current_commit_sequence() == before
    assert service._state("project-1", "own") == (1, AuthorizationState.UNUSED, None)


def test_explicit_revocation_removes_current_membership_and_replays_exactly(indexed_authority):
    unit, service, _, _ = indexed_authority
    before = unit.current_commit_sequence()
    arguments = dict(project_id="project-1", authorization_id="output", intent_id="revoke-output")
    service.revoke(**arguments, request_id="revoke-request")
    assert unit.current_commit_sequence() == before + 3
    assert service._state("project-1", "output") == (2, AuthorizationState.REVOKED, None)
    assert (
        "output"
        not in read_authorization_index(unit.repo, service.workspace_id, "project-1", "run-1")[1]
    )
    before = unit.current_commit_sequence()
    service.revoke(**arguments, request_id="repeated-revoke-request")
    assert unit.current_commit_sequence() == before
