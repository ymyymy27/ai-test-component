import copy
import json
from pathlib import Path

import pytest

from aitest.application.planning.run_mode import runtime_facts_from_execution_facts
from aitest.contracts.execution_facts import ExecutionFacts

ROOT = Path.cwd()


@pytest.mark.parametrize('damage', [
    'runtime_refs', 'step_owner', 'attempt_step', 'attempt_owner',
    'non_current_attempt', 'run_revision', 'duplicate_step', 'extra_map_key',
])
def test_inconsistent_runtime_facts_cannot_be_used_for_revision_authority(damage):
    body = json.loads((ROOT / 'docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures/failure.json').read_text(encoding='utf-8'))
    assert body['steps'] and body['attempts']
    if damage == 'runtime_refs':
        body['runtime_revision_refs'] = ['revision-real']
        body['run']['runtime_revision_refs'] = ['revision-other']
    elif damage == 'step_owner':
        body['steps'][0]['run_id'] = 'foreign-run'
    elif damage == 'attempt_step':
        body['attempts'][0]['step_id'] = 'foreign-step'
    elif damage == 'attempt_owner':
        body['attempts'][0]['run_id'] = 'foreign-run'
    elif damage == 'non_current_attempt':
        body['attempts'][0]['is_current'] = False
    elif damage == 'run_revision':
        body['run_revision'] += 1
    elif damage == 'duplicate_step':
        body['steps'].append(copy.deepcopy(body['steps'][0]))
    elif damage == 'extra_map_key':
        body['current_attempt_by_step']['foreign-step'] = None
    with pytest.raises(ValueError):
        runtime_facts_from_execution_facts(ExecutionFacts.model_validate(body))
