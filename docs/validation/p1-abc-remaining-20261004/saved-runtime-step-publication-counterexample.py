import json
from pathlib import Path

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.contracts.execution_facts import ExecutionFacts


class Unit:
    def next_commit_seq(self):
        return '100'

    def current_revision(self, **kwargs):
        return 0

    def stage_record(self, **kwargs):
        return 1


facts = ExecutionFacts.model_validate_json(
    Path('tests/contracts/fixtures/execution_facts/success.json').read_text(encoding='utf-8'))
for field, replacement in [('case_id', 'case-foreign'), ('required_for_case', False),
                           ('ordinal', 99), ('level', 'L3'), ('assertion_refs', ['fake-basis']),
                           ('dependency_step_ids', ['foreign-upstream'])]:
    coordinator = ExecutionCommitCoordinator(Unit())
    coordinator.read_current_facts = lambda **kwargs: facts
    raw = facts.model_dump(mode='json')
    raw['steps'][0][field] = replacement
    try:
        coordinator._stage_snapshot(ExecutionFacts.model_validate(raw))
    except ValueError:
        result = 'blocked'
    else:
        result = 'accepted'
    print(json.dumps({'frozen_step_field': field, 'publication': result}))
