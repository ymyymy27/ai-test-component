"""One serial candidate verification after the actual baseline run exits.

No scheduler, automatic Git mutation, duplicate heavy suite or stale-state wait.
"""
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
BASE=Path('E:/ai-test-component/docs/validation/p1-fix-recovery2-20261014')
PYTHON=Path('E:/ai-test-component/.runtime/completion-clean313/Scripts/python.exe')


def now():
    return datetime.now(timezone.utc).isoformat()


def save(name,data):
    path=OUT/name
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temporary,path)


source=(ROOT/'docs/validation/p1-unproven-audit-20261014/run_after_baseline.py').read_text(encoding='utf-8')
function=next(node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name=='process_alive')
namespace={'os':os}
exec(compile(ast.Module(body=[function],type_ignores=[]),'<checked-process-alive>','exec'),namespace)
process_alive=namespace['process_alive']

inventory=json.loads((OUT/'source-inventory.json').read_text(encoding='utf-8'))
artifacts=json.loads((OUT/'generated-inventory.json').read_text(encoding='utf-8'))
checkpoint=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()


def frozen():
    drift=[item['path'] for item in inventory+artifacts if hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()!=item['sha256']]
    if drift:
        raise RuntimeError('candidate source drift: '+repr(drift))


if (OUT/'queue-state.json').exists():
    raise RuntimeError('candidate queue already registered; refuse duplicate execution')
save('queue-state.json',{'state':'waiting_for_baseline','runner_pid':os.getpid(),'source_commit':checkpoint,'created_at':now(),'baseline':str(BASE)})
try:
    deadline=time.monotonic()+20*3600
    verifier_deadline=None
    missing_child_since=None
    while True:
        state=json.loads((BASE/'full-state.json').read_text(encoding='utf-8'))
        if state['state']=='finished':
            result=json.loads((BASE/'full-result.json').read_text(encoding='utf-8'))
            if result['exit_code']!=0 or result['source_drift']:
                raise RuntimeError('baseline failed or drifted; stop for actual failure review')
            # The pytest runner writes its result before the ledger verifier finishes.
            verification=BASE/'full-verification.json'
            if verification.exists():
                verdict=json.loads(verification.read_text(encoding='utf-8'))
                if verdict['result']!='passed':
                    raise RuntimeError('baseline node/JUnit/source verification did not pass')
                save('baseline-result.json',result)
                save('baseline-verification.json',verdict)
                break
            if verifier_deadline is None:
                verifier_deadline=time.monotonic()+120
            if time.monotonic()>verifier_deadline:
                raise RuntimeError('finished baseline has no verification receipt after 120 seconds')
        elif state['state']!='running':
            raise RuntimeError('baseline ended without a complete verdict: '+state['state'])
        elif not process_alive(state.get('runner_pid')):
            raise RuntimeError('baseline registered process absent; stale running is not an active run')
        elif not process_alive(state.get('child_pid')):
            # A runner can be hashing its source inventory after pytest exits.
            if missing_child_since is None:
                missing_child_since=time.monotonic()
            if time.monotonic()-missing_child_since>120:
                raise RuntimeError('baseline child exited without a final runner receipt after 120 seconds')
        else:
            missing_child_since=None
        if time.monotonic()>deadline:
            raise RuntimeError('baseline verification deadline exceeded')
        time.sleep(30)
    frozen()
    panel=ROOT/'src/aitest/resources/panel'
    node=Path('F:/Node/node.exe')
    npm_cli=Path('F:/Node/node_modules/npm/bin/npm-cli.js')
    assert node.is_file() and npm_cli.is_file()
    env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8',
         'PYTHONPATH':os.pathsep.join((str(ROOT/'src'),str(ROOT))),
         'TEMP':'C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014',
         'TMP':'C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014'}
    commands=[
        ('generation',[str(PYTHON),'scripts/generate_schemas.py']),
        ('ruff',[str(PYTHON),'-m','ruff','check','src','tests','scripts']),
        ('mypy',[str(PYTHON),'-m','mypy']),
        ('lock',['uv','lock','--check']),
        ('versions',[str(PYTHON),'scripts/check_versions.py']),
        ('panel-install',[str(node),str(npm_cli),'ci','--prefix',str(panel)]),
        ('panel-build',[str(node),str(npm_cli),'run','build','--prefix',str(panel)]),
        ('docs-facts',[str(PYTHON),'scripts/check_docs_facts.py','--full']),
        ('build',[str(PYTHON),'-m','build']),
        ('wheel-smoke',[str(PYTHON),'scripts/smoke_wheel.py']),
        ('artifacts',[str(PYTHON),'scripts/verify_artifacts.py']),
        ('full',[str(PYTHON),str(OUT/'run_full.py')]),
    ]
    save('queued-commands.json',[{'phase':phase,'command':command} for phase,command in commands])
    for phase,command in commands:
        frozen()
        started=now()
        with (OUT/(phase+'-queue.log')).open('wb') as stream:
            child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
            save('queue-state.json',{'state':'running','phase':phase,'runner_pid':os.getpid(),'child_pid':child.pid,'source_commit':checkpoint,'started_at':started})
            code=child.wait()
        save(phase+'-queue-result.json',{'phase':phase,'command':command,'exit_code':code,'started_at':started,'ended_at':now(),'source_commit':checkpoint})
        frozen()
        if code:
            save('queue-state.json',{'state':'failed','phase':phase,'exit_code':code,'source_commit':checkpoint,'ended_at':now()})
            raise SystemExit(code)
        if phase=='artifacts':
            wheel=ROOT/'dist/ai_test_component-0.4.0-py3-none-any.whl'
            with zipfile.ZipFile(wheel) as package:
                modules=[item for item in inventory if item['path'].startswith('src/aitest/') and item['path'].endswith('.py')]
                assert all(hashlib.sha256(package.read(item['path'][4:])).hexdigest()==item['sha256'] for item in modules)
            save('artifact-source-integrity.json',{'result':'passed','source_commit':checkpoint,'wheel_modules_checked':len(modules),'scope':'exact candidate wheel module bytes and shared panel/reference VSIX only; no real host acceptance'})
    save('queue-state.json',{'state':'finished','exit_code':0,'source_commit':checkpoint,'ended_at':now()})
except Exception as error:
    save('queue-state.json',{'state':'blocked','exception_type':type(error).__name__,'reason':str(error),'source_commit':checkpoint,'ended_at':now()})
    raise
