import pytest

from ai_test.domain.tasks import AcceptanceItem, Task


def test_task_requires_observable_acceptance_items() -> None:
    with pytest.raises(ValueError, match="observable_result"):
        AcceptanceItem("AC1", "")


def test_task_rejects_duplicate_acceptance_item_ids() -> None:
    with pytest.raises(ValueError, match="acceptance_item_id"):
        Task(
            task_id="task-1",
            project_id="project-1",
            goal="Create a ticket",
            scope="Ticket creation",
            acceptance_items=(
                AcceptanceItem("AC1", "A created ticket can be queried"),
                AcceptanceItem("AC1", "The ticket is persisted"),
            ),
        )


def test_task_requires_at_least_one_acceptance_item() -> None:
    with pytest.raises(ValueError, match="at least one acceptance item"):
        Task(
            task_id="task-1",
            project_id="project-1",
            goal="Create a ticket",
            scope="Ticket creation",
            acceptance_items=(),
        )
