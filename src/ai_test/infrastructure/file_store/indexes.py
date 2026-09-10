from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[dict[str, object], ...]
    next_cursor: str | None = None

