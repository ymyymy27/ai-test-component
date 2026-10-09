"""路径判定的版本兼容：`Path.is_junction` 自 Python 3.12 起才存在。

`is_junction` 在 3.12+ 直接使用标准库；3.11 按同一语义实现——仅 **junction**（重解析点标签
`IO_REPARSE_TAG_MOUNT_POINT`）为真，符号链接为假，POSIX 恒为假。调用方语义不变。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.version_info >= (3, 12):

    def is_junction(path: str | os.PathLike[str]) -> bool:
        """Python 3.12+：直接使用标准库 `Path.is_junction`。"""
        return Path(path).is_junction()

else:
    _IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x400

    def is_junction(path: str | os.PathLike[str]) -> bool:
        """Python 3.11：按重解析点标签判断是否为 junction（非符号链接、POSIX 恒假）。"""
        try:
            status = os.lstat(path)
        except OSError:
            return False
        tag = getattr(status, "st_reparse_tag", None)
        if tag is not None:
            return bool(tag == _IO_REPARSE_TAG_MOUNT_POINT)
        attributes = getattr(status, "st_file_attributes", 0)
        return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT) and not os.path.islink(path)


__all__ = ["is_junction"]
