from dataclasses import asdict, replace
from typing import Any

from ai_test.application.ports.storage import RecordRepository
from ai_test.application.use_cases.task_context import TaskContext
from ai_test.domain.deliveries import Delivery


class DeliveryContext:
    def __init__(self, records: RecordRepository, tasks: TaskContext) -> None:
        self._records = records
        self._tasks = tasks

    def create(self, delivery: Delivery) -> Delivery:
        if self._tasks.get(delivery.task_id) is None:
            raise ValueError(f"task not found: {delivery.task_id}")
        revision = self._records.put(
            "deliveries", delivery.delivery_id, asdict(delivery), expected_revision=0
        )
        return replace(delivery, revision=revision)

    def get(self, delivery_id: str) -> Delivery | None:
        record = self._records.get("deliveries", delivery_id)
        if record is None:
            return None
        return self._from_record(record)

    def list(self, task_id: str | None = None) -> tuple[Delivery, ...]:
        deliveries = (
            self._from_record(record) for record in self._records.list("deliveries")
        )
        if task_id is None:
            return tuple(deliveries)
        return tuple(delivery for delivery in deliveries if delivery.task_id == task_id)

    @staticmethod
    def _from_record(record: dict[str, Any]) -> Delivery:
        for field_name in (
            "completed",
            "incomplete",
            "changed_modules",
            "api_changes",
            "test_data",
            "dependencies",
            "mocks",
            "known_issues",
            "self_test_evidence",
        ):
            record[field_name] = tuple(record.get(field_name, ()))
        return Delivery(**record)