"""pytest 全局夹具。

A-09：进程级已知凭据登记表（``known_secrets()``）在测试之间必须隔离，
否则某个用例解析的凭据会改变后续用例 spool/object/UOW 的落盘前过滤
切点与判定。每个用例开始前清空登记。

临时目录卫生：本机 TEMP 不可写/被沙箱重定向时，``tempfile.mkdtemp`` 会回退到
**仓库根**，备份/迁移等用例（备份位置必须"独立于活动目录"，因此用 mkdtemp 造
外部目标）就会在仓库根累积 ``aitest-*`` 目录，污染 ``git status`` 并有被
``git add -A`` 误提交的风险（本会话实测：一次全量跑留下 466 个目录）。
这里按**会话**清理这些已知前缀，作为各用例自身清理之外的兜底。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aitest.infrastructure.security import known_secrets

#: 只清理这些**测试/调试专用前缀**，且必须在仓库根；不触碰其他目录。
_TEST_TMP_PREFIXES = (
    "aitest-backup-dest-",
    "aitest-backup-ws-",
    "aitest-backup2-",
    "aitest-migrate-ws-",
    "aitest-retry-entry-",
    "aitest-return-entry-",
    "aitest-wheel-",
    # 本会话调试/冒烟脚本用的前缀（同样会因 TEMP 回退落到仓库根）。
    "dsh-",
    "dbg-",
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _clean_test_tmp_dirs() -> int:
    """删除仓库根下已知测试前缀的临时目录；返回删除数量。"""

    root = _repository_root()
    removed = 0
    for child in root.iterdir():
        if (
            child.is_dir()
            and not child.is_symlink()
            and child.name.startswith(_TEST_TMP_PREFIXES)
        ):
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


@pytest.fixture(autouse=True)
def _clear_known_secret_registry() -> None:
    known_secrets().clear()


@pytest.fixture(scope="session", autouse=True)
def _clean_repository_tmp_dirs() -> None:
    """会话开始与结束时各清理一次（开始清历史遗留，结束清本轮遗留）。"""

    _clean_test_tmp_dirs()
    yield
    _clean_test_tmp_dirs()
