"""共享精确读取原语：只接受请求的那一条记录。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from aitest.application.shared.records import read_exact


@dataclass(frozen=True)
class _Record:
    aggregate_kind: str
    record_id: str
    revision: int
    payload: dict[str, object]


class _Reader:
    def __init__(self, record: _Record) -> None:
        self.record = record

    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> _Record:
        return self.record


def test_read_exact_returns_payload_for_the_requested_record() -> None:
    payload = read_exact(
        _Reader(_Record("case", "c1", 2, {"project_id": "p"})),
        aggregate_kind="case",
        record_id="c1",
        revision=2,
        envelope_error="envelope",
        project_id="p",
    )
    assert payload == {"project_id": "p"}


def test_read_exact_rejects_a_substituted_record_and_foreign_project() -> None:
    with pytest.raises(ValueError, match="envelope"):
        read_exact(
            _Reader(_Record("case", "c1", 3, {"project_id": "p"})),
            aggregate_kind="case",
            record_id="c1",
            revision=2,
            envelope_error="envelope",
        )
    with pytest.raises(ValueError, match="another project"):
        read_exact(
            _Reader(_Record("case", "c1", 2, {"project_id": "q"})),
            aggregate_kind="case",
            record_id="c1",
            revision=2,
            envelope_error="envelope",
            project_id="p",
            project_error="another project",
        )
