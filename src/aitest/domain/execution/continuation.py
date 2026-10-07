"""Stable, saved scheduling identity; an active flag grants no execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass


def continuation_id(workspace: str, project: str, run: str) -> str:
    raw = json.dumps([workspace, project, run], separators=(",", ":"), ensure_ascii=True)
    return "run-work-" + hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class RunContinuation:
    workspace_id: str
    project_id: str
    run_id: str
    schedule_intent_id: str
    intent_id: str
    base_snapshot_commit_id: str
    active: bool

    @property
    def record_id(self) -> str:
        return continuation_id(self.workspace_id, self.project_id, self.run_id)

    def payload(self) -> dict[str, object]:
        return {"schema_version": "aitest.run-continuation/1.0", **asdict(self)}

    @classmethod
    def read(cls, payload: Mapping[str, object]) -> RunContinuation:
        fields = set(cls.__dataclass_fields__)
        if (
            set(payload) != {"schema_version", *fields}
            or payload["schema_version"] != "aitest.run-continuation/1.0"
            or type(payload["active"]) is not bool
            or any(not _identity_text(payload[key]) for key in fields - {"active"})
        ):
            raise ValueError("saved continuation has invalid fields or identity")
        values = {key: payload[key] for key in fields}
        # The strict shape/type guard precedes constructing the immutable state.
        return cls(**values)  # type: ignore[arg-type]


def _identity_text(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= 128 and bool(value.strip())
