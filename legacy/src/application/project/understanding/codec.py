"""项目理解的 payload 编解码与字段校验。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace

from aitest.application.project.understanding.errors import ProjectUnderstandingError
from aitest.application.project.understanding.shared import (
    PROJECT_UNDERSTANDING_SCHEMA,
)
from aitest.domain.canonical import canonical_digest
from aitest.domain.planning.understanding import (
    BusinessFunction,
    BusinessFunctionCatalog,
    BusinessFunctionKind,
    BusinessFunctionOrigin,
    CapabilityGap,
    FunctionTrigger,
    GoalCandidate,
    ProjectSignalOrigin,
    ProjectSignals,
    ProjectUnderstanding,
    ReviewState,
    TestIntent,
    TestIntentOrigin,
)


def understanding_identity(project_id: str, scope: str) -> str:
    """Stable identity of one ``(project, scope)`` understanding.

    A different source snapshot is a new ``revision`` of the same identity;
    a different scope is a different understanding.
    """
    _require_text(project_id, "project_id")
    _require_text(scope, "scope")
    raw = f"{project_id}\x1f{scope}".encode()
    return "und-" + hashlib.sha256(raw).hexdigest()[:16]

def understanding_summary(understanding: ProjectUnderstanding) -> dict[str, object]:
    """The response summary returned by ``analyze_project``.

    ``goal_candidates`` is the UI projection itself (not a count): callers need
    the candidate statements to show a goal picker. The other entries are
    counts so the response stays small even for a large catalog.
    """
    return {
        "understanding_id": understanding.understanding_id,
        "understanding_revision": understanding.revision,
        "function_count": len(understanding.catalog.functions),
        "intent_count": len(understanding.test_intents),
        "goal_candidates": [_goal_payload(goal) for goal in understanding.goal_candidates],
        "unknown_count": len(understanding.unknown_areas),
        "capability_gap_count": len(understanding.capability_gaps),
    }

def understanding_to_payload(
    understanding: ProjectUnderstanding, *, project_id: str, workspace_id: str
) -> dict[str, object]:
    """Serialise the immutable record body; revisions live in the body, too."""
    _require_text(project_id, "project_id")
    _require_text(workspace_id, "workspace_id")
    return {
        "schema_version": PROJECT_UNDERSTANDING_SCHEMA,
        "project_id": project_id,
        "workspace_id": workspace_id,
        "understanding_id": understanding.understanding_id,
        "revision": understanding.revision,
        "source_snapshot_id": understanding.source_snapshot_id,
        "scope": understanding.scope,
        "body": _body_payload(understanding, include_revision=True),
        "digest": understanding.digest,
    }

def understanding_from_payload(
    payload: Mapping[str, object],
    *,
    project_id: str,
    workspace_id: str,
    record_revision: int | None = None,
) -> ProjectUnderstanding:
    """Re-read a saved understanding and verify its content digest.

    A record whose digest no longer matches its body is rejected rather than
    interpreted, so a damaged record cannot become a silent empty catalog.
    """
    _require_text(project_id, "project_id")
    _require_text(workspace_id, "workspace_id")
    schema = _text(payload.get("schema_version"), "schema_version")
    if schema != PROJECT_UNDERSTANDING_SCHEMA:
        raise ProjectUnderstandingError("project understanding schema is unsupported")
    if _text(payload.get("project_id"), "project_id") != project_id:
        raise ProjectUnderstandingError("project understanding belongs to another project")
    if _text(payload.get("workspace_id"), "workspace_id") != workspace_id:
        raise ProjectUnderstandingError("project understanding belongs to another workspace")
    understanding_id = _text(payload.get("understanding_id"), "understanding_id")
    revision = _integer(payload.get("revision"), "revision")
    if revision < 1:
        raise ProjectUnderstandingError("project understanding revision must be >= 1")
    if record_revision is not None and revision != record_revision:
        raise ProjectUnderstandingError(
            "project understanding revision differs from its warehouse record"
        )
    source_snapshot_id = _text(payload.get("source_snapshot_id"), "source_snapshot_id")
    scope = _text(payload.get("scope"), "scope")
    if understanding_id != understanding_identity(project_id, scope):
        raise ProjectUnderstandingError("project understanding identity differs from its scope")
    body = _mapping(payload.get("body"), "body")
    signals = _signals_from_payload(_mapping(body.get("project_signals"), "project_signals"))
    functions = tuple(
        _function_from_payload(item, revision=revision, source_snapshot_id=source_snapshot_id)
        for item in _mappings(body.get("catalog"), "catalog")
    )
    catalog = BusinessFunctionCatalog(
        understanding_id=understanding_id,
        revision=revision,
        functions=functions,
    )
    intents = tuple(
        _intent_from_payload(item, revision=revision)
        for item in _mappings(body.get("test_intents"), "test_intents")
    )
    goals = tuple(
        _goal_from_payload(item)
        for item in _mappings(body.get("goal_candidates"), "goal_candidates")
    )
    gaps = tuple(
        _gap_from_payload(item)
        for item in _mappings(body.get("capability_gaps"), "capability_gaps")
    )
    unknown = tuple(_text_list(body.get("unknown_areas"), "unknown_areas"))
    saved_digest = _text(payload.get("digest"), "digest")
    try:
        understanding = ProjectUnderstanding(
            understanding_id=understanding_id,
            revision=revision,
            source_snapshot_id=source_snapshot_id,
            scope=scope,
            project_signals=signals,
            catalog=catalog,
            test_intents=intents,
            goal_candidates=goals,
            capability_gaps=gaps,
            unknown_areas=unknown,
            digest=saved_digest,
        )
    except ValueError as error:
        raise ProjectUnderstandingError(
            f"saved project understanding is invalid: {error}"
        ) from error
    if understanding.digest != understanding_content_digest(understanding):
        raise ProjectUnderstandingError(
            "saved project understanding digest does not match its body"
        )
    return understanding

def understanding_content_digest(understanding: ProjectUnderstanding) -> str:
    """Content digest that deliberately excludes revision numbers.

    The same source snapshot must replay byte-identically before it becomes a
    new revision. Revision is a warehouse fact, not scan content.
    """
    return _digest(
        {
            "understanding_id": understanding.understanding_id,
            "source_snapshot_id": understanding.source_snapshot_id,
            "scope": understanding.scope,
            **_body_payload(understanding, include_revision=False),
        }
    )

def _body_payload(
    understanding: ProjectUnderstanding, *, include_revision: bool
) -> dict[str, object]:
    return {
        "project_signals": _signals_payload(understanding.project_signals),
        "catalog": [
            _function_payload(function, include_revision=include_revision)
            for function in understanding.catalog.functions
        ],
        "test_intents": [
            _intent_payload(intent, include_revision=include_revision)
            for intent in understanding.test_intents
        ],
        "goal_candidates": [_goal_payload(goal) for goal in understanding.goal_candidates],
        "capability_gaps": [
            _gap_payload(gap) for gap in understanding.capability_gaps
        ],
        "unknown_areas": list(understanding.unknown_areas),
    }

def _signals_payload(signals: ProjectSignals) -> dict[str, object]:
    return {
        "detected_stack": list(signals.detected_stack),
        "entry_candidates": list(signals.entry_candidates),
        "capabilities": list(signals.capabilities),
        "risk_signals": list(signals.risk_signals),
        "confidence": dict(sorted(signals.confidence.items())),
        "evidence_refs": list(signals.evidence_refs),
        "origin": signals.origin.value,
    }

def _function_payload(function: BusinessFunction, *, include_revision: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "function_id": function.function_id,
        "name": function.name,
        "parent_function_id": function.parent_function_id,
        "kind": function.kind.value,
        "trigger": function.trigger.value,
        "business_object": function.business_object,
        "expected_outcome": function.expected_outcome,
        "conditions": list(function.conditions),
        "required_dimensions": list(function.required_dimensions),
        "source_refs": list(function.source_refs),
        "origin": function.origin.value,
        "confidence": function.confidence,
        "review_state": function.review_state.value,
        "canonical_function_id": function.canonical_function_id,
        "aliases": list(function.aliases),
        "source_snapshot_id": function.source_snapshot_id,
    }
    if function.dimension_origins:
        payload["dimension_origins"] = [
            {"dimension": dimension, "source": source, "reason": reason}
            for dimension, source, reason in function.dimension_origins
        ]
    if include_revision:
        payload["understanding_revision"] = function.understanding_revision
    return payload

def _intent_payload(intent: TestIntent, *, include_revision: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "intent_id": intent.intent_id,
        "statement": intent.statement,
        "risk": intent.risk,
        "function_ids": sorted(intent.function_ids),
        "required_dimensions": list(intent.required_dimensions),
        "acceptance_condition": intent.acceptance_condition,
        "rationale": intent.rationale,
        "origin": intent.origin.value,
        "confidence": intent.confidence,
    }
    if include_revision:
        payload["understanding_revision"] = intent.understanding_revision
    return payload

def _goal_payload(goal: GoalCandidate) -> dict[str, object]:
    return {
        "intent_id": goal.intent_id,
        "statement": goal.statement,
        "why": goal.why,
        "function_ids": sorted(goal.function_ids),
        "risk": goal.risk,
    }

def _gap_payload(gap: CapabilityGap) -> dict[str, object]:
    return {
        "gap_id": gap.gap_id,
        "scope": gap.scope,
        "missing_capability": gap.missing_capability,
        "capability_revision": gap.capability_revision,
        "reason": gap.reason,
        "workaround": gap.workaround,
        "blocks_full": gap.blocks_full,
        "source_refs": list(gap.source_refs),
    }

def _signals_from_payload(payload: Mapping[str, object]) -> ProjectSignals:
    confidence = _mapping(payload.get("confidence"), "confidence")
    values: dict[str, float] = {}
    for key, raw in confidence.items():
        values[_text(key, "confidence signal")] = _float(raw, "confidence")
    return ProjectSignals(
        detected_stack=_text_list(payload.get("detected_stack"), "detected_stack"),
        entry_candidates=_text_list(payload.get("entry_candidates"), "entry_candidates"),
        capabilities=_text_list(payload.get("capabilities"), "capabilities"),
        risk_signals=_text_list(payload.get("risk_signals"), "risk_signals"),
        confidence=values,
        evidence_refs=_text_list(payload.get("evidence_refs"), "evidence_refs"),
        origin=ProjectSignalOrigin(_text(payload.get("origin"), "origin")),
    )

def _function_from_payload(
    payload: Mapping[str, object], *, revision: int, source_snapshot_id: str
) -> BusinessFunction:
    return BusinessFunction(
        function_id=_text(payload.get("function_id"), "function_id"),
        name=_text(payload.get("name"), "name"),
        parent_function_id=_optional_text(payload.get("parent_function_id"), "parent_function_id"),
        kind=BusinessFunctionKind(_text(payload.get("kind"), "kind")),
        trigger=FunctionTrigger(_text(payload.get("trigger"), "trigger")),
        business_object=_text(payload.get("business_object"), "business_object"),
        expected_outcome=_text(payload.get("expected_outcome"), "expected_outcome"),
        conditions=_text_list(payload.get("conditions"), "conditions"),
        required_dimensions=_text_list(
            payload.get("required_dimensions"), "required_dimensions"
        ),
        source_refs=_text_list(payload.get("source_refs"), "source_refs"),
        origin=BusinessFunctionOrigin(_text(payload.get("origin"), "origin")),
        confidence=_float(payload.get("confidence"), "confidence"),
        review_state=ReviewState(_text(payload.get("review_state"), "review_state")),
        understanding_revision=_integer(
            payload.get("understanding_revision"), "understanding_revision"
        ),
        canonical_function_id=_optional_text(
            payload.get("canonical_function_id"), "canonical_function_id"
        ),
        aliases=_text_list(payload.get("aliases"), "aliases"),
        source_snapshot_id=_text(payload.get("source_snapshot_id"), "source_snapshot_id"),
        dimension_origins=tuple(
            (
                _text(item.get("dimension"), "dimension"),
                _text(item.get("source"), "source"),
                _reason_text(item.get("reason")),
            )
            for item in _mappings(payload.get("dimension_origins", []), "dimension_origins")
        ),
    )

def _intent_from_payload(payload: Mapping[str, object], *, revision: int) -> TestIntent:
    understanding_revision = _integer(
        payload.get("understanding_revision"), "understanding_revision"
    )
    if understanding_revision != revision:
        raise ProjectUnderstandingError("test intent revision differs from its understanding")
    return TestIntent(
        intent_id=_text(payload.get("intent_id"), "intent_id"),
        statement=_text(payload.get("statement"), "statement"),
        risk=_text(payload.get("risk"), "risk"),
        function_ids=frozenset(_text_list(payload.get("function_ids"), "function_ids")),
        required_dimensions=_text_list(
            payload.get("required_dimensions"), "required_dimensions"
        ),
        acceptance_condition=_text(
            payload.get("acceptance_condition"), "acceptance_condition"
        ),
        rationale=_text(payload.get("rationale"), "rationale"),
        origin=TestIntentOrigin(_text(payload.get("origin"), "origin")),
        confidence=_float(payload.get("confidence"), "confidence"),
        understanding_revision=understanding_revision,
    )

def _goal_from_payload(payload: Mapping[str, object]) -> GoalCandidate:
    return GoalCandidate(
        intent_id=_text(payload.get("intent_id"), "intent_id"),
        statement=_text(payload.get("statement"), "statement"),
        why=_text(payload.get("why"), "why"),
        function_ids=frozenset(_text_list(payload.get("function_ids"), "function_ids")),
        risk=_text(payload.get("risk"), "risk"),
    )

def _gap_from_payload(payload: Mapping[str, object]) -> CapabilityGap:
    workaround = payload.get("workaround")
    return CapabilityGap(
        gap_id=_text(payload.get("gap_id"), "gap_id"),
        scope=_text(payload.get("scope"), "scope"),
        missing_capability=_text(
            payload.get("missing_capability"), "missing_capability"
        ),
        capability_revision=_text(
            payload.get("capability_revision"), "capability_revision"
        ),
        reason=_text(payload.get("reason"), "reason"),
        workaround=None if workaround is None else _text(workaround, "workaround"),
        blocks_full=_boolean(payload.get("blocks_full"), "blocks_full"),
        source_refs=_text_list(payload.get("source_refs"), "source_refs"),
    )

def _digest(value: object) -> str:
    return canonical_digest(value)

def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ProjectUnderstandingError(f"{name} must be a nonempty string")

def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectUnderstandingError(f"{name} must be a nonempty string")
    return value

def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name)

def _reason_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProjectUnderstandingError("reason must be a string")
    return value

def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise ProjectUnderstandingError(f"{name} must be an integer")
    return value

def _float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectUnderstandingError(f"{name} must be a number")
    return float(value)

def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ProjectUnderstandingError(f"{name} must be a boolean")
    return value

def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProjectUnderstandingError(f"{name} must be an object")
    return value

def _mappings(value: object, name: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        raise ProjectUnderstandingError(f"{name} must be a list")
    items: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ProjectUnderstandingError(f"{name} must contain objects")
        items.append(item)
    return tuple(items)

def _text_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ProjectUnderstandingError(f"{name} must be a list")
    return tuple(_text(item, name) for item in value)

def _rebase(understanding: ProjectUnderstanding, revision: int) -> ProjectUnderstanding:
    if revision == understanding.revision:
        return understanding
    functions = tuple(
        replace(function, understanding_revision=revision)
        for function in understanding.catalog.functions
    )
    catalog = replace(
        understanding.catalog, revision=revision, functions=functions
    )
    intents = tuple(
        replace(intent, understanding_revision=revision)
        for intent in understanding.test_intents
    )
    return replace(understanding, revision=revision, catalog=catalog, test_intents=intents)
