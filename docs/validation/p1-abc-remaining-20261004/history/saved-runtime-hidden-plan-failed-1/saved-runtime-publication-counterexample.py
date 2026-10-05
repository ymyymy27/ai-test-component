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
for field in ('plan_revision', 'environment_ref', 'environment_isolation_mode',
              'rules_revision', 'conclusion_ceiling'):
    coordinator = ExecutionCommitCoordinator(Unit())
    coordinator.read_current_facts = lambda **kwargs: facts
    raw = facts.model_dump(mode='json')
    if field == 'plan_revision':
        raw['plan_revision']['digest'] = 'sha256:other'
        raw['run']['plan_revision']['digest'] = 'sha256:other'
    else:
        raw['run'][field] = ({'environment_isolation_mode': 'none',
            'conclusion_ceiling': 'partial'}.get(field, 'changed'))
    try:
        coordinator._stage_snapshot(ExecutionFacts.model_validate(raw))
    except ValueError:
        result = 'blocked'
    else:
        result = 'accepted'
    print(json.dumps({'frozen_basis_field': field, 'publication': result}))
