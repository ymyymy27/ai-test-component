"""Atomic JSON publication with bounded transient filesystem retries."""

from __future__ import annotations

import errno as errno_module
import json
import os as os
import sys
import tempfile
import time as time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

TRANSIENT_ERRNOS = {errno_module.EACCES, errno_module.EPERM, errno_module.EBUSY}
TRANSIENT_WINERRORS = {5, 32}
DEFAULT_ATTEMPTS = 14
DEFAULT_BASE_DELAY = 0.02
MAX_DELAY = 0.5
#: 暂存路径必须留在这个长度以内（260 含终止符，留 1 个余量）。
_MAX_STAGE_PATH = 259
#: Windows ``MAX_PATH``（含终止符）：目标路径本身超过它、且宿主未启用长路径支持时无法创建。
_MAX_TARGET_PATH = 259
#: 越界时用于截断目标名提示的字符数。
_STAGE_HINT_CHARS = 24
#: 仍越界时使用的定长短名前缀（不含 mkstemp 的 8 位随机尾）。
_SHORT_STAGE_PREFIX = ".aitest-stage."
#: 超长路径是不可重试的宿主限制，必须说清原因而不是回一个裸 ENOENT。
_LONG_PATH_REASON = (
    "目标路径过长：Windows 未启用长路径支持时（MAX_PATH=260）无法创建该文件；"
    "请把工作空间放到更短的基准目录（例如 C:\\aitest-tmp），或启用系统长路径支持"
)


def is_transient(error: OSError) -> bool:
    """Atomic JSON publication with bounded transient filesystem retries."""
    if error.errno in TRANSIENT_ERRNOS:
        return True
    winerror = getattr(error, "winerror", None)
    return winerror in TRANSIENT_WINERRORS


def configured_attempts() -> int:
    """Atomic JSON publication with bounded transient filesystem retries."""
    raw = os.environ.get("AITEST_REPLACE_ATTEMPTS", "")
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_ATTEMPTS


def replace_with_retry(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    attempts: int | None = None,
    base_delay: float = DEFAULT_BASE_DELAY,
    restage: Callable[[], str | os.PathLike[str]] | None = None,
) -> None:
    """Atomic JSON publication with bounded transient filesystem retries。

    ``restage`` 供发布方在**暂存源文件消失**（``FileNotFoundError``）时按同一份
    已冻结内容重新落一份暂存文件再试。没有它时源缺失即失败关闭，不猜测。
    """
    budget = configured_attempts() if attempts is None else max(1, attempts)
    last_error: OSError | None = None
    for attempt in range(budget):
        try:
            os.replace(source, destination)
            return
        except FileNotFoundError as error:
            # 暂存源文件在发布途中消失（外部清理或中断的发布）：只有发布方
            # 明确提供重落能力时才重试，且内容仍来自已冻结的同一份值。
            if restage is None:
                raise _long_path_failure(error, Path(destination)) from error
            last_error = error
            source = restage()
        except OSError as error:
            if not is_transient(error):
                raise _long_path_failure(error, Path(destination)) from error
            last_error = error
        delay = min(MAX_DELAY, base_delay * (2**attempt))
        print(
            f"[aitest] transient replace denial ({last_error.errno}); "
            f"retry {attempt + 1}/{budget} in {delay:.2f}s",
            file=sys.stderr,
        )
        time.sleep(delay)
    assert last_error is not None
    raise last_error


def _stage_prefix(*, directory: str, target_name: str) -> str:
    """暂存名前缀：默认保持 ``.<目标名>.``，路径过长时退化为定长短名。

    暂存名 ``.<目标名>.<8 位随机尾>`` 会比正式名长约 10 个字符。在**本已很长的
    叶路径**上（例如 ``indexes/business/<族哈希>/<节点哈希>.json``）会把总长推过
    Windows ``MAX_PATH``(260)，此时 ``tempfile.mkstemp`` 会以 ``ENOENT`` 失败
    （实测：暂存路径 267 失败、257 成功；同一调用换成短名即成功）。

    因此这里按可用预算选择前缀：优先保留既有形态（维护例程按 ``.<目标名>.<尾>``
    识别暂存遗留），仅当会越界时才截断目标名提示、继而退化为定长短名。
    """
    budget = _MAX_STAGE_PATH - len(directory) - 1 - 8
    preferred = f".{target_name}."
    if len(preferred) <= budget:
        return preferred
    shortened = f".{target_name[:_STAGE_HINT_CHARS]}."
    if len(shortened) <= budget:
        return shortened
    return _SHORT_STAGE_PREFIX


def _long_path_failure(error: OSError, target: Path) -> OSError:
    """把「超长路径导致的 ENOENT」改写成可定位的宿主限制说明；其它错误原样返回。

    未启用长路径支持的 Windows 上，``CreateFileW`` 对超过 ``MAX_PATH``(260) 的路径直接
    回 ``ENOENT``——错误信息完全不指向真实原因，用户无从修复（实测：目标 277 字符必失败、
    257 字符成功）。这里只在**确实越界**时补上原因，宿主支持长路径时行为不变。
    """

    if os.name != "nt" or getattr(error, "errno", None) != errno_module.ENOENT:
        return error
    resolved = str(target.absolute())
    if len(resolved) <= _MAX_TARGET_PATH:
        return error
    return OSError(
        error.errno,
        f"{error.strerror}：{_LONG_PATH_REASON}（本目标 {len(resolved)} 字符）",
        resolved,
    )


def _refuse_uncreatable_target(path: Path) -> None:
    """目标路径本身已超 ``MAX_PATH`` 时**提前**失败，并说清是宿主限制。

    这种失败与暂存名长度无关（暂存名已在 ``_stage_prefix`` 里缩短过），重试也不会有
    不同结果：继续走 14 次指数退避只会让用户多等约 7 秒，仍拿到一句「找不到路径」。
    因此在写暂存之前直接给出可操作的原因。
    """

    if os.name != "nt":
        return
    resolved = str(path.absolute())
    if len(resolved) > _MAX_TARGET_PATH:
        raise OSError(
            errno_module.ENAMETOOLONG,
            f"{_LONG_PATH_REASON}（本目标 {len(resolved)} 字符）",
            resolved,
        )


def _stage_json(path: Path, value: dict[str, Any]) -> str:
    """把内容写进目标同目录的暂存文件并落盘，返回该暂存路径。"""
    _refuse_uncreatable_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = _stage_prefix(directory=str(path.parent), target_name=path.name)
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=prefix, dir=path.parent)
    except OSError as error:
        raise _long_path_failure(error, path) from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    return temporary


def write_json(
    path: Path,
    value: dict[str, Any],
    *,
    attempts: int | None = None,
    base_delay: float = DEFAULT_BASE_DELAY,
) -> None:
    """Atomic JSON publication with bounded transient filesystem retries。

    暂存文件可能被外部清理掉：此时按同一份已冻结内容重落暂存并重试，
    目标是「要么完整发布、要么保留旧值」，不产生半份文件。
    """
    staged = {"path": _stage_json(path, value)}

    def restage() -> str:
        previous = staged["path"]
        staged["path"] = _stage_json(path, value)
        with suppress(FileNotFoundError):
            os.unlink(previous)
        return staged["path"]

    try:
        replace_with_retry(
            staged["path"], path, attempts=attempts, base_delay=base_delay, restage=restage
        )
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(staged["path"])
        raise
