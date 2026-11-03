"""Observe the serial queue, then verify saved artifact bytes without running tests."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def save(name, data):
    target = OUT / name
    temporary = target.with_suffix(target.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, target)


def now():
    return datetime.now(timezone.utc).isoformat()


if (OUT / 'artifact-check-state.json').exists():
    raise RuntimeError('artifact observer already registered; refuse duplicate execution')
save('artifact-check-state.json', {'state': 'waiting_for_build', 'runner_pid': os.getpid(), 'started_at': now()})
try:
    deadline = time.monotonic() + 60
    while not (OUT / 'queue-state.json').exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('queue did not publish its initial state within 60 seconds')
        time.sleep(0.1)
    while True:
        state = json.loads((OUT / 'queue-state.json').read_text(encoding='utf-8'))
        completed = OUT / 'wheel-smoke-result.json'
        if completed.exists():
            result = json.loads(completed.read_text(encoding='utf-8'))
            if result['exit_code'] != 0:
                raise RuntimeError('queued wheel smoke failed; no artifact pass inferred')
            break
        if state['state'] in {'failed', 'queue_blocked', 'finished'}:
            raise RuntimeError('queue ended without a successful wheel smoke')
        time.sleep(30)

    before = json.loads((OUT / 'artifact-inventory-before-queue.json').read_text(encoding='utf-8'))
    changed = [item['path'] for item in before if not (ROOT/item['path']).is_file() or hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest() != item['sha256']]
    save('generated-artifact-drift.json', {'checked_at': now(), 'changed_paths': changed, 'scope': 'tracked source, tests, configuration, lock, schemas and packaging definitions; generated ignored dist checked separately'})
    if changed:
        raise RuntimeError('tracked artifact drift requires review before declaring packaging verified')

    reference = json.loads((OUT / 'vsix-reference.json').read_text(encoding='utf-8'))
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    wheel = ROOT / 'dist' / f'ai_test_component-{version}-py3-none-any.whl'
    vsix = ROOT / 'integrations/trae/dist/aitest-trae.vsix'
    if hashlib.sha256(vsix.read_bytes()).hexdigest() != reference['sha256']:
        raise RuntimeError('reference VSIX bytes changed')
    with zipfile.ZipFile(wheel) as package, zipfile.ZipFile(vsix) as extension:
        names = package.namelist()
        assert len([p for p in names if '/templates/' in p and p.endswith('.json')]) == 6
        assert 'aitest/contracts/schemas/Command.json' in names
        assert not any('node_modules' in p or p.startswith('ai_test/') for p in names)
        panel = package.read('aitest/resources/panel/dist/index.js')
        assert panel == extension.read('extension/dist/panel.js')
        assert panel == (ROOT / 'src/aitest/resources/panel/dist/index.js').read_bytes()
        objects = 'aitest/domain/evidence/objects.py'
        assert package.read(objects) == (ROOT / 'src/aitest/domain/evidence/objects.py').read_bytes()
    data = {
        'result': 'passed', 'ended_at': now(), 'candidate_source_commit': reference['candidate_source_commit'],
        'scope': 'candidate wheel contents and shared panel bytes; unchanged reference VSIX, no real Trae acceptance',
        'artifacts': [{'path': p.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in (wheel, vsix)],
        'tracked_artifact_drift_count': len(changed),
    }
    save('queued-artifacts-result.json', data)
    save('artifact-check-state.json', {'state': 'finished', 'exit_code': 0, 'ended_at': now()})
except Exception as error:
    save('artifact-check-state.json', {'state': 'not_verified', 'exception_type': type(error).__name__, 'safe_reason': str(error), 'ended_at': now()})
    raise
