"""Finite issues.list query semantics.

This module defines the exact business filter semantics that A compiles into
its index. It is not a replacement for the production index or a permission to
scan all history.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from aitest.domain.review.defects import IssueDisposition, IssueSeverity, IssueStatus

QUERY_SPEC_VERSION = "aitest.issues-list/1.0"
MAX_PAGE_SIZE = 200
UNKNOWN_FILTER_VALUE = "unknown"


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


class IssueListScope(StrEnum):
    OPEN = "open"
    ALL = "all"


class IssueFacet(StrEnum):
    NONE = "none"
    MODULE = "module"
    LAYER = "layer"
    REVIEW_STATE = "review_state"
    WORKFLOW_STATE = "workflow_state"
    DISPOSITION = "disposition"
    BLOCKING = "blocking"


class IssueFilterMask(StrEnum):
    NONE = "none"
    SEVERITY = "severity"
    MODULE = "module"
    MODULE_SEVERITY = "module+severity"
    LAYER = "layer"
    LAYER_SEVERITY = "layer+severity"
    REVIEW_STATE = "review_state"
    REVIEW_STATE_SEVERITY = "review_state+severity"
    WORKFLOW_STATE = "workflow_state"
    WORKFLOW_STATE_SEVERITY = "workflow_state+severity"
    DISPOSITION = "disposition"
    DISPOSITION_SEVERITY = "disposition+severity"
    BLOCKING = "blocking"
    BLOCKING_SEVERITY = "blocking+severity"


_FACET_MASK: dict[IssueFacet, IssueFilterMask] = {
    IssueFacet.MODULE: IssueFilterMask.MODULE,
    IssueFacet.LAYER: IssueFilterMask.LAYER,
    IssueFacet.REVIEW_STATE: IssueFilterMask.REVIEW_STATE,
    IssueFacet.WORKFLOW_STATE: IssueFilterMask.WORKFLOW_STATE,
    IssueFacet.DISPOSITION: IssueFilterMask.DISPOSITION,
    IssueFacet.BLOCKING: IssueFilterMask.BLOCKING,
}
_FACET_SEVERITY_MASK: dict[IssueFacet, IssueFilterMask] = {
    IssueFacet.MODULE: IssueFilterMask.MODULE_SEVERITY,
    IssueFacet.LAYER: IssueFilterMask.LAYER_SEVERITY,
    IssueFacet.REVIEW_STATE: IssueFilterMask.REVIEW_STATE_SEVERITY,
    IssueFacet.WORKFLOW_STATE: IssueFilterMask.WORKFLOW_STATE_SEVERITY,
    IssueFacet.DISPOSITION: IssueFilterMask.DISPOSITION_SEVERITY,
    IssueFacet.BLOCKING: IssueFilterMask.BLOCKING_SEVERITY,
}


class IssueReviewState(StrEnum):
    UNREVIEWED = "unreviewed"
    REVIEWED = "reviewed"
    UNKNOWN = "unknown"


class IssueBlockingState(StrEnum):
    BLOCKING = "blocking"
    NON_BLOCKING = "non_blocking"
    UNKNOWN = "unknown"


class IssueSeverityFilter(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    UNKNOWN = "unknown"


class IssueQueryError(ValueError):
    code = "QUERY_UNSUPPORTED_FILTER"


class IssueQueryCursorMismatch(IssueQueryError):
    code = "QUERY_CURSOR_MISMATCH"


@dataclass(frozen=True, slots=True)
class IssueListProjection:
    project_id: str
    issue_id: str
    content_revision: int
    module_ids: frozenset[str]
    layers: frozenset[str]
    severity: IssueSeverity | None
    workflow_state: IssueStatus | None
    review_state: IssueReviewState
    disposition: IssueDisposition | None
    blocking: IssueBlockingState
    updated_sequence: int
    canonical_issue_id: str | None = None
    gap_reason: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.project_id, "project_id")
        _require_text(self.issue_id, "issue_id")
        if self.content_revision < 1:
            raise ValueError("content_revision must be positive")
        if self.updated_sequence < 0:
            raise ValueError("updated_sequence must be non-negative")
        if not self.module_ids:
            raise ValueError("module_ids must be non-empty; use 'unknown' explicitly")
        if any(not module_id.strip() for module_id in self.module_ids):
            raise ValueError("module_ids must not contain empty values")
        if not self.layers:
            raise ValueError("layers must be non-empty; use 'unknown' explicitly")
        if any(not layer.strip() for layer in self.layers):
            raise ValueError("layers must not contain empty values")
        if self.canonical_issue_id == self.issue_id:
            raise ValueError("an issue cannot point to itself as canonical")
        if self.disposition is IssueDisposition.DUPLICATE:
            if self.canonical_issue_id is None and not self.gap_reason:
                raise ValueError("dangling duplicate requires a visible gap_reason")
        elif self.canonical_issue_id is not None:
            raise ValueError("canonical_issue_id is only valid for duplicate disposition")
        if self.blocking is IssueBlockingState.UNKNOWN and not self.gap_reason:
            raise ValueError("unknown blocking state requires a visible gap_reason")


@dataclass(frozen=True, slots=True)
class IssueListQuery:
    project_id: str
    scope: IssueListScope = IssueListScope.OPEN
    facet: IssueFacet = IssueFacet.NONE
    facet_value: str | None = None
    severity: IssueSeverityFilter | None = None
    page_size: int = 50

    def __post_init__(self) -> None:
        _require_text(self.project_id, "project_id")
        if not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        if self.facet is IssueFacet.NONE:
            if self.facet_value is not None:
                raise ValueError("NONE facet cannot carry facet_value")
        else:
            if self.facet_value is None:
                raise ValueError("selected facet requires facet_value")
            _validate_facet_value(self.facet, self.facet_value)

    @property
    def mask(self) -> IssueFilterMask:
        if self.facet is IssueFacet.NONE:
            return IssueFilterMask.SEVERITY if self.severity is not None else IssueFilterMask.NONE
        masks = _FACET_SEVERITY_MASK if self.severity is not None else _FACET_MASK
        return masks[self.facet]

    @property
    def condition_fingerprint(self) -> str:
        payload = {
            "query_spec_version": QUERY_SPEC_VERSION,
            "project_id": self.project_id,
            "scope": self.scope.value,
            "facet": self.facet.value,
            "facet_value": self.facet_value,
            "severity": self.severity.value if self.severity is not None else None,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class IssueListCursor:
    commit_id: str
    condition_fingerprint: str
    last_updated_sequence: int
    last_issue_id: str

    def __post_init__(self) -> None:
        _require_text(self.commit_id, "commit_id")
        _require_text(self.condition_fingerprint, "condition_fingerprint")
        _require_text(self.last_issue_id, "last_issue_id")
        if self.last_updated_sequence < 0:
            raise ValueError("last_updated_sequence must be non-negative")


@dataclass(frozen=True, slots=True)
class IssueListPage:
    query_spec_version: str
    scope: IssueListScope
    mask: IssueFilterMask
    items: tuple[IssueListProjection, ...]
    next_cursor: IssueListCursor | None


def _validate_facet_value(facet: IssueFacet, value: str) -> None:
    _require_text(value, "facet_value")
    if facet is IssueFacet.MODULE:
        return
    if facet is IssueFacet.LAYER:
        if value not in {"L1", "L2", "L3", UNKNOWN_FILTER_VALUE}:
            raise IssueQueryError(f"invalid layer value: {value}")
        return
    if facet is IssueFacet.REVIEW_STATE:
        _parse_enum(value, IssueReviewState)
        return
    if facet is IssueFacet.WORKFLOW_STATE:
        if value == UNKNOWN_FILTER_VALUE:
            return
        _parse_enum(value, IssueStatus)
        return
    if facet is IssueFacet.DISPOSITION:
        if value == UNKNOWN_FILTER_VALUE:
            return
        _parse_enum(value, IssueDisposition)
        return
    if facet is IssueFacet.BLOCKING:
        _parse_enum(value, IssueBlockingState)
        return
    raise IssueQueryError(f"unsupported facet: {facet.value}")


def _parse_enum[T: StrEnum](value: str, enum_type: type[T]) -> T:
    try:
        return enum_type(value)
    except ValueError as error:
        raise IssueQueryError(f"invalid {enum_type.__name__} value: {value}") from error


def _resolve_duplicate_root(
    projection: IssueListProjection,
    by_issue: Mapping[str, IssueListProjection],
) -> tuple[IssueListProjection | None, str | None]:
    current = projection
    seen: set[str] = set()
    while current.disposition is IssueDisposition.DUPLICATE:
        if current.issue_id in seen:
            return None, "duplicate issue chain contains a cycle"
        seen.add(current.issue_id)
        if current.canonical_issue_id is None:
            return None, current.gap_reason or "duplicate canonical issue is missing"
        canonical = by_issue.get(current.canonical_issue_id)
        if canonical is None:
            return None, current.gap_reason or "duplicate canonical issue is missing"
        if canonical.project_id != current.project_id:
            return None, "duplicate issue chain crosses projects"
        current = canonical
    return current, None


def is_issue_open(
    projection: IssueListProjection,
    projections_by_issue: Mapping[str, IssueListProjection],
) -> bool:
    if projection.disposition in {IssueDisposition.FIXED, IssueDisposition.NON_DEFECT}:
        return False
    if projection.disposition in {IssueDisposition.ACTIVE, IssueDisposition.DEFERRED}:
        return True
    if projection.disposition is IssueDisposition.DUPLICATE:
        root, _ = _resolve_duplicate_root(projection, projections_by_issue)
        if root is None:
            return True
        return root.disposition not in {IssueDisposition.FIXED, IssueDisposition.NON_DEFECT}
    return True


def _matches_facet(projection: IssueListProjection, query: IssueListQuery) -> bool:
    facet = query.facet
    value = query.facet_value
    if facet is IssueFacet.NONE:
        return True
    if value is None:
        return False
    if facet is IssueFacet.MODULE:
        return value in projection.module_ids
    if facet is IssueFacet.LAYER:
        return value in projection.layers
    if facet is IssueFacet.REVIEW_STATE:
        return projection.review_state is _parse_enum(value, IssueReviewState)
    if facet is IssueFacet.WORKFLOW_STATE:
        if value == UNKNOWN_FILTER_VALUE:
            return projection.workflow_state is None
        return projection.workflow_state is _parse_enum(value, IssueStatus)
    if facet is IssueFacet.DISPOSITION:
        if value == UNKNOWN_FILTER_VALUE:
            return projection.disposition is None
        return projection.disposition is _parse_enum(value, IssueDisposition)
    if facet is IssueFacet.BLOCKING:
        return projection.blocking is _parse_enum(value, IssueBlockingState)
    return False


def _matches_severity(
    projection: IssueListProjection, severity: IssueSeverityFilter | None
) -> bool:
    if severity is None:
        return True
    if severity is IssueSeverityFilter.UNKNOWN:
        return projection.severity is None
    return projection.severity is _parse_enum(severity.value, IssueSeverity)


def query_issue_list(
    projections: tuple[IssueListProjection, ...],
    query: IssueListQuery,
    *,
    commit_id: str,
    cursor: IssueListCursor | None = None,
) -> IssueListPage:
    """Apply the frozen issues.list semantics for contract and index tests."""

    _require_text(commit_id, "commit_id")
    by_issue: dict[str, IssueListProjection] = {}
    for projection in projections:
        if projection.issue_id in by_issue:
            raise ValueError(f"duplicate current projection for issue: {projection.issue_id}")
        if projection.project_id == query.project_id:
            by_issue[projection.issue_id] = projection

    if cursor is not None:
        if cursor.commit_id != commit_id:
            raise IssueQueryCursorMismatch("cursor belongs to a different commit")
        if cursor.condition_fingerprint != query.condition_fingerprint:
            raise IssueQueryCursorMismatch("cursor belongs to different query conditions")

    filtered = [
        projection
        for projection in by_issue.values()
        if (query.scope is IssueListScope.ALL or is_issue_open(projection, by_issue))
        and _matches_facet(projection, query)
        and _matches_severity(projection, query.severity)
    ]
    filtered.sort(key=lambda projection: (projection.updated_sequence, projection.issue_id))
    if cursor is not None:
        cursor_key = (cursor.last_updated_sequence, cursor.last_issue_id)
        filtered = [
            projection
            for projection in filtered
            if (projection.updated_sequence, projection.issue_id) > cursor_key
        ]

    page_items = tuple(filtered[: query.page_size])
    next_cursor = None
    if len(filtered) > query.page_size and page_items:
        last = page_items[-1]
        next_cursor = IssueListCursor(
            commit_id=commit_id,
            condition_fingerprint=query.condition_fingerprint,
            last_updated_sequence=last.updated_sequence,
            last_issue_id=last.issue_id,
        )

    return IssueListPage(
        query_spec_version=QUERY_SPEC_VERSION,
        scope=query.scope,
        mask=query.mask,
        items=page_items,
        next_cursor=next_cursor,
    )


__all__ = [
    "IssueBlockingState",
    "IssueFacet",
    "IssueFilterMask",
    "IssueListCursor",
    "IssueListPage",
    "IssueListProjection",
    "IssueListQuery",
    "IssueListScope",
    "IssueQueryCursorMismatch",
    "IssueQueryError",
    "IssueReviewState",
    "IssueSeverityFilter",
    "QUERY_SPEC_VERSION",
    "is_issue_open",
    "query_issue_list",
]
