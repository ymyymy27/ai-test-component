"""Exact confirmation material and proof invocation; human events are synthetic."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.planning.basis_proof import SavedCaseBasisReader
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import confirmation_to_payload
from aitest.application.ports import CommittedRecord
from aitest.domain.planning.plans import ConfirmationRecord
from tests.unit.test_frozen_run_plan import saved as saved


@pytest.fixture
def material(saved, monkeypatch):
    prepared, records, repo, scenario = saved
    case = scenario.cases[0]
    intent = "controlled-confirmation"
    identity = "confirmation-" + payload_digest([prepared.project_id, intent])[7:]
    value = ConfirmationRecord(identity, case.case_id, case.assertion_basis.revision,
                               case.assertion_basis.text_digest, "17")
    raw = {
        **confirmation_to_payload(value, project_id=prepared.project_id),
        "case_revision": case.revision, "intent_id": intent, "request_id": "confirm-transport",
        "approval_confirmation_id": "saved-controlled-confirmation",
        "input_digest": payload_digest([
            prepared.project_id, case.case_id, case.revision, value.basis_revision,
            value.basis_text_digest,
        ]),
    }
    key = "case_link", identity, 1
    records[key] = CommittedRecord(*key, raw)
    proof = Mock()
    monkeypatch.setattr(repo, "current_revision", lambda **kw: 1)
    return SavedCaseBasisReader(repo, proof), records, value, raw, prepared.project_id


def test_exact_confirmation_keeps_original_case_revision_and_invokes_proof(material):
    reader, records, value, raw, project = material
    original = records["case", value.case_id, 1]
    # A later case does not change the case @1 used by this confirmation.
    records["case", value.case_id, 2] = replace(original, revision=2, payload={
        **original.payload, "revision": 2, "expected": "later assertion",
    })
    assert reader.read(project_id=project, confirmation_id=value.confirmation_id) == value
    reader.proof.validate_basis_confirmation.assert_called_once_with(
        project_id=project, payload=raw,
    )
    assert reader.records.calls == [
        ("case_link", value.confirmation_id, 1), ("case", value.case_id, 1),
    ]


@pytest.mark.parametrize("damage", [
    "project", "identity", "basis_bool", "case_bool", "commit_number", "extra", "legacy",
    "input", "basis_digest", "case_body", "case_digest", "case_envelope",
])
def test_self_consistent_or_wrong_saved_confirmation_cannot_replace_proof(material, damage):
    reader, records, value, raw, project = material
    raw = deepcopy(raw)
    link_key = "case_link", value.confirmation_id, 1
    case_key = "case", value.case_id, 1
    if damage == "project":
        raw["project_id"] = "foreign-project"
    elif damage == "identity":
        raw["confirmation_id"] = "another-confirmation"
    elif damage == "basis_bool":
        raw["basis_revision"] = True
    elif damage == "case_bool":
        raw["case_revision"] = True
    elif damage == "commit_number":
        raw["confirmed_at_commit"] = 17
    elif damage == "extra":
        raw["confirmed"] = True
    elif damage == "legacy":
        raw.pop("approval_confirmation_id")
    elif damage == "input":
        raw["input_digest"] = "sha256:other-input"
    elif damage == "basis_digest":
        raw["basis_text_digest"] = "sha256:other-basis"
        raw["input_digest"] = payload_digest([
            project, value.case_id, 1, 1, raw["basis_text_digest"],
        ])
    elif damage == "case_envelope":
        records[case_key] = replace(records[case_key], revision=True)
    else:
        case = deepcopy(records[case_key].payload)
        if damage == "case_body":
            case["case_id"] = "another-case"
        else:
            case["assertion_basis"]["text"] = "changed text with old digest"
        records[case_key] = replace(records[case_key], payload=case)
    records[link_key] = replace(records[link_key], payload=raw)
    with pytest.raises(ValueError):
        reader.read(project_id=project, confirmation_id=value.confirmation_id)
    assert not reader.proof.mock_calls


@pytest.mark.parametrize("revision", [True, 1.0, "1", 0, 2])
def test_confirmation_requires_exact_immutable_revision_before_read(
    material, monkeypatch, revision,
):
    reader, _, value, _, project = material
    monkeypatch.setattr(reader.records, "current_revision", lambda **kw: revision)
    with pytest.raises(ValueError, match="immutable"):
        reader.read(project_id=project, confirmation_id=value.confirmation_id)
    assert reader.records.calls == []


def test_controlled_proof_failure_and_missing_capability_do_not_create_confirmation(material):
    reader, _, value, _, project = material
    reader.proof.validate_basis_confirmation.side_effect = ValueError(
        "incomplete controlled origin",
    )
    with pytest.raises(ValueError, match="controlled origin"):
        reader.read(project_id=project, confirmation_id=value.confirmation_id)
    reader.proof = None
    with pytest.raises(ValueError, match="reader is unavailable"):
        reader.read(project_id=project, confirmation_id=value.confirmation_id)
