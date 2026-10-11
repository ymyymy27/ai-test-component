from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json

from aitest.application.errors import WorkspaceInUse
from aitest.infrastructure.file_store.locking import LifetimeWriterLock, writer_lock

root = Path('.git/p1-abc-fix-20261003/foreign-thread-lock-probe').resolve()
path = root / 'writer.lock'
lifetime = LifetimeWriterLock(path)
lifetime.acquire()
box = [writer_lock(path)]
box[0].__enter__()
observed = {}
with ThreadPoolExecutor(max_workers=1) as executor:
    # Drop the last context reference on the other thread: ordinary Python cleanup.
    executor.submit(box.clear).result()
    try:
        with writer_lock(path):
            observed['next_owner'] = 'acquired'
    except WorkspaceInUse as error:
        observed['next_owner'] = str(error)
    try:
        lifetime.release()
        observed['lifetime_release'] = 'released'
    except WorkspaceInUse as error:
        observed['lifetime_release'] = str(error)
    executor.submit(lifetime.release).result()
print(json.dumps(observed))
