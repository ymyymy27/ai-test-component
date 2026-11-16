"""Derive pytest failure facts and signature clusters from saved JUnit artifacts.

The saved JUnit XML is permanent evidence. This module only *reads* it and
projects failure facts for views, reports and issue drafts; it never rewrites
the artifact and never invents a location. A failure without a resolvable
frame keeps ``location_unknown``.

A cluster is keyed by ``(step, attempt, signature)`` where the signature is the
failure type, the normalized first message line, and the first project source
frame (or the explicit ``location_unknown`` marker). The same signature inside
one executed scope becomes one issue draft; different signatures stay apart.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from aitest.contracts.execution_facts import (
    EvidenceIntegrityFact,
    EvidenceKindFact,
    ExecutionFacts,
)
from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.domain.execution.junit import JUnitParseError, parse_junit

PYTEST_ISSUE_DRAFT_VERSION = "aitest.pytest-issue-draft/1.0"

#: Stack/system output are diagnostic context, not permanent evidence bytes.
#: Truncation is always explicit so a draft never claims a complete excerpt.
MAX_STACK_CHARS = 8_000
MAX_OUTPUT_CHARS = 4_000
MAX_MESSAGE_CHARS = 2_000

_TEMP_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s\"']*?[\\/](?:Temp|tmp)[\\/][^\s\"']*"
    r"|/(?:tmp|var/folders)/[^\s\"']+)",
    re.IGNORECASE,
)
_MEMORY_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_PARAM_TAIL = re.compile(r"\[[^\[\]]*\]$")


class BytesReader(Protocol):
    def read_bytes(self, ref: StoredObjectRef) -> bytes: ...


@dataclass(frozen=True, slots=True)
class PytestFailureFact:
    """One failed/errored JUnit testcase with its saved evidence identity."""

    node_id: str
    case_id: str
    step_id: str
    attempt_id: str
    evidence_id: str
    failure_type: str | None
    category: str
    message: str | None
    file: str | None
    line: int | None
    suspect_file: str | None
    suspect_line: int | None
    location_unknown: bool
    stack: str
    system_out: str
    system_err: str

    @property
    def location(self) -> str:
        path = self.suspect_file or self.file
        line = self.suspect_line if self.suspect_file else self.line
        if path is None:
            return "location_unknown"
        return f"{path}:{line}" if line else path


@dataclass(frozen=True, slots=True)
class PytestIssueDraftFact:
    """A failure-signature cluster ready for an issue draft."""

    issue_id: str
    signature: str
    title: str
    failure_type: str | None
    category: str
    message: str | None
    case_id: str
    step_id: str
    attempt_id: str
    run_id: str
    locations: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    failures: tuple[PytestFailureFact, ...]
    environment_ref: str = ""
    plan_revision_ref: str = ""
    source_ref: str = ""
    #: Assertion expected/actual are not invented from a bare failure message.
    expected: str | None = None
    actual: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": PYTEST_ISSUE_DRAFT_VERSION,
            "issue_id": self.issue_id,
            "signature": self.signature,
            "failure_type": self.failure_type,
            "category": self.category,
            "message": self.message,
            "case_id": self.case_id,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "environment_ref": self.environment_ref,
            "plan_revision_ref": self.plan_revision_ref,
            "source_ref": self.source_ref,
            "expected": self.expected,
            "actual": self.actual,
            "repro_command": [
                f"python -m pytest --import-mode=importlib {failure.node_id}"
                for failure in self.failures
            ],
            "locations": list(self.locations),
            "evidence_refs": list(self.evidence_refs),
            "failures": [
                {
                    "node_id": item.node_id,
                    "failure_type": item.failure_type,
                    "category": item.category,
                    "message": item.message,
                    "file": item.file,
                    "line": item.line,
                    "suspect_file": item.suspect_file,
                    "suspect_line": item.suspect_line,
                    "location_unknown": item.location_unknown,
                    "stack": item.stack,
                    "system_out": item.system_out,
                    "system_err": item.system_err,
                    "evidence_id": item.evidence_id,
                    "case_id": item.case_id,
                    "step_id": item.step_id,
                    "attempt_id": item.attempt_id,
                }
                for item in self.failures
            ],
        }


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def _first_line(message: str | None) -> str:
    if not message:
        return ""
    line = message.strip().splitlines()[0] if message.strip() else ""
    return " ".join(line.split())[:MAX_MESSAGE_CHARS]


def normalize_failure_message(message: str | None) -> str:
    """Normalize incidental values only; business values stay intact."""

    text = _first_line(message)
    text = _TEMP_PATH.sub("<tmp>", text)
    text = _UUID.sub("<uuid>", text)
    text = _TIMESTAMP.sub("<time>", text)
    text = _MEMORY_ADDRESS.sub("<addr>", text)
    return text


def failure_signature(
    *, failure_type: str | None, category: str, message: str | None,
    suspect_file: str | None, file: str | None,
) -> str:
    """Stable cluster key: type + normalized first line + first project frame."""

    frame = suspect_file or file or "location_unknown"
    material = "\x1f".join((failure_type or category, normalize_failure_message(message), frame))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _artifact_contents(
    facts: ExecutionFacts, objects: BytesReader,
) -> tuple[tuple[str, str, bytes], ...]:
    items: list[tuple[str, str, bytes]] = []
    for evidence in facts.evidence_refs:
        if (
            evidence.evidence_kind is not EvidenceKindFact.ARTIFACT
            or evidence.integrity is not EvidenceIntegrityFact.COMPLETE
            or not (evidence.media_type or "").startswith("application/xml")
            or evidence.gap_ids
        ):
            continue
        try:
            content = objects.read_bytes(
                StoredObjectRef(
                    evidence.project_id,
                    evidence.object_digest,
                    evidence.object_size,
                    evidence.media_type or "application/xml",
                    f"objects/{evidence.project_id}/"
                    f"{evidence.object_digest.removeprefix('sha256:')}",
                )
            )
        except (OSError, KeyError, ValueError):
            continue
        items.append((evidence.evidence_id, evidence.attempt_id, content))
    return tuple(items)


def collect_pytest_failures(
    facts: ExecutionFacts, objects: BytesReader | None,
) -> tuple[PytestFailureFact, ...]:
    """Read saved JUnit artifacts and return failed/errored testcases."""

    if objects is None:
        return ()
    cases = {step.step_id: step.case_id for step in facts.steps}
    current_attempts = getattr(facts, "current_attempt_by_step", None)
    failures: list[PytestFailureFact] = []
    seen: set[tuple[str, str]] = set()
    for evidence_id, attempt_id, content in _artifact_contents(facts, objects):
        if (evidence_id, attempt_id) in seen:
            continue
        seen.add((evidence_id, attempt_id))
        try:
            parsed = parse_junit(content, source_files=frozenset())
        except (JUnitParseError, ValueError, TypeError):
            continue
        for item in parsed.tests:
            if item.outcome not in {"failed", "error"}:
                continue
            attempt = next(
                (value for value in facts.attempts if value.attempt_id == attempt_id),
                None,
            )
            if attempt is None:
                continue
            # Historical attempts stay in the facts, but a report or issue draft
            # describes the step's current result; never mix replaced attempts in.
            if (
                current_attempts is not None
                and current_attempts.get(attempt.step_id) != attempt_id
            ):
                continue
            frame = item.failure_frame
            suspect = item.suspect_source_frame
            failures.append(
                PytestFailureFact(
                    node_id=item.node_id,
                    case_id=cases.get(attempt.step_id, ""),
                    step_id=attempt.step_id,
                    attempt_id=attempt_id,
                    evidence_id=evidence_id,
                    failure_type=item.failure_type,
                    category=item.category,
                    message=item.message,
                    file=(
                        frame.snapshot_path or frame.path if frame is not None else None
                    ),
                    line=frame.line if frame is not None else None,
                    suspect_file=(
                        suspect.snapshot_path or suspect.path
                        if suspect is not None else None
                    ),
                    suspect_line=suspect.line if suspect is not None else None,
                    location_unknown=item.location_unknown,
                    stack=_truncate(item.stack, MAX_STACK_CHARS),
                    system_out=_truncate(item.system_out, MAX_OUTPUT_CHARS),
                    system_err=_truncate(item.system_err, MAX_OUTPUT_CHARS),
                )
            )
    return tuple(failures)


def cluster_pytest_failures(
    project_id: str,
    run_id: str,
    failures: tuple[PytestFailureFact, ...],
) -> tuple[PytestIssueDraftFact, ...]:
    """Group failures by step/attempt/signature with a deterministic identity."""

    groups: dict[tuple[str, str, str], list[PytestFailureFact]] = {}
    signatures: dict[tuple[str, str, str], str] = {}
    for item in failures:
        signature = failure_signature(
            failure_type=item.failure_type,
            category=item.category,
            message=item.message,
            suspect_file=item.suspect_file,
            file=item.file,
        )
        key = (item.step_id, item.attempt_id, signature)
        groups.setdefault(key, []).append(item)
        signatures[key] = signature

    drafts: list[PytestIssueDraftFact] = []
    for key in sorted(groups):
        items = sorted(groups[key], key=lambda value: value.node_id)
        first = items[0]
        signature = signatures[key]
        identity = hashlib.sha256(
            "\x1f".join((project_id, run_id, key[0], key[1], signature)).encode("utf-8")
        ).hexdigest()[:24]
        kind = first.failure_type or first.category
        message = _first_line(first.message)
        title = f"{kind}: {message}" if message else f"{kind}: pytest failure"
        locations = tuple(dict.fromkeys(item.location for item in items))
        evidence_refs = tuple(dict.fromkeys(item.evidence_id for item in items))
        drafts.append(
            PytestIssueDraftFact(
                issue_id=f"issue:pytest:{identity}",
                signature=signature,
                title=title[:300],
                failure_type=first.failure_type,
                category=first.category,
                message=first.message,
                case_id=first.case_id,
                step_id=first.step_id,
                attempt_id=first.attempt_id,
                run_id=run_id,
                locations=locations,
                evidence_refs=evidence_refs,
                failures=tuple(items),
            )
        )
    return tuple(drafts)


def pytest_issue_drafts(
    facts: ExecutionFacts, objects: BytesReader | None,
) -> tuple[PytestIssueDraftFact, ...]:
    drafts = cluster_pytest_failures(
        facts.project_id,
        facts.run_id,
        collect_pytest_failures(facts, objects),
    )
    plan = getattr(facts, "plan_revision", None)
    plan_ref = (
        f"{plan.revision_id}@{plan.revision_no}"
        if plan is not None
        else ""
    )
    run = getattr(facts, "run", None)
    environment_ref = getattr(run, "environment_ref", "") if run is not None else ""
    return tuple(
        replace(
            draft,
            environment_ref=environment_ref,
            plan_revision_ref=plan_ref,
            source_ref=getattr(facts, "snapshot_commit_id", ""),
        )
        for draft in drafts
    )


@dataclass(frozen=True, slots=True)
class PytestComparison:
    new_node_ids: tuple[str, ...]
    existing_node_ids: tuple[str, ...]
    fixed_node_ids: tuple[str, ...]


def compare_failure_nodes(
    current: tuple[str, ...], previous: tuple[str, ...],
) -> PytestComparison:
    """Compare two saved failure sets for the same selected scope.

    ``fixed`` means the node failed previously and passes now; it is a
    comparison fact, not a closure proof.
    """

    now, before = set(current), set(previous)
    return PytestComparison(
        new_node_ids=tuple(sorted(now - before)),
        existing_node_ids=tuple(sorted(now & before)),
        fixed_node_ids=tuple(sorted(before - now)),
    )


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def cluster_identity(value: Mapping[str, object]) -> tuple[str, str, str]:
    """Read the minimal draft identity a caller may submit.

    The caller sends only ``run_id``/``step_id``/``signature``; every failure
    detail is re-derived from saved facts so a draft cannot invent locations,
    messages or evidence.
    """

    run_id = _text(value.get("run_id"), "run_id")
    step_id = _text(value.get("step_id"), "step_id")
    signature = _text(value.get("signature"), "signature")
    if len(signature) != 64 or any(ch not in "0123456789abcdef" for ch in signature):
        raise ValueError("signature must be a lowercase sha256 hex digest")
    return run_id, step_id, signature


def matching_draft(
    drafts: tuple[PytestIssueDraftFact, ...],
    run_id: str,
    step_id: str,
    signature: str,
) -> PytestIssueDraftFact:
    matches = [
        item
        for item in drafts
        if item.run_id == run_id
        and item.step_id == step_id
        and item.signature == signature
    ]
    if len(matches) != 1:
        raise ValueError("the requested pytest failure cluster is not in the saved run")
    return matches[0]
