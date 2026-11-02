"""证据正文只读视图（read_view view=evidence）：门控与白名单元数据。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from aitest.interfaces.local.view_queries import CommittedViewReader

PROJECT = "p1"
EVIDENCE = "evidence-1"


class _Reader:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> Any:
        assert aggregate_kind == "evidence_ref"
        return SimpleNamespace(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            revision=revision,
            payload=self.payload,
        )


class _Objects:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read_bytes(self, ref: Any) -> bytes:
        return self.data


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "evidence_id": EVIDENCE,
        "evidence_revision": 1,
        "project_id": PROJECT,
        "run_id": "r1",
        "step_id": "s1",
        "attempt_id": "a1",
        "source_instance_id": "i1",
        "evidence_kind": "command_output",
        "capture_source": "plugin_runtime",
        "object_digest": "sha256:" + "0" * 64,
        "object_size": 5,
        "integrity": "complete",
        "redaction_state": "not_required",
        "projection_state": "safe",
        "redaction_summary_ref": None,
        "evidence_level": "full",
        "gap_ids": [],
        "media_type": "text/plain",
        "created_at": "2026-11-01T00:00:00+00:00",
        "code_identity": {"workspace_ref": "E:/secret/path"},
    }
    base.update(overrides)
    return base


def _reader(payload: dict[str, Any], objects: Any) -> CommittedViewReader:
    return CommittedViewReader(
        decisions=object(), reader=_Reader(payload), objects=objects  # type: ignore[arg-type]
    )


def _view(payload: dict[str, Any], objects: Any) -> dict[str, Any]:
    return _reader(payload, objects).evidence_view(
        project_id=PROJECT, record_id=EVIDENCE, revision=1
    )


def test_safe_evidence_returns_body_and_strips_code_identity() -> None:
    view = _view(_payload(), _Objects(b"hello"))

    assert view["body"] == "hello"
    assert view["record_kind"] == "evidence"
    assert "code_identity" not in view["record"]
    assert view["record"]["evidence_id"] == EVIDENCE


def test_unsafe_redaction_returns_metadata_only() -> None:
    view = _view(_payload(redaction_state="blocked"), _Objects(b"hello"))

    assert "body" not in view
    assert view["body_omitted_reason"] == "redaction_unsafe"


def test_missing_object_store_degrades_to_metadata() -> None:
    view = _view(_payload(), None)

    assert view["body_omitted_reason"] == "object_unavailable"


def test_oversized_body_is_not_returned() -> None:
    view = _view(_payload(object_size=5 * 1024 * 1024), _Objects(b"x" * 10))

    assert view["body_omitted_reason"] == "body_too_large"


def test_size_mismatch_reports_corrupted_object() -> None:
    view = _view(_payload(object_size=99), _Objects(b"hello"))

    assert view["body_omitted_reason"] == "object_corrupted"


def test_binary_material_is_not_rendered() -> None:
    view = _view(_payload(object_size=2), _Objects(b"\xff\xfe"))

    assert view["body_omitted_reason"] == "binary_content"


def test_cross_project_evidence_is_rejected() -> None:
    with pytest.raises(ValueError):
        _reader(_payload(project_id="other"), _Objects(b"hello")).evidence_view(
            project_id=PROJECT, record_id=EVIDENCE, revision=1
        )
