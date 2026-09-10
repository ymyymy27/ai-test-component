from typing import Any


class InMemoryTelemetry:
    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def increment(self, metric: str, value: int = 1) -> None:
        self._counters[metric] = self._counters.get(metric, 0) + value

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "counters": dict(self._counters)}

