import asyncio
import json

from ai_test.interfaces.asgi_host import AitestASGIApp


def request(path: str) -> tuple[int, dict[str, object]]:
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b""}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    scope = {"type": "http", "method": "GET", "path": path}
    asyncio.run(AitestASGIApp()(scope, receive, send))
    status = int(messages[0]["status"])
    body = json.loads(bytes(messages[1]["body"]))
    return status, body


def test_health_endpoint() -> None:
    status, body = request("/health")
    assert status == 200
    assert body == {"status": "ok"}


def test_unknown_capability_is_explicit() -> None:
    status, body = request("/runs")
    assert status == 501
    assert body["error"] == "CAPABILITY_NOT_IMPLEMENTED"

