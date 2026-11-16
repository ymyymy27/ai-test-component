from datetime import UTC, datetime
from time import monotonic


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return monotonic()
