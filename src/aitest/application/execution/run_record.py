"""Canonical Run records preserve set meaning without losing strict field checks."""

import json
from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter

from aitest.domain.execution.runs import Run

_RUN = TypeAdapter(Run)
_SCOPES = ("required_scope", "selected_scope")


def run_record_payload(run: Run) -> dict[str, Any]:
    result: dict[str, Any] = _RUN.dump_python(run, mode="json")
    for name in _SCOPES:
        result[name] = sorted(getattr(run, name))
    return result


def read_run_record(payload: Mapping[str, object]) -> Run:
    normalized = dict(payload)
    for name in _SCOPES:
        items = normalized.get(name)
        if (
            not isinstance(items, list)
            or any(type(item) is not str or not item.strip() for item in items)
            or len(items) != len(set(items))
        ):
            raise ValueError("run scope must contain unique saved text identities")
        normalized[name] = sorted(items)
    run = _RUN.validate_json(json.dumps(normalized), strict=True)
    if normalized != run_record_payload(run):
        raise ValueError("run record has unknown fields or noncanonical frozen material")
    return run
