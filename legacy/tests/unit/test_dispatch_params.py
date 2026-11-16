"""共享参数解析原语的契约（从 registry 抽出后必须保持既有错误文案）。"""

from __future__ import annotations

from enum import StrEnum
from types import SimpleNamespace

import pytest

from aitest.application.dispatch.errors import BUseCaseError
from aitest.application.dispatch.params import (
    _as_text,
    _enum_of,
    _int_of,
    _payload_revision_or_none,
    _required,
    _revision_of,
    _stage_result,
)


class _Kind(StrEnum):
    A = "a"
    B = "b"


def test_text_and_required_reject_missing_or_blank_values() -> None:
    with pytest.raises(BUseCaseError, match="name must be a non-empty string"):
        _as_text("  ", "name")
    with pytest.raises(BUseCaseError, match="missing required parameter: name"):
        _required({}, "name")


def test_enum_error_lists_allowed_values() -> None:
    with pytest.raises(BUseCaseError, match="invalid kind: 'c'; allowed: a, b"):
        _enum_of(_Kind, "c", "kind")


def test_revision_rules_distinguish_new_from_unknown() -> None:
    assert (
        _payload_revision_or_none(SimpleNamespace(expected_revision=0), default=3, name="r") is None
    )
    assert _payload_revision_or_none(SimpleNamespace(expected_revision=2), default=3, name="r") == 2
    with pytest.raises(BUseCaseError, match="r must be >= 1"):
        _revision_of(0, "r")
    with pytest.raises(BUseCaseError, match="n must be an integer"):
        _int_of(True, "n")


def test_stage_result_reports_only_committed_facts() -> None:
    assert _stage_result(aggregate_kind="case", record_id="c", revision=2) == {
        "aggregate_kind": "case",
        "record_id": "c",
        "revision": 2,
    }
