"""Default publication must prove an exact controlled gesture and saved material."""

from dataclasses import replace

import pytest

from aitest.application.planning.publish import payload_digest
from aitest.contracts.commands import Command
from aitest.contracts.prepared_run import PlanRevisionRef, RuleVersionRef
from aitest.infrastructure.security import KnownSecretRegistry
from tests.support.controlled_publication import controlled_publication_confirm, prepare_publication
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_binding_controlled_origin import HUMAN, make_core


def test_a_bare_human_label_and_draft_boolean_cannot_publish_rules(tmp_path):
    core, _ = make_core(tmp_path)
    try:
        before = core.unit_of_work.current_commit_sequence()
        result = core.api.dispatch(
            Command(
                action="publish_rules",
                project_id="project",
                request_id="raw-rule-publication",
                intent_id="raw-rule-intent",
                expected_revision=0,
                parameters={
                    "project_revision": 1,
                    "expected_revision": 0,
                    "draft": {
                        "rule_id": "rule",
                        "revision": 7,
                        "scope": "project",
                        "text": "assert exact output",
                        "source": "manual",
                        "steps": ["compare output"],
                        "evidence_requirements": ["actual output"],
                        "confirmed": True,
                    },
                },
            ),
            HUMAN,
        )
        assert result.error is not None and result.error.code == "AWAITING_USER_CONFIRMATION"
        assert core.unit_of_work.repo.current_revision("rule_version", "rule") == 0
        assert core.unit_of_work.current_commit_sequence() == before
    finally:
        core.lifetime_lock.release()


def test_a_bare_human_label_cannot_publish_plan(authoritative):
    core, inputs, _ = authoritative
    repo = core.unit_of_work.repo
    scope = repo.read(
        aggregate_kind="acceptance_scope", record_id=inputs.scope_id, revision=1
    ).payload
    cases = [
        dict(repo.read(aggregate_kind="case", record_id=ref.case_id, revision=ref.revision).payload)
        for ref in inputs.case_revisions
    ]
    before = core.unit_of_work.current_commit_sequence()
    result = core.api.dispatch(
        Command(
            action="publish_plan",
            project_id=inputs.project_id,
            request_id="raw-plan-publication",
            intent_id="raw-plan-intent",
            expected_revision=0,
            parameters={
                "project_revision": 1,
                "expected_revision": 0,
                "plan_id": "untrusted-plan",
                "revision": 7,
                "scope": dict(scope),
                "cases": cases,
            },
        ),
        HUMAN,
    )
    assert result.error is not None and result.error.code == "AWAITING_USER_CONFIRMATION"
    assert repo.current_revision("plan", "untrusted-plan") == 0
    assert core.unit_of_work.current_commit_sequence() == before


def rule_command(project="project"):
    return Command(
        action="publish_rules",
        project_id=project,
        request_id="rule-controlled-write",
        intent_id="rule-controlled-intent",
        expected_revision=0,
        parameters={
            "project_revision": 1,
            "expected_revision": 0,
            "draft": {
                "rule_id": "controlled-rule",
                "revision": 7,
                "scope": "project",
                "text": "compare output",
                "source": "manual",
                "steps": ["compare output"],
                "confirmed": True,
            },
        },
    )


def plan_command(core, inputs):
    repo = core.unit_of_work.repo
    return Command(
        action="publish_plan",
        project_id=inputs.project_id,
        request_id="plan-controlled-write",
        intent_id="plan-controlled-intent",
        expected_revision=0,
        parameters={
            "project_revision": 1,
            "expected_revision": 0,
            "plan_id": "controlled-plan",
            "revision": 7,
            "scope": dict(
                repo.read(
                    aggregate_kind="acceptance_scope", record_id=inputs.scope_id, revision=1
                ).payload
            ),
            "cases": [
                dict(
                    repo.read(
                        aggregate_kind="case", record_id=ref.case_id, revision=ref.revision
                    ).payload
                )
                for ref in inputs.case_revisions
            ],
        },
    )


@pytest.fixture(params=["publish_rules", "publish_plan"])
def publication_stack(request, tmp_path):
    if request.param == "publish_plan":
        core, inputs, _ = request.getfixturevalue("authoritative")
        yield core, plan_command(core, inputs)
    else:
        core, _ = make_core(tmp_path)
        try:
            yield core, rule_command()
        finally:
            core.lifetime_lock.release()


def approved(core, command):
    challenge = prepare_publication(core, command, HUMAN)
    assert challenge.error is None, challenge.error
    return command.model_copy(
        update={
            "parameters": dict(command.parameters)
            | {"approval_challenge_id": challenge.result["challenge_id"]}
        }
    ), challenge.result


def actual_event(core, command, challenge):
    return core.api.dispatch_user_confirmation(
        command,
        HUMAN,
        challenge_id=challenge["challenge_id"],
        input_digest=challenge["basis"]["input_digest"],
    )


def test_controlled_publication_preserves_domain_version_and_six_record_origin(publication_stack):
    core, command = publication_stack
    ready, challenge = approved(core, command)
    before = core.unit_of_work.current_commit_sequence()
    result = actual_event(core, ready, challenge)
    assert result.error is None, result.error
    assert result.result["published"] is True
    assert result.result["revision"] == 7 and result.result["record_revision"] == 1
    assert core.unit_of_work.current_commit_sequence() == before + 6
    kind = result.result["kind"]
    identity = result.result["rule_id"] if kind == "rule_version" else result.result["plan_id"]
    payload = core.unit_of_work.repo.read(
        aggregate_kind=kind, record_id=identity, revision=1
    ).payload
    proof = core.unit_of_work.repo.read(
        aggregate_kind="approval_confirmation",
        record_id=payload["approval_confirmation_id"],
        revision=1,
    ).payload["confirmation"]
    assert payload["approval_commit_seq"] == proof["confirmed_at_commit"] == str(before + 6)
    assert result.result["confirmation_id"] == str(before + 6)
    before = core.unit_of_work.current_commit_sequence()
    replay = core.api.dispatch(command.model_copy(update={"request_id": "read-publication"}), HUMAN)
    assert replay.error is None and replay.result == result.result
    assert core.unit_of_work.current_commit_sequence() == before


@pytest.mark.parametrize("fault", [1, 2, 3, 4, 5, 6, "before_commit"])
def test_publication_fault_keeps_original_challenge_and_effect_unpublished(
    publication_stack, monkeypatch, fault
):
    core, command = publication_stack
    ready, challenge = approved(core, command)
    unit = core.unit_of_work
    stage, commit = unit.stage_record, unit.commit
    before = unit.current_commit_sequence()
    count = 0

    def failed_stage(**kwargs):
        nonlocal count
        count += 1
        if count == fault:
            raise OSError("synthetic publication stage failure")
        return stage(**kwargs)

    def failed_commit(*args, **kwargs):
        if fault == "before_commit":
            raise OSError("synthetic publication publish failure")
        return commit(*args, **kwargs)

    monkeypatch.setattr(unit, "stage_record", failed_stage)
    monkeypatch.setattr(unit, "commit", failed_commit)
    result = actual_event(core, ready, challenge)
    assert result.error is not None
    assert unit.current_commit_sequence() == before
    kind = "rule_version" if command.action == "publish_rules" else "plan"
    identity = (
        command.parameters["draft"]["rule_id"]
        if kind == "rule_version"
        else command.parameters["plan_id"]
    )
    assert unit.repo.current_revision(kind, identity) == 0
    assert unit.repo.current_revision("approval_challenge", challenge["challenge_id"]) == 1
    monkeypatch.setattr(unit, "stage_record", stage)
    monkeypatch.setattr(unit, "commit", commit)
    replay = actual_event(
        core, ready.model_copy(update={"request_id": "retry-publication"}), challenge
    )
    assert replay.error is None and replay.result["published"] is True
    assert unit.current_commit_sequence() == before + 6


def test_publication_lost_reply_is_readable_without_second_event(publication_stack, monkeypatch):
    core, command = publication_stack
    ready, challenge = approved(core, command)
    unit = core.unit_of_work
    commit = unit.commit

    def lost(*args, **kwargs):
        commit(*args, **kwargs)
        raise OSError("synthetic lost publication reply")

    monkeypatch.setattr(unit, "commit", lost)
    result = actual_event(core, ready, challenge)
    assert result.error is not None
    monkeypatch.setattr(unit, "commit", commit)
    before = unit.current_commit_sequence()
    replay = core.api.dispatch(
        command.model_copy(update={"request_id": "lost-publication-read"}), HUMAN
    )
    assert replay.error is None and replay.result["record_revision"] == 1
    assert unit.current_commit_sequence() == before


@pytest.mark.parametrize("damage", ["origin", "body", "parameters", "receipt"])
def test_publication_replay_rejects_damage_to_exact_saved_proof(
    publication_stack, monkeypatch, damage
):
    core, command = publication_stack
    result = controlled_publication_confirm(core, command, session=HUMAN)
    assert result.error is None, result.error
    kind = result.result["kind"]
    identity = result.result["rule_id"] if kind == "rule_version" else result.result["plan_id"]
    read = core.unit_of_work.repo.read

    def damaged(**kwargs):
        record = read(**kwargs)
        raw = dict(record.payload)
        if (record.aggregate_kind, record.record_id) == (kind, identity):
            if damage == "origin":
                raw["approval_confirmation_id"] = "missing-exact-confirmation"
            elif damage == "body":
                raw["text" if kind == "rule_version" else "revision"] = (
                    "changed" if kind == "rule_version" else 999
                )
            elif damage == "parameters":
                raw["approval_parameters"] = dict(raw["approval_parameters"])
                raw["approval_parameters"]["project_revision"] = 999
        if (
            damage == "receipt"
            and raw.get("schema_version") == "aitest.controlled-write-intent/1.0"
        ):
            raw["record_revision"] = True
        return replace(record, payload=raw)

    monkeypatch.setattr(core.unit_of_work.repo, "read", damaged)
    before = core.unit_of_work.current_commit_sequence()
    replay = core.api.dispatch(
        command.model_copy(update={"request_id": "bad-publication-read"}), HUMAN
    )
    assert replay.error is not None and replay.error.code == "AWAITING_USER_CONFIRMATION"
    assert core.unit_of_work.current_commit_sequence() == before


def test_plan_challenge_rejects_client_case_body_with_same_saved_reference(authoritative):
    core, inputs, _ = authoritative
    command = plan_command(core, inputs)
    parameters = dict(command.parameters)
    parameters["cases"] = [dict(case) for case in parameters["cases"]]
    parameters["cases"][0]["expected"] = "invented expectation"
    before = core.unit_of_work.current_commit_sequence()
    result = prepare_publication(core, command.model_copy(update={"parameters": parameters}), HUMAN)
    assert result.error is not None and result.error.code == "AWAITING_USER_CONFIRMATION"
    assert core.unit_of_work.current_commit_sequence() == before


def test_default_prepare_rejects_plan_with_missing_publication_origin(authoritative, monkeypatch):
    core, inputs, _ = authoritative
    read = core.unit_of_work.repo.read

    def legacy(**kwargs):
        record = read(**kwargs)
        if record.aggregate_kind == "plan":
            return replace(
                record,
                payload={
                    key: value
                    for key, value in record.payload.items()
                    if not key.startswith("approval_") and key != "workspace_id"
                },
            )
        return record

    monkeypatch.setattr(core.unit_of_work.repo, "read", legacy)
    before = core.unit_of_work.current_commit_sequence()
    result = prepare(core, inputs, request="unproved-plan-prepare", intent="unproved-plan-intent")
    assert result.error is None and result.result["status"] == "blocked"
    assert core.unit_of_work.current_commit_sequence() == before


def test_plan_cannot_consume_legacy_rule_without_its_own_controlled_publication(authoritative):
    core, inputs, _ = authoritative
    unit = core.unit_of_work
    unit.begin("legacy-rule-record", inputs.project_id, intent_id="legacy-rule-record-intent")
    unit.stage_record(
        aggregate_kind="rule_version",
        record_id="legacy-rule",
        expected_revision=0,
        payload={
            "project_id": inputs.project_id,
            "rule_id": "legacy-rule",
            "revision": 7,
            "scope": "project",
            "text": "compare output",
            "source": "manual",
            "steps": [],
            "evidence_requirements": [],
            "status": "published",
        },
    )
    unit.commit("legacy-rule-record")
    command = plan_command(core, inputs)
    command = command.model_copy(
        update={
            "parameters": dict(command.parameters)
            | {"rule_revisions": [{"rule_id": "legacy-rule", "revision": 1}]}
        }
    )
    before = unit.current_commit_sequence()
    result = prepare_publication(core, command, HUMAN)
    assert result.error is not None and result.error.code == "AWAITING_USER_CONFIRMATION"
    assert unit.current_commit_sequence() == before


def test_controlled_plan_and_rule_prepare_with_warehouse_refs_then_missing_rule_proof_blocks(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    rule = rule_command(inputs.project_id)
    published_rule = controlled_publication_confirm(core, rule, session=HUMAN)
    assert published_rule.error is None, published_rule.error
    assert published_rule.result["revision"] == 7 and published_rule.result["record_revision"] == 1
    command = plan_command(core, inputs)
    command = command.model_copy(
        update={
            "parameters": dict(command.parameters)
            | {"rule_revisions": [{"rule_id": "controlled-rule", "revision": 1}]}
        }
    )
    published_plan = controlled_publication_confirm(core, command, session=HUMAN)
    assert published_plan.error is None, published_plan.error
    repo = core.unit_of_work.repo
    rule_payload = repo.read(
        aggregate_kind="rule_version", record_id="controlled-rule", revision=1
    ).payload
    plan_payload = repo.read(aggregate_kind="plan", record_id="controlled-plan", revision=1).payload
    candidate = replace(
        inputs,
        prepare_request_id="controlled-rule-plan-prepare",
        plan_revision=PlanRevisionRef(
            revision_id="controlled-plan", revision_no=1, digest=payload_digest(plan_payload)
        ),
        rule_versions=(
            RuleVersionRef(
                rule_id="controlled-rule", revision=1, digest=payload_digest(rule_payload)
            ),
        ),
        input_revisions=replace(inputs.input_revisions, plan_revision=1, rules_revision=1),
    )
    prepared = prepare(
        core,
        candidate,
        request="controlled-plan-prepare-request",
        intent="controlled-plan-prepare-intent",
    )
    assert prepared.error is None and prepared.result["status"] == "prepared", prepared
    read = repo.read

    def lost_rule_receipt(**kwargs):
        record = read(**kwargs)
        if (record.payload.get("schema_version"), record.payload.get("action")) == (
            "aitest.controlled-write-intent/1.0",
            "publish_rules",
        ):
            raise KeyError("missing exact rule publication effect")
        return record

    monkeypatch.setattr(repo, "read", lost_rule_receipt)
    before = core.unit_of_work.current_commit_sequence()
    refused = prepare(
        core,
        replace(candidate, prepare_request_id="missing-rule-proof-prepare"),
        request="missing-rule-proof-request",
        intent="missing-rule-proof-intent",
    )
    assert refused.error is None and refused.result["status"] == "blocked", refused
    assert core.unit_of_work.current_commit_sequence() == before


def test_required_context_gap_cannot_be_relabeled_nonblocking(publication_stack):
    core, command = publication_stack
    changed = command.model_copy(
        update={
            "parameters": dict(command.parameters)
            | {
                "context_gaps": [
                    {
                        "kind": "missing_environment_carrier",
                        "subject": "environment",
                        "detail": "required carrier is absent",
                        "blocking": False,
                    }
                ]
            }
        }
    )
    before = core.unit_of_work.current_commit_sequence()
    result = prepare_publication(core, changed, HUMAN)
    assert result.error is not None and result.error.code == "AWAITING_USER_CONFIRMATION"
    assert core.unit_of_work.current_commit_sequence() == before


def test_filtered_controlled_rule_cannot_publish_a_different_frozen_body(tmp_path, monkeypatch):
    core, _ = make_core(tmp_path)
    try:
        registry = KnownSecretRegistry()
        secret = "synthetic-rule-freeze-value"
        registry.register(secret)
        monkeypatch.setattr(core.unit_of_work, "_registry", registry)
        command = rule_command()
        parameters = dict(command.parameters)
        parameters["draft"] = dict(parameters["draft"])
        parameters["draft"]["text"] = "compare output " + secret
        command = command.model_copy(update={"parameters": parameters})
        ready, challenge = approved(core, command)
        before = core.unit_of_work.current_commit_sequence()
        result = actual_event(core, ready, challenge)
        assert result.error is not None
        assert core.unit_of_work.repo.current_revision("rule_version", "controlled-rule") == 0
        assert core.unit_of_work.current_commit_sequence() == before
        assert (
            core.unit_of_work.repo.current_revision("approval_challenge", challenge["challenge_id"])
            == 1
        )
    finally:
        core.lifetime_lock.release()
