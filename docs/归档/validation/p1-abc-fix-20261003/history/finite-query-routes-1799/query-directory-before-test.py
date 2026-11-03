import json
from pathlib import Path
import pytest
from aitest.contracts.queries import QuerySpec
from aitest.infrastructure.file_store import atomic
from aitest.infrastructure.file_store.index import FileQueryIndex, IndexMissing

def _row(number, *, project='p', kind='case', revision=1, sequence=None):
    result = dict(project_id=project, aggregate_kind=kind, record_id=f'r{number:05}', revision=revision, commit_sequence=sequence or number + 1)
    if kind == 'report':
        result.update(report_id=result['record_id'], content_revision=revision, published_sequence=result['commit_sequence'], business_outcome='passed')
    if kind == 'issue':
        result.update(updated_sequence=result['commit_sequence'], issue_index_entries=[dict(view='ALL', mask=0), dict(view='OPEN', mask=0)])
    return result

def test_page_and_commit_do_not_read_whole_directory_history(tmp_path, monkeypatch):
    index = FileQueryIndex(tmp_path, shard_size=8)
    index.rebuild([_row(i) for i in range(8)] + [_row(i, project='unrelated') for i in range(2048)])
    reads, writes = ([], [])
    original_text, original_bytes = (Path.read_text, Path.read_bytes)
    original_write = atomic.write_json

    def record_read(path):
        if path.is_relative_to(tmp_path):
            reads.append((path, path.stat().st_size))

    def read_text(path, *args, **kwargs):
        record_read(path)
        return original_text(path, *args, **kwargs)

    def read_bytes(path, *args, **kwargs):
        record_read(path)
        return original_bytes(path, *args, **kwargs)

    def write_json(path, value, **kwargs):
        writes.append((path, len(json.dumps(value).encode())))
        return original_write(path, value, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    monkeypatch.setattr(atomic, 'write_json', write_json)
    page = index.query_spec(QuerySpec(project_id='p', limit=5))
    assert page.status == 'ok' and len(page.items) == 5
    assert sum((size for _, size in reads)) < 40000, reads
    reads.clear()
    index.publish([_row(9000, sequence=3000)], commit_sequence=3000)
    assert max((size for _, size in reads)) < 24000, reads
    assert max((size for _, size in writes)) < 24000, writes
    assert sum((size for _, size in reads)) < 150000, reads

@pytest.mark.parametrize('kind', ['report', 'issue'])
def test_corrupt_current_key_ledger_cannot_be_treated_as_empty(tmp_path, kind):
    index = FileQueryIndex(tmp_path, shard_size=8)
    original = _row(1, kind=kind)
    index.rebuild([original])
    header = json.loads((tmp_path / 'indexes.json').read_text())
    snapshot = json.loads((tmp_path / 'indexes/roots' / f"{header['snapshot_root']}.json").read_text())
    ledger = snapshot.get('latest_keys')
    if ledger is None:
        path = tmp_path / 'indexes/.latest-keys.json'
    else:
        path = tmp_path / 'indexes/latest-keys' / f"{ledger['root']['file']}"
    path.write_text('{corrupt', encoding='utf-8')
    root_before = (tmp_path / 'indexes.json').read_bytes()
    with pytest.raises(IndexMissing):
        index.publish([_row(1, kind=kind, revision=2, sequence=3)], commit_sequence=3)
    assert (tmp_path / 'indexes.json').read_bytes() == root_before
