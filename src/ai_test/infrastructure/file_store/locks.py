from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class WriterLease:
    """Bounded cross-process writer lock backed by portalocker."""

    def __init__(self, path: Path, timeout_seconds: float = 10.0) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds

    @contextmanager
    def acquire(self) -> Iterator[None]:
        import portalocker

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(
            str(self.path),
            mode="a",
            timeout=self.timeout_seconds,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        ):
            yield
