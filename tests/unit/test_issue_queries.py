import pytest

from aitest.application.review.issue_queries import list_issues
from aitest.domain.review.defects import IssueDisposition, IssueSeverity, IssueStatus
from aitest.domain.review.issue_queries import (
    IssueBlockingState,
    IssueFacet,
    IssueFilterMask,
    IssueListProjection,
    IssueListQuery,
    IssueListScope,
    IssueQueryCursorMismatch,
    IssueQueryError,
    IssueReviewState,
    IssueSeverityFilter,
)
from aitest.interfaces.dto import issue_list_page_dto

COMMIT_ID = "commit-1"


def _projection(
    issue_id: str,
    *,
    modules: tuple[str, ...],
    layers: tuple[str, ...],
    severity: IssueSeverity | None,
    workflow: IssueStatus | None,
    review: IssueReviewState,
    disposition: IssueDisposition | None,
    blocking: IssueBlockingState,
    sequence: int,
    canonical: str | None = None,
    gap: str | None = None,
) -> IssueListProjection:
    return IssueListProjection(
        project_id="project-1",
        issue_id=issue_id,
        content_revision=1,
        module_ids=frozenset(modules),
        layers=frozenset(layers),
        severity=severity,
        workflow_state=workflow,
        review_state=review,
        disposition=disposition,
        blocking=blocking,
        updated_sequence=sequence,
        canonical_issue_id=canonical,
        gap_reason=gap,
    )


def _projections() -> tuple[IssueListProjection, ...]:
    return (
        _projection(
            "active",
            modules=("module-a", "module-b"),
            layers=("L2",),
            severity=IssueSeverity.P0,
            workflow=IssueStatus.CONFIRMED,
            review=IssueReviewState.UNREVIEWED,
            disposition=IssueDisposition.ACTIVE,
            blocking=IssueBlockingState.BLOCKING,
            sequence=1,
        ),
        _projection(
            "fixed",
            modules=("module-b",),
            layers=("L3",),
            severity=IssueSeverity.P1,
            workflow=IssueStatus.CLOSED,
            review=IssueReviewState.REVIEWED,
            disposition=IssueDisposition.FIXED,
            blocking=IssueBlockingState.NON_BLOCKING,
            sequence=2,
        ),
        _projection(
            "deferred",
            modules=("unknown",),
            layers=("unknown",),
            severity=None,
            workflow=IssueStatus.CONFIRMED,
            review=IssueReviewState.UNKNOWN,
            disposition=IssueDisposition.DEFERRED,
            blocking=IssueBlockingState.UNKNOWN,
            sequence=3,
            gap="blocking state is unknown",
        ),
        _projection(
            "duplicate-active",
            modules=("module-a",),
            layers=("L2",),
            severity=IssueSeverity.P0,
            workflow=IssueStatus.CONFIRMED,
            review=IssueReviewState.REVIEWED,
            disposition=IssueDisposition.DUPLICATE,
            blocking=IssueBlockingState.BLOCKING,
            sequence=4,
            canonical="active",
        ),
        _projection(
            "duplicate-fixed",
            modules=("module-b",),
            layers=("L3",),
            severity=IssueSeverity.P2,
            workflow=IssueStatus.CLOSED,
            review=IssueReviewState.REVIEWED,
            disposition=IssueDisposition.DUPLICATE,
            blocking=IssueBlockingState.NON_BLOCKING,
            sequence=5,
            canonical="fixed",
        ),
        _projection(
            "orphan-duplicate",
            modules=("module-c",),
            layers=("L1",),
            severity=IssueSeverity.P1,
            workflow=IssueStatus.DRAFT,
            review=IssueReviewState.UNREVIEWED,
            disposition=IssueDisposition.DUPLICATE,
            blocking=IssueBlockingState.UNKNOWN,
            sequence=6,
            canonical="missing-root",
            gap="duplicate canonical issue is missing",
        ),
    )


def _query(
    *,
    scope: IssueListScope = IssueListScope.OPEN,
    facet: IssueFacet = IssueFacet.NONE,
    value: str | None = None,
    severity: IssueSeverityFilter | None = None,
    page_size: int = 20,
) -> IssueListQuery:
    return IssueListQuery(
        project_id="project-1",
        scope=scope,
        facet=facet,
        facet_value=value,
        severity=severity,
        page_size=page_size,
    )


def _ids(page) -> set[str]:
    return {item.issue_id for item in page.items}


def test_query_model_exposes_exactly_fourteen_masks() -> None:
    facet_values = {
        IssueFacet.NONE: None,
        IssueFacet.MODULE: "module-a",
        IssueFacet.LAYER: "L2",
        IssueFacet.REVIEW_STATE: "reviewed",
        IssueFacet.WORKFLOW_STATE: "closed",
        IssueFacet.DISPOSITION: "active",
        IssueFacet.BLOCKING: "blocking",
    }
    masks = {
        _query(facet=facet, value=value, severity=severity).mask
        for facet, value in facet_values.items()
        for severity in (None, IssueSeverityFilter.P0)
    }

    assert len(masks) == 14
    assert masks == set(IssueFilterMask)


def test_open_and_all_follow_duplicate_root_resolution() -> None:
    projections = _projections()
    open_page = list_issues(projections, _query(), commit_id=COMMIT_ID)
    all_page = list_issues(
        projections,
        _query(scope=IssueListScope.ALL),
        commit_id=COMMIT_ID,
    )

    assert _ids(open_page) == {"active", "deferred", "duplicate-active", "orphan-duplicate"}
    assert _ids(all_page) == {projection.issue_id for projection in projections}


def test_module_filter_does_not_duplicate_multimodule_issue() -> None:
    page = list_issues(
        _projections(),
        _query(facet=IssueFacet.MODULE, value="module-a"),
        commit_id=COMMIT_ID,
    )

    assert [item.issue_id for item in page.items] == ["active", "duplicate-active"]


def test_severity_can_be_used_alone_or_stacked_on_main_facet() -> None:
    severity_only = list_issues(
        _projections(),
        _query(severity=IssueSeverityFilter.P0),
        commit_id=COMMIT_ID,
    )
    module_and_severity = list_issues(
        _projections(),
        _query(
            scope=IssueListScope.ALL,
            facet=IssueFacet.MODULE,
            value="module-b",
            severity=IssueSeverityFilter.P1,
        ),
        commit_id=COMMIT_ID,
    )

    assert _ids(severity_only) == {"active", "duplicate-active"}
    assert _ids(module_and_severity) == {"fixed"}


@pytest.mark.parametrize(
    ("facet", "value", "expected"),
    [
        (IssueFacet.LAYER, "L2", {"active", "duplicate-active"}),
        (IssueFacet.REVIEW_STATE, "reviewed", {"duplicate-active"}),
        (IssueFacet.WORKFLOW_STATE, "closed", set()),
        (IssueFacet.DISPOSITION, "duplicate", {"duplicate-active", "orphan-duplicate"}),
        (IssueFacet.BLOCKING, "unknown", {"deferred", "orphan-duplicate"}),
    ],
)
def test_remaining_main_facets_filter_current_open_projection(
    facet: IssueFacet,
    value: str,
    expected: set[str],
) -> None:
    page = list_issues(
        _projections(),
        _query(facet=facet, value=value),
        commit_id=COMMIT_ID,
    )

    assert _ids(page) == expected


def test_unknown_is_explicit_and_not_same_as_no_filter() -> None:
    explicit_unknown = list_issues(
        _projections(),
        _query(severity=IssueSeverityFilter.UNKNOWN),
        commit_id=COMMIT_ID,
    )
    no_severity_filter = list_issues(_projections(), _query(), commit_id=COMMIT_ID)

    assert _ids(explicit_unknown) == {"deferred"}
    assert len(no_severity_filter.items) > len(explicit_unknown.items)


def test_cursor_pages_same_commit_without_duplicates_or_gaps() -> None:
    projections = _projections()
    query = _query(scope=IssueListScope.ALL, page_size=2)
    seen: list[str] = []
    cursor = None
    while True:
        page = list_issues(projections, query, commit_id=COMMIT_ID, cursor=cursor)
        seen.extend(item.issue_id for item in page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert seen == [
        "active",
        "fixed",
        "deferred",
        "duplicate-active",
        "duplicate-fixed",
        "orphan-duplicate",
    ]


def test_cursor_rejects_condition_or_commit_change() -> None:
    projections = _projections()
    first = list_issues(projections, _query(page_size=1), commit_id=COMMIT_ID)
    assert first.next_cursor is not None

    with pytest.raises(IssueQueryCursorMismatch, match="different commit"):
        list_issues(
            projections,
            _query(page_size=1),
            commit_id="commit-2",
            cursor=first.next_cursor,
        )

    changed = _query(
        facet=IssueFacet.MODULE,
        value="module-a",
        page_size=1,
    )
    with pytest.raises(IssueQueryCursorMismatch, match="different query conditions"):
        list_issues(projections, changed, commit_id=COMMIT_ID, cursor=first.next_cursor)


def test_invalid_filter_combinations_are_rejected() -> None:
    with pytest.raises(ValueError, match="requires facet_value"):
        _query(facet=IssueFacet.MODULE)
    with pytest.raises(ValueError, match="cannot carry facet_value"):
        _query(value="module-a")
    with pytest.raises(IssueQueryError, match="IssueReviewState"):
        _query(facet=IssueFacet.REVIEW_STATE, value="not-a-state")


def test_unknown_blocking_projection_requires_visible_gap() -> None:
    with pytest.raises(ValueError, match="visible gap_reason"):
        _projection(
            "unknown-blocking",
            modules=("module-a",),
            layers=("L1",),
            severity=IssueSeverity.P1,
            workflow=IssueStatus.CONFIRMED,
            review=IssueReviewState.UNREVIEWED,
            disposition=IssueDisposition.ACTIVE,
            blocking=IssueBlockingState.UNKNOWN,
            sequence=9,
        )


def test_issue_list_dto_serializes_projection_without_recalculation() -> None:
    page = list_issues(
        _projections(),
        _query(page_size=2),
        commit_id=COMMIT_ID,
    )
    view = issue_list_page_dto(page)

    assert view.scope is page.scope
    assert view.mask is page.mask
    assert tuple(item.issue_id for item in view.items) == tuple(
        item.issue_id for item in page.items
    )
    assert view.next_cursor is not None
    assert view.next_cursor.commit_id == COMMIT_ID
