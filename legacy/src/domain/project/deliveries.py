"""A formal delivery freezes references; it never turns declarations into verification."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeliverySubmission:
    submission_id: str
    project_id: str
    version: str
    delivery_id: str
    delivery_record_revision: int
    task_id: str
    task_record_revision: int
    snapshot_id: str
    snapshot_record_revision: int
    binding_id: str
    binding_record_revision: int
    content_identity: str
    acceptance_item_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "submission_id",
            "project_id",
            "version",
            "delivery_id",
            "task_id",
            "snapshot_id",
            "binding_id",
            "content_identity",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("formal delivery requires a nonempty frozen identity")
        for name in (
            "delivery_record_revision",
            "task_record_revision",
            "snapshot_record_revision",
            "binding_record_revision",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError("formal delivery requires exact warehouse revisions")
        if (
            not self.acceptance_item_ids
            or any(
                not isinstance(item, str) or not item.strip() for item in self.acceptance_item_ids
            )
            or len(set(self.acceptance_item_ids)) != len(self.acceptance_item_ids)
        ):
            raise ValueError("formal delivery requires the unique frozen task acceptance scope")
