"""Run this candidate's checks once, serially after the already running full suite.

No scheduler or automatic Git writes. Each command/result retains its own scope.
"""
import datetime, hashlib, json, os, shutil, subprocess, sys, time
from pathlib import Path

root = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
baseline = Path('E:/ai-test-component/docs/validation/p1-final-closure-20261014')
python = Path('E:/ai-test-component/.runtime/completion-clean313/Scripts/python.exe')
temporary = Path('C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014')

def save(name,data):
 target=out/name; temp=target.with_suffix(target.suffix+'.tmp')
 temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 os.replace(temp,target)

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()

def frozen():
 inventory=json.loads((out/'source-inventory.json').read_text(encoding='utf-8'))
 changed=[item['path'] for item in inventory if hashlib.sha256((root/item['path']).read_bytes()).hexdigest()!=item['sha256']]
 if changed: raise RuntimeError('candidate source changed before/during queued verification: '+repr(changed))
 return inventory

if (out/'queue-state.json').exists(): raise RuntimeError('queue already exists; refuse duplicate execution')
checkpoint=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
save('queue-state.json',{'state':'waiting_for_baseline','runner_pid':os.getpid(),'created_at':now(),'candidate_commit':checkpoint,'baseline':str(baseline)})
try:
 while True:
  state=json.loads((baseline/'full-state.json').read_text(encoding='utf-8'))
  if state['state']=='finished':
   if not (baseline/'full-result.json').exists() or not (baseline/'full.xml').exists():
    raise RuntimeError('baseline claims finished without its complete result and JUnit')
   save('baseline-result.json',json.loads((baseline/'full-result.json').read_text(encoding='utf-8')))
   break
  if state['state']!='running': raise RuntimeError('baseline has no complete verdict; candidate queue remains unverified')
  time.sleep(30)
 frozen()
 env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8','PYTHONPATH':os.pathsep.join((str(root/'src'),str(root))),'TEMP':str(temporary),'TMP':str(temporary)}
 files=[
  'tests/unit/test_action_resolver.py','tests/unit/test_saved_exports.py',
  'tests/unit/test_default_start_source_chain.py','tests/unit/test_default_step_execution.py',
  'tests/unit/test_execution_authorization_origin.py','tests/unit/test_default_execution_authorization.py',
  'tests/unit/test_execution_commit.py','tests/unit/test_current_execution_snapshot.py',
  'tests/unit/test_source_checks.py','tests/unit/test_start_source_verification.py',
  'tests/unit/test_a_a08_load_source_probe.py','tests/unit/test_source_probe_protocol.py',
  'tests/unit/test_saved_reports.py','tests/unit/test_completion_boundaries.py','tests/unit/test_reuse_consumption.py',
 ]
 node=shutil.which('node')
 npm=shutil.which('npm.cmd')
 if node is None or npm is None: raise RuntimeError('Node/npm unavailable for generated panel build')
 npm_cli=Path(npm).parent/'node_modules/npm/bin/npm-cli.js'
 if not npm_cli.is_file(): raise RuntimeError('npm CLI path cannot be verified')
 panel=root/'src/aitest/resources/panel'
 commands=[
  ('affected',[str(python),'-m','pytest',*files,'-ra','--basetemp='+str(temporary/'unproven-affected'),'--junitxml='+str(out/'affected.xml')]),
  ('generation',[str(python),'scripts/generate_schemas.py']),
  ('ruff',[str(python),'-m','ruff','check','src','tests','scripts']),
  ('mypy',[str(python),'-m','mypy']),
  ('panel-install',[node,str(npm_cli),'ci','--prefix',str(panel)]),
  ('panel-build',[node,str(npm_cli),'run','build','--prefix',str(panel)]),
  ('docs-facts',[str(python),'scripts/check_docs_facts.py','--full']),
  ('build',[str(python),'-m','build']),
  ('wheel-smoke',[str(python),'scripts/smoke_wheel.py']),
  ('full',[str(python),'-m','pytest','tests','-ra','--basetemp='+str(temporary/'unproven-full'),'--junitxml='+str(out/'full.xml')]),
 ]
 save('queued-commands.json',[{'name':name,'command':command,'candidate_commit':checkpoint} for name,command in commands])
 for name,command in commands:
  frozen(); started=now()
  with (out/(name+'.log')).open('wb') as log:
   child=subprocess.Popen(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
   save('queue-state.json',{'state':'running','phase':name,'runner_pid':os.getpid(),'child_pid':child.pid,'started_at':started,'candidate_commit':checkpoint})
   code=child.wait()
  save(name+'-result.json',{'exit_code':code,'started_at':started,'ended_at':now(),'candidate_commit':checkpoint,'command':command})
  frozen()
  if code:
   save('queue-state.json',{'state':'failed','phase':name,'exit_code':code,'candidate_commit':checkpoint,'ended_at':now()})
   raise SystemExit(code)
 save('queue-state.json',{'state':'finished','exit_code':0,'candidate_commit':checkpoint,'ended_at':now()})
except Exception as error:
 save('queue-state.json',{'state':'queue_blocked','exception_type':type(error).__name__,'safe_reason':str(error),'candidate_commit':checkpoint,'ended_at':now()})
 raise
