"""Immutable object identities shared by evidence and source observations."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StoredObjectRef:
    project_id: str
    digest: str
    size: int
    media_type: str
    relative_path: str

    def __post_init__(self) -> None:
        for name in ("project_id", "digest", "media_type", "relative_path"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if self.size < 0:
            raise ValueError("size must be non-negative")
