"""Serial checks before the independent full suite; keep exact receipts."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
PYTHON = ROOT / '.runtime/completion-clean313/Scripts/python.exe'


def now():
    return datetime.now(timezone.utc).isoformat()


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def main():
    checkpoint = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    inventory = json.loads((OUT/'source-inventory.json').read_text(encoding='utf-8'))
    paths = ['pyproject.toml','uv.lock','src/aitest/resources/panel/package-lock.json',
             'integrations/trae/package-lock.json','integrations/trae/package.json']
    paths += [p.relative_to(ROOT).as_posix() for p in (ROOT/'src/aitest/contracts/schemas').glob('*.json')]
    generated = [{'path':p,'sha256':hashlib.sha256((ROOT/p).read_bytes()).hexdigest()} for p in paths]
    save('generated-before.json',generated)
    env = {**os.environ, 'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8',
           'PYTHONPATH':os.pathsep.join((str(ROOT/'src'),str(ROOT))),
           'TEMP':'C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014',
           'TMP':'C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014'}
    commands = [
        ('versions',[str(PYTHON),'scripts/check_versions.py']),
        ('lock',['uv','lock','--check']),
        ('generation',[str(PYTHON),'scripts/generate_schemas.py']),
        ('ruff',[str(PYTHON),'-m','ruff','check','src','tests','scripts']),
        ('mypy',[str(PYTHON),'-m','mypy']),
        ('docs-facts',[str(PYTHON),'scripts/check_docs_facts.py','--full']),
        ('build',[str(PYTHON),'-m','build']),
        ('wheel-smoke',[str(PYTHON),'scripts/smoke_wheel.py']),
        ('artifacts',[str(PYTHON),'scripts/verify_artifacts.py']),
    ]
    for name, command in commands:
        receipt=OUT/(name+'-result.json')
        if receipt.exists() and json.loads(receipt.read_text(encoding='utf-8'))['exit_code']==0:
            print(name,'existing successful receipt retained',flush=True)
            continue
        started = now()
        save('preflight-state.json',{'state':'running','phase':name,'pid':os.getpid(),'source_commit':checkpoint})
        with (OUT/(name+'.log')).open('wb') as stream:
            result = subprocess.run(command,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
        save(name+'-result.json',{'command':command,'source_commit':checkpoint,'started_at':started,'ended_at':now(),'exit_code':result.returncode})
        print(name,result.returncode,flush=True)
        if result.returncode:
            save('preflight-state.json',{'state':'failed','phase':name,'exit_code':result.returncode})
            return result.returncode
    drift = [item['path'] for item in inventory+generated if hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()!=item['sha256']]
    assert not drift,drift
    wheel=ROOT/'dist/ai_test_component-0.4.0-py3-none-any.whl'
    vsix=ROOT/'integrations/trae/dist/aitest-trae.vsix'
    checked=[]
    with zipfile.ZipFile(wheel) as package:
        for item in inventory:
            if item['path'].startswith('src/aitest/') and item['path'].endswith('.py'):
                name=item['path'][4:]
                assert hashlib.sha256(package.read(name)).hexdigest()==item['sha256'],name
                checked.append(name)
    assert hashlib.sha256(vsix.read_bytes()).hexdigest()=='da853ac4b5d397c2dbc8399d26c558c3168f400c160e78af5ecb34f21176051e'
    save('artifact-source-integrity.json',{'result':'passed','source_commit':checkpoint,'source_drift':drift,'wheel_modules_checked':len(checked),'modules':checked,'vsix_scope':'unchanged reference bytes and shared panel content only; no real Trae acceptance'})
    save('preflight-state.json',{'state':'finished','exit_code':0,'source_commit':checkpoint,'ended_at':now(),'source_drift':drift})
    return 0


if __name__=='__main__':
    raise SystemExit(main())
