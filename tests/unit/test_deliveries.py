import pytest

from aitest.domain.project.context import Delivery


def test_delivery_rejects_overlapping_completion_lists() -> None:
    with pytest.raises(ValueError, match="must not overlap"):
        Delivery(
            delivery_id="delivery-1",
            task_id="task-1",
            version="v1",
            completed=("item",),
            incomplete=("item",),
            run_method="pytest",
        )


def test_delivery_requires_run_method() -> None:
    with pytest.raises(ValueError, match="run_method"):
        Delivery(
            delivery_id="delivery-1",
            task_id="task-1",
            version="v1",
            run_method="",
        )
