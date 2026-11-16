"""C rechecks exact saved preparation and approvals before publishing a new run."""

from dataclasses import replace

import pytest

from aitest.application.execution.registration import InitialRunRegistration
from aitest.application.planning.substrate_adapter import PortsRecordReader
from aitest.application.project.source_analysis import SourceAnalysisService
from aitest.contracts.prepared_run import AssertionBasisStateFact, ConfirmationRef
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_default_basis_confirmation import confirm
from tests.unit.test_initial_run_registration import register, service


def expect_no_registration(core, prepared):
    before = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="prepar|basis|confirmation|origin|proof|registration"):
        register(core, prepared)
    assert core.unit_of_work.current_commit_sequence() == before
    assert core.unit_of_work.repo._load()["intents"].get("register-intent") is None


@pytest.mark.parametrize("action", ["publish_plan", "save_binding"])
def test_lost_origin_receipt_blocks_new_initial_registration(authoritative, monkeypatch, action):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    read = core.unit_of_work.repo.read

    def lost(**kwargs):
        saved = read(**kwargs)
        if (saved.payload.get("schema_version"), saved.payload.get("action")) == (
            "aitest.controlled-write-intent/1.0",
            action,
        ):
            raise KeyError("exact controlled effect receipt is lost")
        return saved

    monkeypatch.setattr(core.unit_of_work.repo, "read", lost)
    expect_no_registration(core, prepared)


def test_initial_registration_requires_proof_ports_before_actual_source_read(authoritative):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    component = InitialRunRegistration(
        unit=core.unit_of_work,
        reader=PortsRecordReader(core.unit_of_work.repo),
        records=core.unit_of_work.repo,
        workspace_id=core.workspace.workspace_id,
        source_analysis=service(core).source_analysis,
    )
    before = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="proof|confirmation"):
        component.register(
            project_id=inputs.project_id,
            prepared_run_id=prepared["prepared_run_id"],
            request_id="missing-proof-request",
            intent_id="missing-proof-intent",
        )
    assert core.unit_of_work.current_commit_sequence() == before


@pytest.mark.parametrize("damage", ["missing", "cancelled", "input_digest"])
def test_preparation_receipt_is_consumed_with_the_saved_dto(authoritative, monkeypatch, damage):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    read = core.unit_of_work.repo.read

    def damaged(**kwargs):
        saved = read(**kwargs)
        if saved.aggregate_kind == "preparation_record":
            if damage == "missing":
                raise KeyError("exact preparation receipt is missing")
            payload = dict(saved.payload)
            payload["cancelled" if damage == "cancelled" else "payload_hash"] = (
                True if damage == "cancelled" else "sha256:different-input"
            )
            return replace(saved, payload=payload)
        return saved

    monkeypatch.setattr(core.unit_of_work.repo, "read", damaged)
    expect_no_registration(core, prepared)


@pytest.mark.parametrize("damage", ["record_id", "boolean_revision"])
def test_saved_preparation_envelope_must_match_exact_warehouse_read(
    authoritative, monkeypatch, damage
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    read = core.unit_of_work.repo.read

    def damaged(**kwargs):
        saved = read(**kwargs)
        if saved.aggregate_kind == "prepared_run":
            return replace(
                saved,
                **(
                    {"record_id": "another-preparation"}
                    if damage == "record_id"
                    else {"revision": True}
                ),
            )
        return saved

    monkeypatch.setattr(core.unit_of_work.repo, "read", damaged)
    expect_no_registration(core, prepared)


@pytest.mark.parametrize("damage", ["saved_dto", "plan_origin"])
def test_saved_dto_must_be_reread_and_approval_rechecked_after_actual_source(
    authoritative, monkeypatch, damage
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    original_check, read = SourceAnalysisService.check, core.unit_of_work.repo.read

    def checked(self, **kwargs):
        result = original_check(self, **kwargs)

        def damaged(**read_kwargs):
            saved = read(**read_kwargs)
            if damage == "saved_dto" and saved.aggregate_kind == "prepared_run":
                return replace(saved, payload=dict(saved.payload) | {"created_at_commit": "999999"})
            if damage == "plan_origin" and (
                saved.payload.get("schema_version"),
                saved.payload.get("action"),
            ) == ("aitest.controlled-write-intent/1.0", "publish_plan"):
                raise KeyError("plan origin was lost during actual source reading")
            return saved

        monkeypatch.setattr(core.unit_of_work.repo, "read", damaged)
        return result

    monkeypatch.setattr(SourceAnalysisService, "check", checked)
    expect_no_registration(core, prepared)


@pytest.mark.parametrize("damage", ["intent", "prepared_run", "boolean_revision", "envelope"])
def test_original_registration_receipt_has_exact_intent_preparation_and_envelope(
    authoritative, monkeypatch, damage
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    original = register(core, prepared)
    read = core.unit_of_work.repo.read

    def damaged(**kwargs):
        saved = read(**kwargs)
        if saved.aggregate_kind == "execution_intent" and saved.payload.get("schema_version") == (
            "aitest.run-registration-intent/1.0"
        ):
            if damage == "envelope":
                return replace(saved, record_id="another-original-intent")
            payload = dict(saved.payload)
            key, value = {
                "intent": ("intent_id", "another-registration-intent"),
                "prepared_run": ("prepared_run_id", "another-prepared-run"),
                "boolean_revision": ("snapshot_revision", True),
            }[damage]
            payload[key] = value
            return replace(saved, payload=payload)
        return saved

    monkeypatch.setattr(core.unit_of_work.repo, "read", damaged)
    before = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="registration|original|envelope"):
        register(core, prepared, request="damaged-original-recall")
    assert core.unit_of_work.current_commit_sequence() == before
    assert original.run.control_state.value == "not_started"


@pytest.mark.parametrize("kind", ["execution_facts", "run", "step", "step_revision"])
def test_original_registration_requires_exact_saved_snapshot_run_step_and_content(
    authoritative, monkeypatch, kind
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    original = register(core, prepared)
    read = core.unit_of_work.repo.read

    def wrong_envelope(**kwargs):
        saved = read(**kwargs)
        return replace(saved, revision=True) if saved.aggregate_kind == kind else saved

    monkeypatch.setattr(core.unit_of_work.repo, "read", wrong_envelope)
    before = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="registration"):
        register(core, prepared, request="damaged-original-material")
    assert core.unit_of_work.current_commit_sequence() == before
    assert not original.attempts


def test_original_read_keeps_exact_history_after_both_publication_proofs_are_lost(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    original = register(core, prepared)
    read = core.unit_of_work.repo.read

    def lost(**kwargs):
        saved = read(**kwargs)
        if saved.payload.get("schema_version") == "aitest.controlled-write-intent/1.0":
            raise KeyError("historic human origin is no longer available for a new registration")
        return saved

    monkeypatch.setattr(core.unit_of_work.repo, "read", lost)
    before = core.unit_of_work.current_commit_sequence()
    assert register(core, prepared, request="historic-read-request") == original
    with pytest.raises(ValueError, match="basis"):
        register(
            core, prepared, request="new-unauthorized-request", intent="new-unauthorized-intent"
        )
    assert core.unit_of_work.current_commit_sequence() == before


def test_preparation_request_hash_reuses_b_contract_for_changed_entry_arguments(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    read = core.unit_of_work.repo.read

    def changed(**kwargs):
        saved = read(**kwargs)
        if saved.aggregate_kind == "prepared_run":
            raw = dict(saved.payload)
            raw["execution_source"] = dict(raw["execution_source"])
            raw["execution_source"]["entry_arguments"] = ["different-entry-argument"]
            return replace(saved, payload=raw)
        return saved

    monkeypatch.setattr(core.unit_of_work.repo, "read", changed)
    expect_no_registration(core, prepared)


@pytest.mark.parametrize("lost", [False, True])
def test_c_initial_registration_consumes_exact_assertion_confirmation_origin(
    authoritative, monkeypatch, lost
):
    core, inputs, _ = authoritative
    confirmed = confirm(core, inputs)
    assert confirmed.error is None, confirmed.error
    payload = confirmed.result
    reference = ConfirmationRef(
        **{
            name: payload[name]
            for name in ("confirmation_id", "case_id", "basis_revision", "confirmed_at_commit")
        }
    )
    inputs = replace(
        inputs,
        assertion_bases=(
            inputs.assertion_bases[0].model_copy(
                update={
                    "assertion_basis_state": AssertionBasisStateFact.CONFIRMED,
                    "confirmation_refs": (reference,),
                }
            ),
            *inputs.assertion_bases[1:],
        ),
    )
    prepared = prepare(core, inputs).result
    assert prepared["status"] == "prepared"
    if lost:
        read = core.unit_of_work.repo.read

        def lost_confirmation(**kwargs):
            saved = read(**kwargs)
            if saved.payload.get("schema_version") == "aitest.action-confirmation/1.0" and (
                saved.payload["confirmation"]["basis"]["action"] == "confirm_basis"
            ):
                raise KeyError("exact assertion confirmation origin is lost")
            return saved

        monkeypatch.setattr(core.unit_of_work.repo, "read", lost_confirmation)
        expect_no_registration(core, prepared)
    else:
        facts = register(core, prepared)
        assert facts.run.control_state.value == "not_started"
        assert facts.run.evidence_level is None
        assert not facts.attempts and not facts.verifications and not facts.evidence_refs


@pytest.mark.parametrize("missing", ["ports", "publish_plan", "save_binding"])
def test_unverified_c_preparation_never_reaches_actual_source_check(
    authoritative, monkeypatch, missing
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    component = service(core)
    if missing == "ports":
        component = InitialRunRegistration(
            unit=core.unit_of_work,
            reader=PortsRecordReader(core.unit_of_work.repo),
            records=core.unit_of_work.repo,
            workspace_id=core.workspace.workspace_id,
            source_analysis=component.source_analysis,
        )
    else:
        read = core.unit_of_work.repo.read

        def lost(**kwargs):
            saved = read(**kwargs)
            if (saved.payload.get("schema_version"), saved.payload.get("action")) == (
                "aitest.controlled-write-intent/1.0",
                missing,
            ):
                raise KeyError("required origin receipt is unavailable")
            return saved

        monkeypatch.setattr(core.unit_of_work.repo, "read", lost)
    calls = []

    def forbidden_source(**kwargs):
        calls.append(kwargs)
        raise AssertionError("source inspection must follow exact C approval checks")

    monkeypatch.setattr(component.source_analysis, "check", forbidden_source)
    before = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="proof|confirmation|basis"):
        component.register(
            project_id=inputs.project_id,
            prepared_run_id=prepared["prepared_run_id"],
            request_id="before-source-request",
            intent_id="before-source-intent",
        )
    assert calls == []
    assert core.unit_of_work.current_commit_sequence() == before
