"""Atomic validation snapshots; advisory progress cannot abort a test suite."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from uuid import uuid4


def write_snapshot(directory: Path, name: str, value: object, *, advisory: bool=False,
                   attempts: int=8, delay: float=0.05) -> bool:
    target=directory/name
    temporary=target.with_name('.'+target.name+'.'+str(os.getpid())+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
            stream.flush()
            if not advisory:
                os.fsync(stream.fileno())
        for attempt in range(attempts):
            try:
                os.replace(temporary,target)
                return True
            except PermissionError:
                if attempt+1==attempts:
                    raise
                time.sleep(delay)
    except Exception as error:
        if not advisory:
            raise
        # Only progress is optional; ledger/JUnit/final receipts stay mandatory.
        record={'target':name,'exception_type':type(error).__name__,
                'winerror':getattr(error,'winerror',None),'advisory':True,
                'updated_at':datetime.now(timezone.utc).isoformat()}
        try:
            with (directory/'progress-errors.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(record)+'\n')
        except OSError:
            pass
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return False
