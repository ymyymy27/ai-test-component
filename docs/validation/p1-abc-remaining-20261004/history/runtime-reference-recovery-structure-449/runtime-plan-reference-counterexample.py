from dataclasses import replace
import json
import runpy

scope = runpy.run_path('tests/unit/test_runtime_revision.py')
plan = replace(scope['_plan'](revision=7), record_revision=2)
for requested in (2, 7):
    payload = scope['_failure_in_progress']()
    payload['plan_revision']['revision_no'] = 2
    payload['run']['plan_revision']['revision_no'] = 2
    decision = scope['_decide'](payload, plan=plan, base_plan_revision_no=requested)
    print(json.dumps({'body_revision': 7, 'record_revision': 2, 'requested_revision': requested,
                      'accepted': decision.accepted, 'refusals': sorted(scope['_codes'](decision))}))

for field, value in [('revision_id', 'foreign-plan'), ('revision_no', 8), ('digest', 'sha256:foreign')]:
    payload = scope['_failure_in_progress']()
    payload['run']['plan_revision'][field] = value
    try:
        facts = scope['ExecutionFacts'].model_validate(payload)
        view = scope['runtime_facts_from_execution_facts'](facts)
    except ValueError:
        print(json.dumps({'mismatched_run_plan_field': field, 'translation': 'blocked'}))
    else:
        print(json.dumps({'mismatched_run_plan_field': field, 'translation': 'accepted',
                          'plan_used': view.plan_revision_id, 'revision_used': view.plan_revision_no}))
