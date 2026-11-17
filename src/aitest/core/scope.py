"""Acceptance-item scope: which items apply, which were selected, what stayed out.

``applicable`` always means every active acceptance item of the task. The
optional module filter is a display/selection aid only; it never shrinks the
frozen applicable set ``M``. ``uncovered`` lists the applicable items that the
current scope did not select; it is a projection of stored task/plan facts, not
a verdict.
"""

from __future__ import annotations

from aitest.models import AcceptanceItem, Scope, Task


def applicable_items(task: Task, module_ids: list[str] | None = None) -> list[AcceptanceItem]:
    """Active items, optionally filtered by module for display purposes."""
    modules = set(module_ids or [])
    return [
        item
        for item in task.acceptance_items
        if item.status == "active" and (not modules or item.module_id in modules)
    ]


def all_applicable_items(task: Task) -> list[AcceptanceItem]:
    """The frozen applicable set ``M``: every active item of the task."""
    return [item for item in task.acceptance_items if item.status == "active"]


def uncovered_items(task: Task | None, scope: Scope) -> list[str]:
    """Return ``id：text`` for applicable items that the scope did not select."""
    if task is None:
        return []
    selected = set(scope.acceptance_item_ids)
    return [
        f"{item.item_id}：{item.text}"
        for item in all_applicable_items(task)
        if item.item_id not in selected
    ]
