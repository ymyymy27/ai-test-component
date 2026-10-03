"""pytest 全局夹具。

A-09：进程级已知凭据登记表（``known_secrets()``）在测试之间必须隔离，
否则某个用例解析的凭据会改变后续用例 spool/object/UOW 的落盘前过滤
切点与判定。每个用例开始前清空登记。
"""

from __future__ import annotations

import pytest

from aitest.infrastructure.security import known_secrets


@pytest.fixture(autouse=True)
def _clear_known_secret_registry() -> None:
    known_secrets().clear()
