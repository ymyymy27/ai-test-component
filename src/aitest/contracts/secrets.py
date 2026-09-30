"""Secret resolution contracts shared by application ports and adapters.

凭据值只存在于受控内存，永不进入：命令行、普通临时文件、检查点、日志、
配置、面板 DTO、导出或上传。合同类型不提供任何可逆恢复信息。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

# 一期受控用途集合；新增用途须显式登记。
SecretPurpose = Literal["model", "http"]
KNOWN_PURPOSES: Final[tuple[str, ...]] = ("model", "http")


@dataclass(slots=True)
class ResolvedSecret:
    """一次受控解析的结果；默认表示绝不暴露值。

    调用方在受控进程内通过 :meth:`reveal` 显式取值；使用完毕应
    :meth:`clear`。``str()`` 与 ``repr()`` 均只暴露元数据。
    """

    purpose: str
    reference: str
    source: str
    _value: str

    def reveal(self) -> str:
        """显式取出明文字段值（仅内存）。"""
        return self._value

    def clear(self) -> None:
        """用空字符串替换内存引用。"""
        self._value = ""

    def __str__(self) -> str:
        return f"ResolvedSecret(purpose={self.purpose!r}, reference={self.reference!r})"

    def __repr__(self) -> str:
        return (
            "ResolvedSecret("
            f"purpose={self.purpose!r}, reference={self.reference!r}, "
            f"source={self.source!r}, value='***')"
        )


__all__ = ["KNOWN_PURPOSES", "ResolvedSecret", "SecretPurpose"]
