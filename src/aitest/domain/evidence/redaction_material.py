"""One strict meaning for temporary and frozen safe stream summaries."""

import re

from aitest.domain.evidence.evidence import RedactionSummary
from aitest.domain.execution.runs import OutputStreamName
from aitest.domain.json_material import require_json_text

MAX_SUMMARY_BYTES = 64 * 1024
_SUMMARY_FIELDS = frozenset({
    "schema_version", "summary_id", "stream_name", "policy_version",
    "applied_rule_categories", "filtered_streams", "filtered_ranges",
    "replacement_count", "completeness", "gap_reasons",
})


def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _require_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value



def parse_redaction_summary(
    raw: object, attempt_id: str, stream_name: OutputStreamName,
) -> RedactionSummary:
    if not isinstance(raw, dict) or set(raw) != _SUMMARY_FIELDS or (
        raw.get("schema_version"), raw.get("summary_id"), raw.get("stream_name")
    ) != ("aitest.redaction-summary/1.0", f"redaction:{attempt_id}:{stream_name.value}",
          stream_name.value):
        raise ValueError("redaction summary schema or ownership cannot be verified")

    def text(value: object, name: str) -> str:
        result = _require_str(value, name)
        require_json_text(result)
        if not result.strip():
            raise ValueError("redaction summary text must not be blank")
        return result

    def values(name: str) -> tuple[str, ...]:
        result = tuple(text(item, name) for item in _require_list(raw.get(name), name))
        if len(set(result)) != len(result):
            raise ValueError("redaction summary entries must be unique")
        return result

    result = RedactionSummary(
        policy_version=text(raw.get("policy_version"), "policy_version"),
        applied_rule_categories=values("applied_rule_categories"),
        filtered_streams=values("filtered_streams"), filtered_ranges=values("filtered_ranges"),
        replacement_count=_require_int(raw.get("replacement_count"), "replacement_count"),
        completeness=text(raw.get("completeness"), "completeness"),
        gap_reasons=values("gap_reasons"),
    )
    if result.filtered_streams != (stream_name.value,) or (
        result.completeness not in {"complete", "partial", "gap", "unknown"}
    ) or (result.completeness == "complete" and result.gap_reasons):
        raise ValueError("redaction summary stream or completeness cannot be verified")
    previous_end = 0
    for value in result.filtered_ranges:
        match = re.fullmatch(re.escape(stream_name.value) + r":([0-9]+)-([0-9]+)", value)
        if match is None:
            raise ValueError("redaction summary range belongs to another stream or is invalid")
        start, end = map(int, match.groups())
        if start < previous_end or end < start:
            raise ValueError("redaction summary ranges overlap or are reversed")
        previous_end = end
    return result


def redaction_summary_payload(
    summary: RedactionSummary, attempt_id: str, stream_name: OutputStreamName,
) -> dict[str, object]:
    if summary.created_at is not None:
        raise ValueError("stream summary cannot invent an uncaptured timestamp")
    payload: dict[str, object] = {
        "schema_version": "aitest.redaction-summary/1.0",
        "summary_id": f"redaction:{attempt_id}:{stream_name.value}",
        "stream_name": stream_name.value,
        "policy_version": summary.policy_version,
        "applied_rule_categories": list(summary.applied_rule_categories),
        "filtered_streams": list(summary.filtered_streams),
        "filtered_ranges": list(summary.filtered_ranges),
        "replacement_count": summary.replacement_count,
        "completeness": summary.completeness,
        "gap_reasons": list(summary.gap_reasons),
    }
    if parse_redaction_summary(payload, attempt_id, stream_name) != summary:
        raise ValueError("stream summary cannot preserve its exact meaning")
    return payload
