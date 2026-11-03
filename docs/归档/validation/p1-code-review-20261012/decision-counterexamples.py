"""Reproduce D-10 on the audited baseline; this is defect evidence, not acceptance."""

import copy
import json
from pathlib import Path

from aitest.application.review.execution_decision_adapter import (
    AssertionOutcome,
    AssertionOutcomeFact,
    DecisionContextFacts,
    build_decision_facts,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.review.reports import evaluate_review

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / 'docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures/success.json'
original = json.loads(FIXTURE.read_text(encoding='utf-8'))
outcome = AssertionOutcomeFact(
    case_id='case-1', step_id='step-1', attempt_id='attempt-1',
    assertion_ref='assertion-1', basis_revision=1, outcome=AssertionOutcome.PASSED,
    evidence_refs=('evidence-1',), case_assertions_complete=True,
    necessary_assertion=True, basis_confirmed=True, verification_valid=True,
    dependencies_valid=True,
)
context = DecisionContextFacts(template_required_case_ids=frozenset({'case-1'}))
results = []

for label in ('control', 'required_evidence_conflict', 'source_unverified', 'attempt_not_current'):
    raw = copy.deepcopy(original)
    if label == 'required_evidence_conflict':
        raw['steps'][0]['evidence_requirement_ids'] = ['required-stderr']
        raw['evidence_refs'][1]['integrity'] = 'conflict'
    elif label == 'source_unverified':
        extra = copy.deepcopy(raw['source_verifications'][0])
        extra['verification_id'] = 'source-verify-unverified'
        extra['state'] = 'unverified'
        raw['source_verifications'].append(extra)
    elif label == 'attempt_not_current':
        raw['attempts'][0]['is_current'] = False
    facts = ExecutionFacts.model_validate(raw)
    decision_facts = build_decision_facts(facts, assertion_outcomes=(outcome,), context=context)
    decision = evaluate_review(decision_facts)
    results.append({
        'case': label, 'schema_accepted': True,
        'executed': sorted(decision_facts.coverage.executed),
        'verified': sorted(decision_facts.coverage.verified),
        'required_evidence_valid': decision_facts.required_evidence_valid,
        'source_identity_state': decision_facts.source_identity_state.value,
        'business_outcome': decision.business_outcome.value,
        'evidence_grade': decision.evidence_grade.value if decision.evidence_grade else None,
    })

print(json.dumps(results, ensure_ascii=False, indent=2))
