"""Application boundary for the finite issues.list query."""

from __future__ import annotations

from aitest.domain.review.issue_queries import (
    IssueListCursor,
    IssueListPage,
    IssueListProjection,
    IssueListQuery,
    query_issue_list,
)


def list_issues(
    projections: tuple[IssueListProjection, ...],
    query: IssueListQuery,
    *,
    commit_id: str,
    cursor: IssueListCursor | None = None,
) -> IssueListPage:
    return query_issue_list(projections, query, commit_id=commit_id, cursor=cursor)


__all__ = ["list_issues"]
