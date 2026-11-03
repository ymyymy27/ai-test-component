"""Serial small selections; retain the exact child exit codes and versions."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parents[3]
out=Path(__file__).resolve().parent
env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8','PYTHONPATH':os.pathsep.join((str(root/'src'),str(root)))}
results=[]
for version in ('311','312'):
    python=Path('E:/ai-test-component/.runtime')/('completion-clean'+version)/'Scripts/python.exe'
    command=[str(python),'-m','pytest','tests/unit/test_action_resolver.py','tests/unit/test_saved_exports.py','tests/unit/test_backup_security_integrity.py','-k','resolv or bundle_guard or source_material or backup_security','-q','-ra','--basetemp=C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014/runtime-backup-v2-'+version,'--junitxml='+str(out/('runtime-backup-v2-'+version+'.xml'))]
    started=datetime.now(timezone.utc).isoformat()
    with (out/('runtime-backup-v2-'+version+'.log')).open('wb') as log:
        process=subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
    record={'python':version,'command':command,'started_at':started,'ended_at':datetime.now(timezone.utc).isoformat(),'exit_code':process.returncode,'scope':'only the 96 selected source/bundle/backup checks; not full regression'}
    results.append(record)
    (out/'runtime-backup-v2-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'python':version,'exit_code':process.returncode}),flush=True)
    if process.returncode:
        raise SystemExit(process.returncode)
