"""应用用例统一的错误类型。

原先定义在 2847 行的 `usecase_registry.py` 里；抽出后仍从原模块 re-export，
外部 import 路径不变。
"""

from __future__ import annotations


class BUseCaseError(RuntimeError):
    """带稳定业务编码的用例错误。

    `code` 由入口层解释；应用层不 import `contracts.errors`。
    """

    def __init__(self, code: str, message: str) -> None:
        if not code.strip():
            raise ValueError("error code must not be empty")
        self.code = code
        super().__init__(message)
