"""Acceptance-item scope: which items apply, which were selected, what stayed out.

``applicable`` always means every active acceptance item of the task. The
optional module filter is a display/selection aid only; it never shrinks the
frozen applicable set ``M``. ``uncovered`` lists the applicable items that the
current scope did not select; it is a projection of stored task/plan facts, not
a verdict.
"""

from __future__ import annotations

import hashlib
import json

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


def uncovered_items(
    task: Task | None, scope: Scope, fallback_count: int = 0
) -> list[str]:
    """Return ``id：text`` for applicable items that the scope did not select.

    When the task and its snapshot are both gone, use the frozen applicable
    count so a missing history cannot be shown as "uncovered 0".
    """
    if task is None:
        if fallback_count <= 0:
            return ["未还原验收项（任务快照缺失，适用范围未知）"]
        missing = max(0, fallback_count - len(scope.acceptance_item_ids))
        return [f"未还原验收项 {index + 1}（任务快照缺失）" for index in range(missing)]
    selected = set(scope.acceptance_item_ids)
    return [
        f"{item.item_id}：{item.text}"
        for item in all_applicable_items(task)
        if item.item_id not in selected
    ]


def task_contract(task: Task) -> str:
    """Stable digest of the facts that make a plan's cases/test intent valid.

    Title, goal prose, executor and reviewer are deliberately excluded: editing
    them must not force a new plan. Acceptance-item text/status/module changes
    and the smoke/business intent do change what a plan is allowed to claim.
    """
    payload = {
        "intent": task.intent,
        "items": sorted(
            [
                [
                    item.item_id,
                    item.text,
                    item.status,
                    item.observable,
                    item.module_id or "",
                ]
                for item in task.acceptance_items
                if item.status == "active"
            ]
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
