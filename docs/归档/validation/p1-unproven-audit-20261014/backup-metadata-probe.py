"""Unknown format, duplicate fields and file aliases must not be certified."""
import json
from pathlib import Path
import tempfile

from aitest.infrastructure.file_store.backup import BackupError, FileBackupStore

results = []
with tempfile.TemporaryDirectory(prefix='aitest-backup-meta-') as folder:
    parent = Path(folder)
    root = parent / 'workspace'
    (root / 'exports').mkdir(parents=True)
    (root / 'exports/retained.txt').write_text('permanent evidence', encoding='utf-8')
    store = FileBackupStore(root)
    for case in ('unknown_schema', 'duplicate_fields', 'path_alias'):
        backup = parent / case
        store.create(backup)
        path = backup / 'backup.json'
        raw = json.loads(path.read_text(encoding='utf-8'))
        if case == 'unknown_schema':
            raw['schema'] = 'aitest.backup/999.0'
            content = json.dumps(raw)
        elif case == 'duplicate_fields':
            content = '{"schema":"aitest.backup/1.0","files":{},"files":' + json.dumps(raw['files']) + '}'
        else:
            raw['files']['exports/./retained.txt'] = raw['files']['exports/retained.txt']
            content = json.dumps(raw)
        path.write_text(content, encoding='utf-8')
        try:
            result = store.verify(backup)
            rejected = not result['ok']
        except BackupError:
            rejected = True
        results.append({'case':case,'rejected':rejected})
print(json.dumps(results))
raise SystemExit(0 if all(item['rejected'] for item in results) else 1)
