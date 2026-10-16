"""Check the exact legacy waiter's pure liveness guard without starting a queue."""
import ast
import json
import os
from pathlib import Path

root=Path(__file__).resolve().parents[3]
source=root/'docs/validation/p1-unproven-audit-20261014/run_after_baseline.py'
tree=ast.parse(source.read_text(encoding='utf-8'))
function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='process_alive')
module=ast.Module(body=[function],type_ignores=[])
scope={'os':os}
exec(compile(module,str(source),'exec'),scope)
alive=scope['process_alive']
checks={'current_process_alive':alive(os.getpid()),'zero_rejected':not alive(0),'bool_rejected':not alive(True),'text_rejected':not alive('26248'),'absent_pid_rejected':not alive(1073741824)}
assert all(checks.values()),checks
Path(__file__).with_name('waiter-health-result.json').write_text(json.dumps({'result':'passed','checks':checks,'scope':'Win32 process-handle liveness checks only; no background queue or real acceptance'},indent=2)+'\n',encoding='utf-8')
print(json.dumps(checks))
