"""The single bounded transport retry policy; no execution or sleeping here."""


def retry_delay(retries_completed: int, *, read_only: bool, idempotency_proven: bool) -> int | None:
    if retries_completed < 0:
        raise ValueError("retry count cannot be negative")
    if not (read_only or idempotency_proven) or retries_completed >= 3:
        return None
    return (2, 5, 10)[retries_completed]
