"""Stable error codes and exceptions for the lightweight plugin CLI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ErrorInfo:
    code: str
    message: str
    next_step: str = ""

    def to_dict(self) -> dict[str, str]:
        data = {"code": self.code, "message": self.message}
        if self.next_step:
            data["next_step"] = self.next_step
        return data


class AiTestError(Exception):
    """A business rejection with a stable code."""

    def __init__(self, code: str, message: str, next_step: str = "") -> None:
        super().__init__(message)
        self.info = ErrorInfo(code=code, message=message, next_step=next_step)


def invalid_request(message: str, next_step: str = "修正请求字段后重试") -> AiTestError:
    return AiTestError("INVALID_REQUEST", message, next_step)


def invalid_path(message: str, next_step: str = "换一个存在且可写的目录") -> AiTestError:
    return AiTestError("INVALID_PATH", message, next_step)


def not_found(code: str, message: str, next_step: str) -> AiTestError:
    return AiTestError(code, message, next_step)
