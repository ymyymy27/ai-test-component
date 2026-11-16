"""证据正文只读视图（read_view view=evidence）：门控与白名单元数据。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aitest.interfaces.local.view_queries import CommittedViewReader, ReadViewError

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


class _MissingReader:
    """记录仓没有该编号/修订时的真实行为：ValueError。"""

    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> Any:
        raise ValueError("unknown revision")


class _Objects:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read_bytes(self, ref: Any) -> bytes:
        return self.data


class _BrokenObjects:
    """对象仓存在但读取失败：视图必须降级为元数据，不能把异常抛给宿主。"""

    def read_bytes(self, ref: Any) -> bytes:
        raise OSError("object store is unavailable")


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


def _reader(
    payload: dict[str, Any], objects: Any, *, workspace_root: Path | None = None
) -> CommittedViewReader:
    return CommittedViewReader(
        decisions=object(),  # type: ignore[arg-type]
        reader=_Reader(payload),
        objects=objects,
        workspace_root=workspace_root,
    )


def _view(
    payload: dict[str, Any], objects: Any, *, workspace_root: Path | None = None
) -> dict[str, Any]:
    return _reader(payload, objects, workspace_root=workspace_root).evidence_view(
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


def test_unreadable_object_degrades_to_metadata() -> None:
    """六类省略原因之一：对象仓读取失败时只返回白名单元数据。"""

    view = _view(_payload(), _BrokenObjects())

    assert "body" not in view
    assert view["body_omitted_reason"] == "object_unreadable"
    assert view["record"]["evidence_id"] == EVIDENCE


def test_binary_material_is_not_rendered() -> None:
    view = _view(_payload(object_size=2), _Objects(b"\xff\xfe"))

    assert view["body_omitted_reason"] == "binary_content"


def test_cross_project_evidence_is_rejected_as_not_found() -> None:
    with pytest.raises(ReadViewError) as error:
        _reader(_payload(project_id="other"), _Objects(b"hello")).evidence_view(
            project_id=PROJECT, record_id=EVIDENCE, revision=1
        )

    # 不暴露“记录是否存在”，统一按不可用处理。
    assert error.value.code == "RECORD_NOT_FOUND"


def test_missing_record_or_revision_is_a_structured_not_found() -> None:
    reader = CommittedViewReader(decisions=object(), reader=_MissingReader())  # type: ignore[arg-type]

    with pytest.raises(ReadViewError) as error:
        reader.evidence_view(project_id=PROJECT, record_id=EVIDENCE, revision=9)

    assert error.value.code == "RECORD_NOT_FOUND"


def test_selector_revision_must_match_saved_evidence_revision() -> None:
    with pytest.raises(ReadViewError) as error:
        _view(_payload(evidence_revision=2), _Objects(b"hello"))

    assert error.value.code == "INTEGRITY_CHECK_FAILED"


def test_absolute_paths_are_projected_from_body(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    body = (
        f"workspace={workspace}\\src\\app.py\n"
        f"home={Path.home()}\\secret.txt\n"
        'file="/home/u/app.py"\n'
        r"unc=\\server\share\file.py" "\n"
        "url=https://example.com/a\n"
        'interpreter="C:\\Program Files\\Python313\\python.exe"'
    )
    view = _view(
        _payload(object_size=len(body.encode("utf-8"))),
        _Objects(body.encode("utf-8")),
        workspace_root=workspace,
    )

    assert str(workspace) not in view["body"]
    assert "<workspace>" in view["body"]
    assert "<home>" in view["body"]
    assert "/home/u/app.py" not in view["body"]
    assert "server\\share" not in view["body"]
    assert "C:\\Program Files" not in view["body"]
    # URL 不是文件绝对路径，不能被误删。
    assert "https://example.com/a" in view["body"]
