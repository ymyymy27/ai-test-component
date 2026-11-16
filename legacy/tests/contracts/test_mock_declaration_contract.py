"""Mock declaration input contract: static hints cannot become verified facts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aitest.contracts.mock_declaration import MockDeclarationInput


def _item(**overrides: object) -> MockDeclarationInput:
    values: dict[str, object] = {
        "mock_declaration_id": "mock-1",
        "declaration_source": "development_declaration",
        "replaced_object_ref": "module:payment",
        "replaced_behavior_ref": "charge",
        "scope": "payment",
        "verification_state": "unverified",
    }
    values.update(overrides)
    return MockDeclarationInput(**values)  # type: ignore[arg-type]


def test_unverified_declaration_needs_no_evidence() -> None:
    assert _item().verification_state == "unverified"


def test_static_candidate_cannot_claim_verification() -> None:
    with pytest.raises(ValidationError, match="static Mock candidate"):
        _item(declaration_source="static_candidate", verification_state="verified")


def test_verified_declaration_requires_conclusion_and_evidence() -> None:
    with pytest.raises(ValidationError, match="conclusion"):
        _item(verification_state="verified")
    with pytest.raises(ValidationError, match="evidence"):
        _item(
            verification_state="verified",
            verification_conclusion_ref="conclusion-1",
        )


def test_complete_verified_declaration_is_accepted() -> None:
    item = _item(
        verification_state="partial",
        verification_conclusion_ref="conclusion-1",
        evidence_refs=("evidence-1",),
    )
    assert item.verification_state == "partial"
