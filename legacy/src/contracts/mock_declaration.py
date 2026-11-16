"""Versioned Mock declaration input carried by external result import."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from aitest.contracts.prepared_run import ContractModel

MockDeclarationSource = Literal[
    "development_declaration", "runtime_config", "static_candidate"
]
MockVerificationState = Literal[
    "unverified", "verified", "contradicted", "partial", "unknown"
]


class MockDeclarationInput(ContractModel):
    """One declared Mock; static candidates remain hints, never verified facts."""

    mock_declaration_id: str = Field(min_length=1)
    declaration_source: MockDeclarationSource
    replaced_object_ref: str = Field(min_length=1)
    replaced_behavior_ref: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    verification_state: MockVerificationState = "unverified"
    verification_conclusion_ref: str | None = None
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _truthfulness_is_explicit(self) -> Self:
        if self.declaration_source == "static_candidate" and self.verification_state in {
            "verified",
            "contradicted",
            "partial",
        }:
            raise ValueError("a static Mock candidate cannot be a verified fact")
        if self.verification_state in {"verified", "contradicted", "partial"}:
            if not self.verification_conclusion_ref or not self.verification_conclusion_ref.strip():
                raise ValueError("a verified Mock requires a verification conclusion reference")
            if not self.evidence_refs or any(not item.strip() for item in self.evidence_refs):
                raise ValueError("a verified Mock requires evidence references")
        if any(not item.strip() for item in self.evidence_refs):
            raise ValueError("Mock evidence references must not be empty")
        return self


__all__ = ["MockDeclarationInput", "MockDeclarationSource", "MockVerificationState"]
