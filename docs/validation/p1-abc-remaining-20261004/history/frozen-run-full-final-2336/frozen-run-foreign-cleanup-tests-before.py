"""The acquiring thread must see a lock ended when another thread finalizes it."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from aitest.infrastructure.file_store.locking import LifetimeWriterLock, writer_lock


@pytest.mark.parametrize("with_lifetime", [False, True])
def test_foreign_thread_finalization_clears_the_actual_owner(
    tmp_path: Path, with_lifetime: bool
) -> None:
    path = tmp_path / "writer.lock"
    lifetime = LifetimeWriterLock(path) if with_lifetime else None
    if lifetime is not None:
        lifetime.acquire()

    with ThreadPoolExecutor(max_workers=2) as executor:

        def acquire_and_collect() -> None:
            holder = [writer_lock(path)]
            holder[0].__enter__()
            # Last-reference destruction runs the generator cleanup on the other thread.
            executor.submit(holder.clear).result(timeout=10)
            with writer_lock(path):
                pass
            if lifetime is not None:
                lifetime.release()
                lifetime.acquire()
                with writer_lock(path):
                    pass

        try:
            executor.submit(acquire_and_collect).result(timeout=20)
        finally:
            if lifetime is not None:
                lifetime.release()
