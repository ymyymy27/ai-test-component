"""Reporter I/O captured before product tests patch shared stdlib attributes."""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import time
from uuid import uuid4

_OPEN = io.open
_FSYNC = os.fsync
_REPLACE = os.replace
_UNLINK = os.unlink
_GETPID = os.getpid
_SLEEP = time.sleep
_DUMPS = json.dumps


def append_record(path, value):
    with _OPEN(path, 'a', encoding='utf-8') as stream:
        stream.write(_DUMPS(value, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()
        _FSYNC(stream.fileno())


def write_snapshot(directory, name, value, *, advisory=False, attempts=8, delay=0.05):
    target=directory/name
    temporary=target.with_name('.'+target.name+'.'+str(_GETPID())+'.'+uuid4().hex+'.tmp')
    try:
        with _OPEN(temporary,'x',encoding='utf-8') as stream:
            stream.write(_DUMPS(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
            stream.flush()
            if not advisory:
                _FSYNC(stream.fileno())
        for attempt in range(attempts):
            try:
                _REPLACE(temporary,target)
                return True
            except PermissionError:
                if attempt+1==attempts:
                    raise
                _SLEEP(delay)
    except Exception as error:
        if not advisory:
            raise
        record={'target':name,'exception_type':type(error).__name__,
                'winerror':getattr(error,'winerror',None),'advisory':True,
                'updated_at':datetime.now(timezone.utc).isoformat()}
        try:
            append_record(directory/'progress-errors.jsonl',record)
        except OSError:
            pass
        return False
    finally:
        try:
            _UNLINK(temporary)
        except OSError:
            pass
    return False
