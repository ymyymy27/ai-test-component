import json
from typing import Any

from ai_test.application.use_cases.capabilities import describe_capabilities


class AitestASGIApp:
    """Dependency-free ASGI surface for host health and capability discovery."""

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            return
        path = scope.get("path", "/")
        method = scope.get("method", "GET")
        if method == "GET" and path == "/health":
            await self._json(send, 200, {"status": "ok"})
        elif method == "GET" and path == "/capabilities":
            await self._json(send, 200, describe_capabilities())
        else:
            await self._json(
                send,
                501,
                {"error": "CAPABILITY_NOT_IMPLEMENTED", "method": method, "path": path},
            )

    @staticmethod
    async def _json(send: Any, status: int, value: dict[str, Any]) -> None:
        content = json.dumps(value, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    [b"content-type", b"application/json; charset=utf-8"],
                    [b"content-length", str(len(content)).encode("ascii")],
                ],
            }
        )
        await send({"type": "http.response.body", "body": content})

