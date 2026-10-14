"""One complete frozen-source suite; keep observer I/O out of fault injection."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from report_io import append_record, write_snapshot, _GETPID

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
PYTHON=Path('E:/ai-test-component/.runtime/completion-clean313/Scripts/python.exe')


def now(): return datetime.now(timezone.utc).isoformat()


class Ledger:
    def __init__(self, output): self.output=output
    def pytest_collection_modifyitems(self, items):
        ids=[item.nodeid for item in items]
        assert len(ids)==len(set(ids)), 'duplicate collection'
        write_snapshot(self.output,'collected-nodes.json',{'count':len(ids),'nodeids':ids})
    def pytest_runtest_logreport(self, report):
        record={'nodeid':report.nodeid,'when':report.when,'outcome':report.outcome,
                'duration':report.duration,'updated_at':now()}
        if report.skipped:
            record['skip_reason']=str(report.longrepr[2]) if isinstance(report.longrepr,tuple) else str(report.longrepr)
        append_record(self.output/'case-results.jsonl',record)
    def pytest_runtest_logstart(self,nodeid,location):
        write_snapshot(self.output,'progress.json',{'state':'running','worker_pid':_GETPID(),
                       'current_test':nodeid,'updated_at':now()},advisory=True)


class Priority(Ledger):
    def pytest_collection_modifyitems(self, items):
        expected=json.loads((OUT/'facts.json').read_text(encoding='utf-8'))['collected_nodes']
        assert len(items)==expected, 'frozen collection count changed'
        affected=json.loads((OUT/'affected-paths.json').read_text(encoding='utf-8'))
        def rank(item):
            path=item.nodeid.split('::')[0]
            if path in ('tests/unit/test_a_a08_snapshot_durability.py',
                        'tests/unit/test_tree_material_immutability.py',
                        'tests/recovery/test_atomic_and_lock.py'):
                return 0
            if path in ('tests/unit/test_a_migrations.py','tests/unit/test_a_a16_migration_rollback.py',
                        'tests/unit/test_a_a07_reclaim_eligibility.py','tests/unit/test_a_maintenance.py'):
                return 1
            if 'test_default_source_observation_is_atomic_and_readable_after_restart' in item.nodeid:
                return 2
            return 3 if path in affected else 4
        items.sort(key=rank)
        super().pytest_collection_modifyitems(items)


def main():
    if '--probe' in sys.argv:
        output=OUT/'observer-probe';output.mkdir(exist_ok=True)
        if (output/'case-results.jsonl').exists():
            raise RuntimeError('probe receipts exist; refuse duplicate append')
        import pytest
        paths=['tests/unit/test_a_a08_snapshot_durability.py',
               'tests/unit/test_tree_material_immutability.py',
               'tests/recovery/test_atomic_and_lock.py']
        arguments=[*paths,'-q','--basetemp=C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014/reporter-probe',
                   '--junitxml='+str(output/'probe.xml')]
        code=pytest.main(arguments,plugins=[Ledger(output)])
        ids=set(json.loads((output/'collected-nodes.json').read_text(encoding='utf-8'))['nodeids'])
        rows=[json.loads(line) for line in (output/'case-results.jsonl').read_text(encoding='utf-8').splitlines()]
        cases=ET.parse(output/'probe.xml').getroot().findall('.//testcase')
        phases={(row['nodeid'],row['when']) for row in rows}
        complete=len(phases)==len(rows) and all((node,phase) in phases for node in ids for phase in ('setup','call','teardown'))
        passed=code==0 and len(cases)==len(ids) and complete and not any(row['outcome']=='failed' for row in rows)
        write_snapshot(OUT,'observer-probe-result.json',{'result':'passed' if passed else 'not_verified',
             'command':[str(PYTHON),str(Path(__file__).resolve()),'--probe'],'pytest_arguments':arguments,
             'exit_code':code,'nodes':len(ids),'junit_cases':len(cases),'ledger_records':len(rows),
             'scope':'three complete real fault-injection files with the exact new reporter; not full or real AC'})
        return 0 if passed else 1
    if '--worker' in sys.argv:
        import pytest
        return pytest.main(['tests','-ra','-x',
             '--basetemp=C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014/migration-closure-full',
             '--junitxml='+str(OUT/'full.xml')],plugins=[Priority(OUT)])
    if (OUT/'full-state.json').exists():
        raise RuntimeError('full state exists; refuse duplicate execution')
    checkpoint=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    inventory=json.loads((OUT/'source-inventory.json').read_text(encoding='utf-8'))
    assert all(hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()==item['sha256'] for item in inventory)
    expected=json.loads((OUT/'facts.json').read_text(encoding='utf-8'))['collected_nodes']
    command=[str(PYTHON),str(Path(__file__).resolve()),'--worker']
    env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8','PYTHONPATH':os.pathsep.join((str(ROOT/'src'),str(ROOT))),
         'TEMP':'C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014','TMP':'C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014'}
    started=now()
    write_snapshot(OUT,'full-command.json',{'command':command,'source_commit':checkpoint,'started_at':started,
         'expected_unique_nodes':expected,'scope':'one whole collection; fault-injection and affected files first; no concurrent heavy test'})
    try:
        with (OUT/'full.log').open('wb') as log:
            child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            write_snapshot(OUT,'full-state.json',{'state':'running','source_commit':checkpoint,'runner_pid':os.getpid(),
                 'child_pid':child.pid,'started_at':started})
            code=child.wait()
        drift=[item['path'] for item in inventory if hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()!=item['sha256']]
        record={'state':'finished','exit_code':code,'source_commit':checkpoint,'started_at':started,'ended_at':now(),
             'source_drift':drift,'scope':'complete JUnit, every unique phase, exit0 and zero drift are mandatory'}
        write_snapshot(OUT,'full-result.json',record);write_snapshot(OUT,'full-state.json',record)
        verifier=subprocess.run([str(PYTHON),str(OUT/'verify_full.py')],cwd=ROOT,env=env)
        return code or verifier.returncode
    except Exception as error:
        write_snapshot(OUT,'full-state.json',{'state':'runner_failed','exception_type':type(error).__name__,
                        'source_commit':checkpoint,'ended_at':now()})
        raise


if __name__=='__main__': raise SystemExit(main())
