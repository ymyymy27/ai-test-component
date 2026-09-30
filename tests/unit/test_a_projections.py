"""Unit tests for the safe material projector (infrastructure/projections.py)."""

from __future__ import annotations

import hashlib

from aitest.application.planning.model_ports import (
    ProjectionStatus,
)
from aitest.domain.planning.model_outbound import MaterialKind
from aitest.infrastructure.projections import (
    ITEM_LIMIT_BYTES,
    SafeMaterialProjector,
    sha256_text,
)


def _project(material: dict[MaterialKind, str], *, snippets: bool = False):
    return SafeMaterialProjector().project(
        material=material, source_snippets_enabled=snippets
    )


def test_clean_material_completes_with_real_digests() -> None:
    projection = _project({MaterialKind.PROJECT_CONTEXT: "hello world"})

    assert projection.status is ProjectionStatus.COMPLETE
    assert len(projection.projected) == 1
    item = projection.projected[0]
    assert item.digest == "sha256:" + hashlib.sha256(b"hello world").hexdigest()
    assert projection.projection_digest.startswith("sha256:")
    assert projection.policy_revision >= 1


def test_overall_digest_is_deterministic() -> None:
    material = {
        MaterialKind.PROJECT_CONTEXT: "a",
        MaterialKind.TEMPLATE_CONTENT: "b",
    }
    first = _project(material)
    second = _project(material)
    assert first.projection_digest == second.projection_digest


def test_source_snippet_excluded_when_switch_off() -> None:
    projection = _project(
        {MaterialKind.SOURCE_SNIPPET: "def test(): pass"}, snippets=False
    )
    assert projection.status is ProjectionStatus.PARTIAL
    assert projection.excluded == (
        (MaterialKind.SOURCE_SNIPPET, "material.source_snippet"),
    )
    assert projection.projected == ()


def test_source_snippet_included_when_switch_on() -> None:
    projection = _project(
        {MaterialKind.SOURCE_SNIPPET: "def test(): pass"}, snippets=True
    )
    assert projection.status is ProjectionStatus.COMPLETE
    assert projection.projected[0].material_kind is MaterialKind.SOURCE_SNIPPET


def test_credential_line_dropped_other_lines_kept() -> None:
    projection = _project(
        {MaterialKind.CASE_CONTENT: "useful line\napi_key=abc123\nsecond line"}
    )
    assert projection.status is ProjectionStatus.COMPLETE
    text = projection.projected[0].projected_text
    assert "api_key=abc123" not in text
    assert "useful line" in text
    assert "second line" in text


def test_only_credential_lines_excludes_item() -> None:
    projection = _project({MaterialKind.CASE_CONTENT: "password=hunter2"})
    assert projection.status is ProjectionStatus.PARTIAL
    assert (MaterialKind.CASE_CONTENT, "material.case_content") in projection.excluded


def test_non_printable_control_chars_excluded() -> None:
    projection = _project({MaterialKind.CASE_CONTENT: "binary\x00\x01text"})
    assert projection.status is ProjectionStatus.PARTIAL
    assert (MaterialKind.CASE_CONTENT, "material.case_content") in projection.excluded


def test_oversize_material_excluded() -> None:
    oversized = "x" * (ITEM_LIMIT_BYTES + 1)
    projection = _project({MaterialKind.CASE_CONTENT: oversized})
    assert projection.status is ProjectionStatus.PARTIAL
    assert projection.excluded == (
        (MaterialKind.CASE_CONTENT, "material.case_content"),
    )


def test_empty_material_is_partial() -> None:
    projection = _project({})
    assert projection.status is ProjectionStatus.PARTIAL
    assert projection.projected == ()
    assert projection.excluded != ()


def test_sha256_text_helper() -> None:
    assert sha256_text("abc") == "sha256:" + hashlib.sha256(b"abc").hexdigest()
