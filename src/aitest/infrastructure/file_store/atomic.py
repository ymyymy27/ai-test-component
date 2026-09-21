"""Atomic JSON publication with bounded transient filesystem retries."""

from __future__ import annotations

import errno as errno_module
import json
import os
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

TRANSIENT_ERRNOS = {errno_module.EACCES, errno_module.EPERM, errno_module.EBUSY}
TRANSIENT_WINERRORS = {5, 32}
DEFAULT_ATTEMPTS = 14
DEFAULT_BASE_DELAY = 0.02
MAX_DELAY = 0.5


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
) -> None:
    """Atomic JSON publication with bounded transient filesystem retries."""
    budget = configured_attempts() if attempts is None else max(1, attempts)
    last_error: OSError | None = None
    for attempt in range(budget):
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if not is_transient(error):
                raise
            last_error = error
            delay = min(MAX_DELAY, base_delay * (2**attempt))
            print(
                f"[aitest] transient replace denial ({error.errno}); "
                f"retry {attempt + 1}/{budget} in {delay:.2f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


def write_json(
    path: Path,
    value: dict[str, Any],
    *,
    attempts: int | None = None,
    base_delay: float = DEFAULT_BASE_DELAY,
) -> None:
    """Atomic JSON publication with bounded transient filesystem retries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temporary, path, attempts=attempts, base_delay=base_delay)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
