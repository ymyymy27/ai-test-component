"""Verify this one run's complete node/phase ledger, JUnit and source bytes."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent


def main():
    receipt=json.loads((OUT/'full-result.json').read_text(encoding='utf-8'))
    inventory=json.loads((OUT/'source-inventory.json').read_text(encoding='utf-8'))
    collection=json.loads((OUT/'collected-nodes.json').read_text(encoding='utf-8'))
    rows=[json.loads(line) for line in (OUT/'case-results.jsonl').read_text(encoding='utf-8').splitlines()]
    expected=json.loads((OUT/'facts.json').read_text(encoding='utf-8'))['collected_nodes']
    nodes=set(collection['nodeids'])
    phases=Counter((r['nodeid'],r['when']) for r in rows)
    duplicates=[{'nodeid':node,'phase':phase,'count':count} for (node,phase),count in phases.items() if count!=1]
    unfinished=[]
    for node in nodes:
        actual=[r for r in rows if r['nodeid']==node]
        setup=[r for r in actual if r['when']=='setup']
        call=[r for r in actual if r['when']=='call']
        teardown=[r for r in actual if r['when']=='teardown']
        if len(setup)!=1 or len(teardown)!=1 or (setup[0]['outcome']=='passed' and len(call)!=1):
            unfinished.append(node)
    xml=OUT/'full.xml'
    cases=ET.parse(xml).getroot().findall('.//testcase') if xml.exists() else []
    failed=[r for r in rows if r['outcome']=='failed']
    skipped=[r for r in rows if r['outcome']=='skipped']
    drift=[r['path'] for r in inventory if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()!=r['sha256']]
    foreign=sorted({r['nodeid'] for r in rows}-nodes)
    passed=(receipt['exit_code']==0 and len(nodes)==expected and len(cases)==expected
            and not (duplicates or unfinished or failed or drift or foreign)
            and not any(case.find('failure') is not None or case.find('error') is not None for case in cases))
    summary={'result':'passed' if passed else 'not_verified','source_commit':receipt['source_commit'],
             'verified_at':datetime.now(timezone.utc).isoformat(),'exit_code':receipt['exit_code'],
             'collected_unique':len(nodes),'junit_cases':len(cases),'call_outcomes':dict(Counter(r['outcome'] for r in rows if r['when']=='call')),
             'skipped':skipped,'source_drift':drift,'duplicate_phases':duplicates,'unfinished_nodes':unfinished,'foreign_nodes':foreign,
             'scope':'one full run only; conditional skips retained explicitly; does not prove real host/product acceptance'}
    (OUT/'full-verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:summary[k] for k in ('result','collected_unique','junit_cases','exit_code')}))
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
