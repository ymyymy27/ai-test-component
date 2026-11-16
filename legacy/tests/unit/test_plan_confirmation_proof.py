"""One controlled plan publication can prove every per-case assertion basis."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from aitest.application.approval_service import ApprovalService
from aitest.application.planning.basis_confirmation import BasisConfirmationService
from aitest.application.planning.basis_proof import SavedCaseBasisReader
from aitest.application.planning.substrate_adapter import PortsRecordReader
from aitest.domain.approvals import ApprovalRequired, EntryKind, TrustedActor
from tests.unit.test_authoritative_preparation import authoritative as authoritative


def _actor_context(core, project_id: str):
    actor = TrustedActor(
        workspace_id=core.workspace.workspace_id,
        project_id=project_id,
        session_id="plan-proof-session",
        entry_kind=EntryKind.HUMAN_UI,
        interactive=True,
    )
    context = Mock()
    context.current.return_value = actor
    return context


def _approval_service(core, project_id: str) -> ApprovalService:
    return ApprovalService(
        unit=core.unit_of_work,
        records=core.unit_of_work.repo,
        actors=_actor_context(core, project_id),
        resolver=Mock(),
        identities=Mock(),
        clock=Mock(),
        workspace_id=core.workspace.workspace_id,
    )


def _exact_material(core, inputs):
    plan = core.unit_of_work.repo.read(
        aggregate_kind="plan",
        record_id=inputs.plan_revision.revision_id,
        revision=1,
    )
    case_id = inputs.assertion_bases[0].case_id
    case = core.unit_of_work.repo.read(aggregate_kind="case", record_id=case_id, revision=1)
    raw = plan.payload
    basis = case.payload["assertion_basis"]
    payload = {
        "plan_confirmation_id": raw["approval_confirmation_id"],
        "plan_id": raw["plan_id"],
        "plan_revision": plan.revision,
        "confirmed_at_commit": raw["approval_commit_seq"],
        "case_id": case.payload["case_id"],
        "case_revision": case.payload["revision"],
        "basis_revision": basis["revision"],
        "basis_text_digest": basis["text_digest"],
    }
    return raw, payload


@pytest.fixture
def exact(authoritative):
    core, inputs, _ = authoritative
    raw, payload = _exact_material(core, inputs)
    return core, inputs, raw, payload


def test_plan_publication_proves_every_case_basis_and_reader_recalls_it(exact):
    core, inputs, raw, payload = exact
    project_id = inputs.project_id
    approvals = _approval_service(core, project_id)
    proofs = BasisConfirmationService(
        reader=PortsRecordReader(core.unit_of_work.repo),
        unit=core.unit_of_work,
        approvals=approvals,
    )

    result = proofs.confirm_with_plan(
        request_id="plan-basis-request",
        intent_id="plan-basis-intent",
        project_id=project_id,
        case_id=payload["case_id"],
        case_revision=payload["case_revision"],
        basis_revision=payload["basis_revision"],
        basis_text_digest=payload["basis_text_digest"],
        plan_confirmation_id=payload["plan_confirmation_id"],
        plan_id=payload["plan_id"],
        plan_revision=payload["plan_revision"],
    )

    saved = core.unit_of_work.repo.read(
        aggregate_kind="case_link", record_id=result["confirmation_id"], revision=1
    )
    assert saved.payload["plan_confirmation_id"] == raw["approval_confirmation_id"]
    assert saved.payload["plan_id"] == raw["plan_id"]
    assert saved.payload["plan_revision"] == 1
    assert saved.payload["confirmed_at_commit"] == raw["approval_commit_seq"]
    assert "approval_confirmation_id" not in saved.payload

    reader = SavedCaseBasisReader(
        PortsRecordReader(core.unit_of_work.repo), approvals
    )
    recalled = reader.read(
        project_id=project_id, confirmation_id=result["confirmation_id"]
    )
    assert recalled.confirmation_id == result["confirmation_id"]
    assert recalled.confirmed_at_commit == raw["approval_commit_seq"]


def test_plan_proof_rejects_wrong_confirmation_commit_case_or_basis(exact):
    core, inputs, _raw, payload = exact
    approvals = _approval_service(core, inputs.project_id)

    for key, value in (
        ("plan_confirmation_id", "another-confirmation"),
        ("plan_id", "another-plan"),
        ("plan_revision", 2),
        ("confirmed_at_commit", "another-commit"),
        ("case_id", "another-case"),
        ("case_revision", 2),
        ("basis_revision", 2),
        ("basis_text_digest", "sha256:another-basis"),
    ):
        damaged = dict(payload) | {key: value}
        with pytest.raises(ApprovalRequired):
            approvals.validate_plan_basis_confirmation(
                project_id=inputs.project_id, payload=damaged
            )


def test_plan_proof_replay_is_idempotent_and_changed_plan_conflicts(exact):
    core, inputs, _raw, payload = exact
    project_id = inputs.project_id
    proofs = BasisConfirmationService(
        reader=PortsRecordReader(core.unit_of_work.repo),
        unit=core.unit_of_work,
        approvals=_approval_service(core, project_id),
    )
    kwargs = {
        "intent_id": "plan-basis-replay",
        "project_id": project_id,
        "case_id": payload["case_id"],
        "case_revision": payload["case_revision"],
        "basis_revision": payload["basis_revision"],
        "basis_text_digest": payload["basis_text_digest"],
        "plan_confirmation_id": payload["plan_confirmation_id"],
        "plan_id": payload["plan_id"],
        "plan_revision": payload["plan_revision"],
    }
    first = proofs.confirm_with_plan(request_id="plan-basis-first", **kwargs)
    second = proofs.confirm_with_plan(request_id="plan-basis-second", **kwargs)
    assert second == first
