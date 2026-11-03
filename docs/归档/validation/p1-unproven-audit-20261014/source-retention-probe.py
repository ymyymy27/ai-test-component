"""Pure component evidence: an admitted source observation was discarded.

Runs the saved f4c9f32 resolver source in memory against validated synthetic
Run/Step/source facts. No process launch, business UOW, real host or AC claim.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root), str(root/'src')]
from tests.unit.test_action_resolver import _resolver, _run, _step, _prepared, _source_observation
from aitest.application.execution.action_resolver import SavedActionResolver

before = subprocess.check_output(['git','show','f4c9f32:src/aitest/application/execution/action_resolver.py'],cwd=root).decode('utf-8')
module = importlib.util.module_from_spec(importlib.util.spec_from_loader('source_resolution_before',loader=None))
sys.modules[module.__name__] = module
exec(compile(before, 'f4c9f32/action_resolver.py', 'exec'), module.__dict__)
rows=[]
for label, cls in [('before',module.SavedActionResolver),('repair',SavedActionResolver)]:
 template=_resolver()
 observed=_source_observation()
 resolver=cls(attempt_index=template._attempt_index,side_effect_class=template._side_effect_class,
  admission=template._admission,pinned_snapshot_ref=template._pinned_snapshot_ref,
  source_verification_provider=lambda *_args: observed)
 action=resolver.resolve(run=_run(),step=_step(),intent_id='component-intent',prepared=_prepared(),
  step_content={'step_id':'step-1','run_id':'run-1'})
 rows.append({'source':label,'observation_preserved':getattr(action,'source_verification',None)==observed})
print(json.dumps({'scope':'component, synthetic facts; no real acceptance','rows':rows},indent=2))
assert rows==[{'source':'before','observation_preserved':False},{'source':'repair','observation_preserved':True}]
