"""Audit probes: real pinned blobs/materialization and Python import, fixture project reader.

Exit zero proves the recorded behavior is reproducible, not that it meets P1.
No product patch and no acceptance status update.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from aitest.bootstrap import _source_verification_provider
from aitest.infrastructure.adapters.execution.python_checks import PythonLoadSourceProbe
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
from aitest.application.project.serialization import project_to_payload
from tests.unit.test_start_source_verification import _Reader, _project, _prepared
from tests.unit.test_authoritative_preparation import _authoritative, prepare
from tests.unit.test_initial_run_registration import register


def default_chain() -> dict[str, object]:
    fixture = ROOT / '.runtime' / ('default-source-audit-' + uuid4().hex)
    fixture.mkdir(parents=True)
    with _authoritative(fixture) as (core, inputs, source):
        prepared = prepare(core, inputs)
        assert prepared.error is None, prepared.error
        facts = register(core, prepared.result)
        saved = core.unit_of_work.repo.read(aggregate_kind='source_snapshot', record_id=inputs.snapshot.source_snapshot_id, revision=1)
        try:
            result = core.execution_authorizations.prepare(project_id=inputs.project_id, run_id=facts.run_id, step_id=facts.steps[0].step_id, intent_id='default-source-audit', request_id='default-source-audit')
            error = None
        except Exception as caught:
            result = None
            error = {'type': type(caught).__name__, 'code': getattr(caught, 'code', None), 'message': str(caught)}
        assert error is not None, 'Default chain behavior changed; review issue'
        return {'scope': 'real core/UOW/source analysis/preparation/registration/default resolver; controlled-authority and environment fixtures; no real Trae acceptance', 'prepared_status': prepared.result['status'], 'business_source_id': inputs.snapshot.source_snapshot_id, 'technical_pinned_id': saved.payload['pinned_snapshot_id'], 'prepared_snapshot_fields': inputs.snapshot.model_dump(mode='json'), 'pinned_fields': sorted(core.snapshot_store.verify_pinned(saved.payload['pinned_snapshot_id'])), 'prepare_execution_error': error, 'result': result}


def run_case(label: str, files: dict[str, str], patterns: tuple[str, ...]) -> dict[str, object]:
    fixture = ROOT / '.runtime' / ('source-audit-' + uuid4().hex)
    assert fixture.resolve().is_relative_to((ROOT / '.runtime').resolve())
    source = fixture / 'source'
    source.mkdir(parents=True)
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    store = FileSourceSnapshotStore(fixture / 'workspace')
    pinned = store.pin(canonical_path=str(source), purpose='P1 source verification audit')
    pinned = store.verify_pinned(str(pinned['snapshot_id']))
    materialized = store.materialize(str(pinned['snapshot_id']), str(fixture / 'fixed'))
    identity = 'sha256:' + hashlib.sha256(json.dumps(pinned, sort_keys=True).encode()).hexdigest()
    prepared = _prepared(identity)
    prepared['snapshot'] = {'snapshot_id': pinned['snapshot_id'], 'content_identity': identity}
    reader = _Reader(project_to_payload(_project(*patterns)))
    real_provider = _source_verification_provider(store, reader)
    failures = {}
    actual_contract = dict(prepared, snapshot={'source_snapshot_id': pinned['snapshot_id'], 'purpose': 'audit', 'content_identity': identity, 'record_revision': 1})
    for variant, content in [('actual_SnapshotRef_fields', actual_contract), ('fixture_snapshot_id_fields_real_store', prepared)]:
        try:
            real_provider(str(fixture / 'fixed'), materialized, content)
        except Exception as error:
            failures[variant] = {'type': type(error).__name__, 'message': str(error)}
    # Explicit compatible boundary double to inspect downstream logic. This is NOT
    # an implemented production integration fix and is not an acceptance proof.
    class CompatibleSnapshotFixture:
        def verify_pinned(self, snapshot_id):
            return {**store.verify_pinned(snapshot_id), 'content_identity': identity}
    provider = _source_verification_provider(CompatibleSnapshotFixture(), reader)
    result = provider(str(fixture / 'fixed'), materialized, prepared)
    syntax_errors = []
    for name, content in files.items():
        try:
            ast.parse(content, filename=name)
        except SyntaxError as error:
            syntax_errors.append({'path': name, 'error': error.msg})
    modules = [pattern.replace('/', '.') for pattern in patterns if (fixture / 'fixed' / pattern).is_dir()]
    observed = PythonLoadSourceProbe(executable=sys.executable).probe(materialized_root=fixture / 'fixed', modules=modules)
    return {
        'case': label,
        'fixture': str(fixture),
        'pinned_identity': identity,
        'materialization_verified': materialized['verified'],
        'syntax_errors': syntax_errors,
        'actual_imports': [{'module': m.module, 'file': m.file, 'digest': m.sha256, 'error': m.load_error} for m in observed.modules],
        'provider_state': result.state.value,
        'provider_gaps': list(result.gap_ids),
        'provider_selected_import': result.observed_import_ref,
        'real_provider_errors': failures,
        'compatibility_fixture_used_for_downstream_state': True,
    }


cases = [
    run_case('invalid_flat_entry', {'entry.py': 'def broken(:\n'}, ('entry.py',)),
    run_case('good_and_broken_packages', {'entry.py': 'VALUE = 1\n', 'good/__init__.py': 'VALUE = 1\n', 'bad/__init__.py': 'def broken(:\n'}, ('good', 'bad')),
    run_case('namespace_with_broken_child', {'entry.py': 'VALUE = 1\n', 'pkg/child.py': 'def broken(:\n', 'good/__init__.py': 'VALUE = 1\n'}, ('pkg', 'good')),
]
assert all(case['materialization_verified'] is True for case in cases)
assert all(case['syntax_errors'] for case in cases)
assert all(case['provider_state'] == 'verified' for case in cases), 'Behavior changed: review the recorded issue'
payload = {'baseline': '708439bf0152008ea981cfb89c68d64538b2e57d', 'scope': 'real default chain plus source provider boundary; downstream false verification uses explicitly marked compatibility fixture', 'default_chain': default_chain(), 'cases': cases}
output = Path(__file__).with_suffix('.json')
output.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'output': str(output), 'default_error': payload['default_chain']['prepare_execution_error']['type'], 'bad_verified_cases': len(cases)}, ensure_ascii=True))
