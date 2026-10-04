import hashlib
import json
import platform
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path.cwd()))

from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.publication_backend import FilePublicationBackend, WindowsFileAPI
from tests.unit.test_complete_commit_closure import commit, facts

root = Path.cwd()
out = root / 'docs/validation/p1-abc-fix-20261003'
archive = out / 'history/publication-prototype-1928/validation-source.zip'
old_name = 'src/aitest/infrastructure/file_store/commit_manifest.py'
with zipfile.ZipFile(archive) as saved:
    old = saved.read(old_name)
assert b'CURRENT_SCHEMA = "aitest.current-commit/1"' in old
assert b'atomic.write_json(self.current_path, pointer)' in old
records = []
calls = dict(flush=0, replace=0, initialize=0)
original_flush, original_replace, original_initial = (
    WindowsFileAPI.flush, WindowsFileAPI.replace, WindowsFileAPI.initialize
)

def flush(self, descriptor):
    calls['flush'] += 1
    return original_flush(self, descriptor)

def replace(self, current, candidate, backup):
    calls['replace'] += 1
    return original_replace(self, current, candidate, backup)

def initialize(self, candidate, current):
    calls['initialize'] += 1
    return original_initial(self, candidate, current)

with tempfile.TemporaryDirectory(prefix='aitest-native-publication-') as directory:
    workspace = Path(directory) / 'workspace'
    with patch.object(WindowsFileAPI, 'flush', flush), patch.object(
        WindowsFileAPI, 'replace', replace
    ), patch.object(WindowsFileAPI, 'initialize', initialize):
        core = assemble_workspace_core(workspace, instance_id='publication-component-probe')
        try:
            start = (workspace / 'current.json').read_bytes()
            current = FileCommitStore(workspace).read_current(verify_material=True)
            assert len(current['pointer']) == 7
            original_switch = FilePublicationBackend._replace
            with patch.object(FilePublicationBackend, '_replace', side_effect=OSError('denied')):
                try:
                    commit(workspace, ('one',), request='one', intent='intent')
                    raise AssertionError('unexpected acknowledgement')
                except OSError:
                    pass
            assert (workspace / 'current.json').read_bytes() == start
            assert facts(workspace, 'one')[:5] == (0, 0, 'ok', 0, 0)
            records.append(dict(cut='replacement_denied', acknowledged=False,
                                root_unchanged=True, commit=0, revision=0,
                                inspection=FilePublicationBackend(workspace).inspect_publication()))

            def lost_response(self, candidate, backup, *, initial):
                original_switch(self, candidate, backup, initial=initial)
                raise OSError('lost API response')

            with patch.object(FilePublicationBackend, '_replace', lost_response):
                _, result = commit(workspace, ('one',), request='retry', intent='intent')
            assert result['commit_sequence'] == 1
            _, recalled = commit(workspace, ('one',), request='other-entry', intent='intent')
            assert recalled['commit_sequence'] == 1
            proof = facts(workspace, 'one')
            assert proof[:5] == (1, 1, 'ok', 1, 1) and len(proof[6]) == 1
            records.append(dict(cut='lost_response_after_actual_ReplaceFileW',
                                acknowledged=True, intent_recalled=True, commit=1,
                                revision=1, event_count=1, query_status=proof[2],
                                query_event_cursor_present=proof[5] is not None))
            backend = FilePublicationBackend(workspace)
            marker = json.loads(backend.marker.read_bytes())
            sealed = json.loads((backend.directory/f"{marker['descriptor_digest']}.json").read_bytes())
            backup = backend.directory/sealed['attempt']/'backup.json'
            assert backup.read_bytes() == start
            pointer = FileCommitStore(workspace).read_current(verify_material=True)['pointer']
            assert calls['replace'] == 1 and calls['initialize'] == 1 and calls['flush'] > 0
            result = dict(status='verified_component_behavior', platform=platform.platform(),
                          python=platform.python_version(), before=dict(
                              kind='frozen_source_contract_gap_not_a_runtime_failure',
                              archive=str(archive.relative_to(out)).replace('\\','/'),
                              archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                              source=old_name, source_sha256=hashlib.sha256(old).hexdigest(),
                              pointer_fields=4, publication_api='atomic.write_json/os.replace'),
                          actual_native_calls=calls, pointer_fields=sorted(pointer),
                          backup_matches_previous=True, scenarios=records,
                          remaining=['full normative manifest field/reference closure',
                                     'evidence object and business change index closure',
                                     'actual core emitter origin', 'bounded ordinary startup',
                                     'manual uncertain-publication recovery workflow',
                                     'target-device physical power-loss validation',
                                     'default business/Trae/AC acceptance'])
        finally:
            core.lifetime_lock.release()
result['sources'] = [dict(path=str(path.relative_to(root)).replace('\\','/'),
                          sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in (
    root/'src/aitest/infrastructure/file_store/publication_backend.py',
    root/old_name,
    root/'src/aitest/infrastructure/file_store/records.py',
    root/'src/aitest/infrastructure/file_store/unit_of_work.py',
    root/'src/aitest/infrastructure/file_store/migrations.py',
    root/'src/aitest/bootstrap.py',
    root/'tests/unit/test_windows_publication_integrity.py',
)]
(out/'windows-publication-probe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n',
                                              encoding='utf-8')
print(json.dumps(dict(status=result['status'], native_calls=calls, fields=len(pointer)),ensure_ascii=False))
