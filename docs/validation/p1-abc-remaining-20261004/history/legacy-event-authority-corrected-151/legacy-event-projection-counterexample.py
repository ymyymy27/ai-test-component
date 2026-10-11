import json
from pathlib import Path
from tempfile import TemporaryDirectory

from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.workspace import Workspace

with TemporaryDirectory(prefix='aitest-legacy-event-probe-') as directory:
    root = Path(directory)
    workspace = Workspace(root)
    with workspace.acquire():
        pass
    journal = FileEventJournal(root, instance_id='controlled-old-core')
    args = dict(commit_sequence=1, request_id='uncommitted-request', intent_id='uncommitted-intent',
                workspace_id=workspace.workspace_id, project_id='project', writer_epoch=1)
    journal.begin_boundary(**args)
    journal.record_event(**args, event_type='record_created', aggregate_kind='case', record_id='never-saved', revision=1)
    (root / 'commit.json').write_text(json.dumps({'commits': [{'commit_sequence': 1, 'request_id': 'uncommitted-request'}]}), encoding='utf-8')
    result = RecoveryOrchestrator(root, instance_id='controlled-recovery').run()
    events = journal.read().events
    print(json.dumps(dict(authority_exists=(root / 'records.json').exists(), recovery_state=result.state,
                         completed=list(result.reconcile.completed_boundaries), published_record_ids=[event.record_id for event in events]), ensure_ascii=False))
