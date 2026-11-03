"""Native reporter calls survive product injection; actual reporter failure stays fatal."""
import io, json, os, tempfile
from pathlib import Path
from unittest.mock import patch
import report_io
checks={}
with tempfile.TemporaryDirectory(prefix='aitest-reporter-captured-') as name:
    root=Path(name)
    def injected(*args,**kwargs): raise OSError('injected product fault')
    with patch('os.fsync',injected):
        report_io.append_record(root/'ledger.jsonl',{'when':'call','outcome':'passed'})
    checks['shared_fsync_injection_does_not_touch_reporter']=json.loads((root/'ledger.jsonl').read_text())['outcome']=='passed'
    with patch.object(Path,'open',lambda *a,**k: io.BytesIO(b'fake product bytes')):
        report_io.append_record(root/'ledger.jsonl',{'when':'teardown','outcome':'passed'})
    checks['path_open_replacement_does_not_fake_ledger']=len((root/'ledger.jsonl').read_text().splitlines())==2
    with patch('io.open',injected):
        report_io.append_record(root/'ledger.jsonl',{'when':'setup','outcome':'passed'})
    checks['shared_io_open_injection_does_not_touch_reporter']=len((root/'ledger.jsonl').read_text().splitlines())==3
    with patch('os.replace',injected):
        checks['shared_replace_injection_does_not_touch_reporter']=report_io.write_snapshot(root,'state.json',{'state':'kept'})
    with patch('report_io._FSYNC',injected):
        try: report_io.append_record(root/'failed-ledger.jsonl',{'when':'call'})
        except OSError: checks['actual_ledger_sync_failure_is_fatal']=True
        else: checks['actual_ledger_sync_failure_is_fatal']=False
        try: report_io.write_snapshot(root,'critical.json',{'state':'critical'})
        except OSError: checks['actual_critical_snapshot_failure_is_fatal']=True
        else: checks['actual_critical_snapshot_failure_is_fatal']=False
assert all(checks.values()),checks
out=Path(__file__).resolve().parent
(out/'captured-io-result.json').write_text(json.dumps({'result':'passed','checks':checks,'scope':'validation reporter only; mandatory I/O failure is not hidden'},indent=2),encoding='utf-8')
print(json.dumps(checks))
