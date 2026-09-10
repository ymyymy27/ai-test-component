from typing import Any, Protocol


class TelemetryPort(Protocol):
    def increment(self, metric: str, value: int = 1) -> None: ...

    def health(self) -> dict[str, Any]: ...

