import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory

import pytest

scope = runpy.run_path('tests/unit/test_legacy_event_authority.py')
for field, value in [('records', []), ('commits', [None])]:
    with TemporaryDirectory(prefix='aitest-recovery-structure-') as directory:
        root = Path(directory)
        with pytest.MonkeyPatch.context() as monkey:
            journal, staging = scope['pending_business_event'](root, monkey)
        authority = root / 'records.json'
        body = json.loads(authority.read_bytes())
        body[field] = value
        authority.write_text(json.dumps(body), encoding='utf-8')
        before = staging.read_bytes()
        try:
            result = scope['RecoveryOrchestrator'](root, instance_id='inspection').run()
        except Exception as error:
            observed = {'result': 'unhandled', 'type': type(error).__name__}
        else:
            observed = {'result': result.state}
        print(json.dumps({'changed_field': field, **observed,
                          'staging_retained': staging.exists() and staging.read_bytes() == before,
                          'published_events': len(journal.read().events)}))
