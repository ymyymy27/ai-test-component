"""Freeze completed whole files from this one run; preserve conditional skips."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
destination=OUT/sys.argv[1]
assert destination.parent==OUT and not destination.exists()
rows=[json.loads(line) for line in (OUT/'case-results.jsonl').read_text(encoding='utf-8').splitlines()]
by_node=defaultdict(list)
for row in rows:
    by_node[row['nodeid']].append(row)
files=defaultdict(list)
for node in json.loads((OUT/'collected-nodes.json').read_text(encoding='utf-8'))['nodeids']:
    files[node.split('::')[0]].append(node)
completed=[]
for path,nodes in files.items():
    valid=True
    for node in nodes:
        reports=by_node[node]
        phases=defaultdict(list)
        for report in reports:
            phases[report['when']].append(report)
        if (len(phases['setup'])!=1 or len(phases['teardown'])!=1
                or any(len(values)!=1 for values in phases.values())
                or (phases['setup'][0]['outcome']=='passed' and len(phases['call'])!=1)):
            valid=False
            break
    if valid:
        reports=[report for node in nodes for report in by_node[node]]
        assert not any(report['outcome']=='failed' for report in reports),path
        skips=[report for report in reports if report['outcome']=='skipped']
        completed.append({'path':path,'node_count':len(nodes),
                          'result':'completed_with_skips' if skips else 'passed',
                          'phases':reports,'skips':skips})
inventory=json.loads((OUT/'source-inventory.json').read_text(encoding='utf-8'))
drift=[item['path'] for item in inventory if hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()!=item['sha256']]
assert not drift,drift
checkpoint=json.loads((OUT/'full-command.json').read_text(encoding='utf-8'))['source_commit']
data={'source_commit':checkpoint,'saved_at':datetime.now(timezone.utc).isoformat(),
      'scope':'only completed whole files from this one ongoing run; partial files excluded; not a full-suite or real acceptance verdict',
      'source_drift':drift,'files':completed}
destination.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'completed_files':len(completed),'nodes_in_completed_files':sum(item['node_count'] for item in completed),'skips':sum(len(item['skips']) for item in completed)}))
