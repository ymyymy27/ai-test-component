"""OS lifetime lock primitive; workspace admission and epoch management are pending."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import portalocker

from aitest.application.errors import WorkspaceInUse


@contextmanager
def writer_lock(path: Path) -> Iterator[None]:
    lock = portalocker.Lock(
        str(path), mode="a", timeout=0, flags=portalocker.LOCK_EX | portalocker.LOCK_NB
    )
    try:
        lock.acquire()
    except portalocker.exceptions.LockException as error:
        raise WorkspaceInUse("workspace already has a writer") from error
    try:
        yield
    finally:
        lock.release()
