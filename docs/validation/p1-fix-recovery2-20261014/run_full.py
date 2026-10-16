"""One latest-source full suite, with affected paths first and exact progress."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
PYTHON=ROOT/'.runtime/completion-clean313/Scripts/python.exe'


def now(): return datetime.now(timezone.utc).isoformat()


def save(name,data):
    target=OUT/name; temporary=target.with_suffix(target.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temporary,target)


class Priority:
    def pytest_collection_modifyitems(self, items):
        ids=[item.nodeid for item in items]
        if len(ids)!=5370 or len(set(ids))!=5370:
            raise RuntimeError('latest full collection must contain exactly 5370 unique nodes')
        affected=json.loads((OUT/'affected-paths.json').read_text(encoding='utf-8'))
        def rank(item):
            if 'test_default_source_observation_is_atomic_and_readable_after_restart' in item.nodeid:
                return 0
            return 1 if item.nodeid.split('::')[0] in affected else 2
        items.sort(key=rank)
        save('collected-nodes.json',{'count':len(items),'unique':len(set(ids)),'nodeids':[item.nodeid for item in items],'scope':'one full collection, reordered only; no historical sum or duplicate execution'})

    def pytest_runtest_logreport(self, report):
        record={'nodeid':report.nodeid,'when':report.when,'outcome':report.outcome,'duration':report.duration,'updated_at':now()}
        if report.skipped:
            record['skip_reason']=str(report.longrepr[2]) if isinstance(report.longrepr,tuple) else str(report.longrepr)
        with (OUT/'case-results.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(record,ensure_ascii=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())

    def pytest_runtest_logstart(self,nodeid,location):
        save('progress.json',{'state':'running','worker_pid':os.getpid(),'current_test':nodeid,'updated_at':now()})


if '--worker' in sys.argv:
    import pytest
    raise SystemExit(pytest.main(['tests','-ra','-x','--basetemp=C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014/fix-recovery2-full','--junitxml='+str(OUT/'full.xml')],plugins=[Priority()]))

if (OUT/'full-state.json').exists():
    raise RuntimeError('full state exists; refuse duplicate execution')
checkpoint=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
inventory=json.loads((OUT/'source-inventory.json').read_text(encoding='utf-8'))
assert all(hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()==item['sha256'] for item in inventory)
command=[str(PYTHON),str(Path(__file__).resolve()),'--worker']
env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8','PYTHONPATH':os.pathsep.join((str(ROOT/'src'),str(ROOT))),'TEMP':'C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014','TMP':'C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014'}
started=now()
save('full-command.json',{'command':command,'source_commit':checkpoint,'started_at':started,'scope':'all 5370 unique nodes; new atomic cases and all affected paths first; stop on first failure; no concurrent heavy test'})
try:
    with (OUT/'full.log').open('wb') as log:
        child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        save('full-state.json',{'state':'running','source_commit':checkpoint,'runner_pid':os.getpid(),'child_pid':child.pid,'started_at':started})
        code=child.wait()
    drift=[item['path'] for item in inventory if hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()!=item['sha256']]
    record={'state':'finished','exit_code':code,'source_commit':checkpoint,'started_at':started,'ended_at':now(),'source_drift':drift,'scope':'only a complete passing JUnit plus zero drift proves full pass; failfast is not a complete verdict'}
    save('full-result.json',record);save('full-state.json',record)
    verifier=subprocess.run([str(PYTHON),str(OUT/'verify_full.py')],cwd=ROOT,env=env)
    if code==0 and verifier.returncode!=0:
        raise SystemExit(verifier.returncode)
    raise SystemExit(code)
except Exception as error:
    save('full-state.json',{'state':'runner_failed','exception_type':type(error).__name__,'source_commit':checkpoint,'ended_at':now()})
    raise
