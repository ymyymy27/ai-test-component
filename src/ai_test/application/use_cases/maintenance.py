from typing import Any

from ai_test.application.ports.telemetry import TelemetryPort


def doctor(telemetry: TelemetryPort) -> dict[str, Any]:
    return telemetry.health()
