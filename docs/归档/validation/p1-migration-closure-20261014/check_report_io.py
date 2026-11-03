"""Real Windows sharing-lock check plus transient/persistent error injection."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from report_io import write_snapshot

OUT=Path(__file__).resolve().parent
checks={}
with tempfile.TemporaryDirectory(prefix='aitest-progress-writer-',dir='C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014') as name:
    root=Path(name)
    checks['normal_critical_snapshot']=write_snapshot(root,'state.json',{'state':'initial'})
    real_replace=__import__('os').replace
    calls=[]
    def flaky(source,target):
        calls.append(1)
        if len(calls)<=2:
            raise PermissionError('injected sharing conflict')
        return real_replace(source,target)
    with patch('report_io._REPLACE',flaky):
        checks['transient_permission_retried']=write_snapshot(root,'state.json',{'state':'updated'},delay=0) and len(calls)==3
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.CloseHandle.restype=wintypes.BOOL
    target=root/'progress.json'
    target.write_text('{"state":"previous"}',encoding='utf-8')
    handle=kernel.CreateFileW(str(target),0x80000000,1,None,3,0x80,None)
    assert handle not in (None,wintypes.HANDLE(-1).value)
    try:
        # Read sharing without FILE_SHARE_DELETE reproduces the actual WinError 5.
        try:
            write_snapshot(root,'progress.json',{'state':'critical'},attempts=2,delay=0)
        except PermissionError:
            checks['critical_lock_failure_not_hidden']=True
        else:
            checks['critical_lock_failure_not_hidden']=False
        checks['advisory_lock_cannot_abort_suite']=not write_snapshot(root,'progress.json',{'state':'new'},advisory=True,attempts=2,delay=0)
        checks['locked_snapshot_unchanged']=json.loads(target.read_text(encoding='utf-8'))['state']=='previous'
        notices=[json.loads(line) for line in (root/'progress-errors.jsonl').read_text(encoding='utf-8').splitlines()]
        checks['advisory_error_recorded']=len(notices)==1 and notices[0]['winerror']==5
    finally:
        kernel.CloseHandle(handle)
    checks['replacement_after_reader_closed']=write_snapshot(root,'progress.json',{'state':'resumed'},advisory=True)
    checks['no_owned_temp_leftovers']=not list(root.glob('.*.tmp'))
assert all(checks.values()),checks
(OUT/'progress-writer-result.json').write_text(json.dumps({'result':'passed','checks':checks,'scope':'validation writer only; actual Windows read-sharing lock and error injection; not product acceptance'},indent=2)+'\n',encoding='utf-8')
print(json.dumps(checks))
