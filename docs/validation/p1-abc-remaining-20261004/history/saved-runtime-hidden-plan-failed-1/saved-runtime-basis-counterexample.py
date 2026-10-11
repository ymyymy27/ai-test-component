from dataclasses import replace
import json
import runpy

scope = runpy.run_path('tests/unit/test_runtime_revision.py')
original = scope['_case']()
for kind in ('independent_verification_removed', 'mandatory_link_removed'):
    forged = (replace(original, independent_verification=None)
              if kind == 'independent_verification_removed'
              else replace(original, links=replace(original.links,
                  acceptance_item_ids=frozenset({'AC-substitute'}))))
    change = scope['_change'](forged)
    decision = scope['_decide'](scope['_failure_in_progress'](), frozen_case=forged, change=change)
    authoritative = scope['_decide'](scope['_failure_in_progress'](), frozen_case=original, change=change)
    print(json.dumps(dict(counterexample=kind, supplied_same_id_revision_accepted=decision.accepted,
        actual_frozen_content_accepted=authoritative.accepted,
        actual_refusals=sorted(scope['_codes'](authoritative))), ensure_ascii=False))
