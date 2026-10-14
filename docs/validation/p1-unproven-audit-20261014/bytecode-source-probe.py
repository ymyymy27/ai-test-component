"""Real Python component counterexample: a stale cache executes different code.

Creates harmless bytecode that only prints a canary, never alters user files.
The archive/source/full-suite workspaces are untouched; this is not Trae AC.
"""
import hashlib, importlib.util, json, marshal, struct, sys, tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(root),str(root/'src')]
from aitest.infrastructure.adapters.execution.python_checks import PythonLoadSourceProbe, evaluate_source_binding
with tempfile.TemporaryDirectory(prefix='aitest-source-bytecode-probe-') as temporary:
 project=Path(temporary)
 source=project/'case_module.py'
 source.write_text("value = 'declared source'\n",encoding='utf-8')
 cache=Path(importlib.util.cache_from_source(str(source)))
 cache.parent.mkdir()
 injected=compile("print('ATTEST_BYTECODE_CANARY')\nvalue='different code'\n",str(source),'exec')
 metadata=source.stat()
 cache.write_bytes(importlib.util.MAGIC_NUMBER+struct.pack('<III',0,int(metadata.st_mtime),metadata.st_size)+marshal.dumps(injected))
 fact=PythonLoadSourceProbe().probe(materialized_root=project,modules=('case_module',))
 expected=hashlib.sha256(source.read_bytes()).hexdigest()
 verdict=evaluate_source_binding(fact,expected_modules={'case_module':expected},project_root=project)
 canary=b'ATTEST_BYTECODE_CANARY' in fact.raw_output
 print(json.dumps({'scope':'actual interpreter component; not real host acceptance','cached_code_executed':canary,
  'source_bytes_sha256':expected,'reported_module_sha256':fact.modules[0].sha256,
  'binding_state':verdict.state,'blocked':verdict.blocked},indent=2))
 if canary and not verdict.blocked: raise SystemExit(1)
