"""A nested file named backup.json must not bypass the exact backup closure."""
import json
from pathlib import Path
import tempfile

from aitest.infrastructure.file_store.backup import FileBackupStore

with tempfile.TemporaryDirectory(prefix='aitest-backup-extra-') as folder:
    parent = Path(folder)
    root = parent / 'workspace'
    (root / 'exports').mkdir(parents=True)
    (root / 'exports/retained.txt').write_text('permanent evidence', encoding='utf-8')
    backup = parent / 'backup'
    store = FileBackupStore(root)
    store.create(backup)
    clean = store.verify(backup)
    added = backup / 'exports/nested/backup.json'
    added.parent.mkdir(parents=True)
    added.write_text('{"unregistered":true}', encoding='utf-8')
    changed = store.verify(backup)
    print(json.dumps({'clean':clean,'unregistered_nested_manifest':changed},ensure_ascii=False))
    raise SystemExit(0 if clean['ok'] and not changed['ok'] else 1)
