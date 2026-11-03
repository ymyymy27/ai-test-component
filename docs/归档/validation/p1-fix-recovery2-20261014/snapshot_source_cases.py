"""Save only the two fully completed source/FileUOW cases, not a full verdict."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
BASE='tests/unit/test_default_start_source_chain.py::test_default_source_observation_is_atomic_and_readable_after_restart'
nodes=[BASE+'[False]',BASE+'[True]']
rows=[json.loads(line) for line in (OUT/'case-results.jsonl').read_text(encoding='utf-8').splitlines()]
selected=[row for row in rows if row['nodeid'] in nodes]
complete=len(selected)==6 and all(len([row for row in selected if row['nodeid']==node and row['when']==phase and row['outcome']=='passed'])==1 for node in nodes for phase in ('setup','call','teardown'))
if not complete:
    print('The two exact parameterized cases have not both completed; no pass snapshot written.')
    raise SystemExit(2)
inventory=json.loads((OUT/'source-inventory.json').read_text(encoding='utf-8'))
drift=[row['path'] for row in inventory if hashlib.sha256((ROOT/row['path']).read_bytes()).hexdigest()!=row['sha256']]
assert not drift,drift
checkpoint=json.loads((OUT/'full-command.json').read_text(encoding='utf-8'))['source_commit']
result={'result':'passed','source_commit':checkpoint,'saved_at':datetime.now(timezone.utc).isoformat(),
        'scope':'two exact real probe/FileUOW component tests only; human consent/environment claims remain fixtures; full suite and real product acceptance not inferred',
        'checked_paths':len(inventory),'source_drift':drift,'phases':selected}
(OUT/'source-cases-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Two exact component cases passed with all six phase receipts; source unchanged.')
