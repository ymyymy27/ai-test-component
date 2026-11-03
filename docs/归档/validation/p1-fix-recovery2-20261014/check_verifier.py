"""Synthetic checks of the evidence verifier, never product/full-suite results."""
import hashlib
import json
from pathlib import Path
import runpy
import tempfile

OUT=Path(__file__).resolve().parent
check=runpy.run_path(str(OUT/'verify_full.py'))['main']
checks={}
with tempfile.TemporaryDirectory(prefix='aitest-ledger-verifier-') as directory:
    fixture=Path(directory)
    check.__globals__.update(ROOT=fixture,OUT=fixture)
    def save(name,value):
        (fixture/name).write_text(json.dumps(value),encoding='utf-8')
    (fixture/'source.py').write_bytes(b'fixture bytes')
    save('source-inventory.json',[{'path':'source.py','sha256':hashlib.sha256(b'fixture bytes').hexdigest()}])
    save('full-result.json',{'exit_code':0,'source_commit':'synthetic-not-product'})
    nodes=[f'fixture.py::test_fixture[{i}]' for i in range(5370)]
    save('collected-nodes.json',{'nodeids':nodes})
    rows=[{'nodeid':node,'when':phase,'outcome':'passed'} for node in nodes for phase in ('setup','call','teardown')]
    def ledger(items):
        (fixture/'case-results.jsonl').write_text(''.join(json.dumps(item)+'\n' for item in items),encoding='utf-8')
    ledger(rows)
    (fixture/'full.xml').write_text('<testsuites><testsuite>'+''.join(f'<testcase name="fixture{i}" />' for i in range(5370))+'</testsuite></testsuites>',encoding='utf-8')
    checks['complete_fixture_accepted']=check()==0
    ledger(rows[:-1])
    checks['missing_teardown_rejected']=check()==1
    ledger(rows+[rows[-1]])
    checks['duplicate_report_rejected']=check()==1
    ledger(rows)
    (fixture/'source.py').write_bytes(b'drifted bytes')
    checks['source_drift_rejected']=check()==1
    (fixture/'source.py').write_bytes(b'fixture bytes')
    save('full-result.json',{'exit_code':1,'source_commit':'synthetic-not-product'})
    checks['nonzero_exit_rejected']=check()==1
    assert all(checks.values()),checks
(OUT/'verifier-check-result.json').write_text(json.dumps({'result':'passed','checks':checks,'scope':'synthetic evidence-verifier fixtures only; not execution of product tests'},indent=2)+'\n',encoding='utf-8')
