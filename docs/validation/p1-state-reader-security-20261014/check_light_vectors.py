"""Exercise new small-file vectors directly; no core/full pytest run."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import runpy
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.maintenance import FileMaintenanceService
from aitest.infrastructure.file_store.migrations import FileMigrationManager
from aitest.infrastructure.file_store.workspace import Workspace
import aitest.infrastructure.file_store.migrations as migration_module
assert Path(migration_module.__file__).resolve().is_relative_to(ROOT/'src')

migrations=runpy.run_path(str(ROOT/'tests/unit/test_a_migrations.py'))
reclaim=runpy.run_path(str(ROOT/'tests/unit/test_a_a07_reclaim_eligibility.py'))
maintenance=runpy.run_path(str(ROOT/'tests/unit/test_a_maintenance.py'))
checks=[]


def verify(module,name,kind,*arguments):
    with tempfile.TemporaryDirectory(prefix='aitest-state-reader-',dir='C:/Users/86178/AppData/Local/Temp/aitest-p1-completion-20261014') as directory:
        root=Path(directory)
        Workspace(root)
        FileEventJournal(root,instance_id='component-fixture')
        subject=(FileMigrationManager(root) if kind=='manager' else
                 FileMaintenanceService(root) if kind=='service' else root)
        module[name](subject,*arguments)
        checks.append({'test':name,'arguments':repr(arguments),'result':'passed'})


for key in ('state',r'st\u0061te'):
    verify(migrations,'test_duplicate_checkpoint_cannot_hide_prepared_state','manager',key)
for schema in (None,'aitest.migration-registry/999'):
    verify(migrations,'test_unknown_registry_schema_cannot_release_maintenance','manager',schema)
verify(migrations,'test_duplicate_registry_cannot_forge_applied_step','manager')
for field,value in (('schema',None),('schema','unknown'),('plan_id','other')):
    verify(migrations,'test_unknown_plan_schema_or_identity_is_rejected_before_apply','manager',field,value)
verify(migrations,'test_duplicate_commit_cannot_hide_rollback_watermark','manager')
for registered_plan in (None,'different'):
    verify(migrations,'test_prepared_checkpoint_needs_registration_from_exact_plan','manager',registered_plan)
verify(migrations,'test_successful_exact_plan_registration_releases_prepared_checkpoint','manager')
for damage in ('duplicate_checkpoint','unknown_registry','other_plan'):
    verify(migrations,'test_uncertain_migration_blocks_adapter_write_without_blocking_doctor','manager',damage)
for suffix in ('json','jsonl'):
    verify(reclaim,'test_duplicate_permanent_reference_blocks_actual_reclaim','root',suffix)
for material in (
    b'{"state":"capturing","state":"sealed"}',
    b'{"state":"sealed","detail":NaN}',
    b'{"state":"sealed","detail":1e9999}',
    b'{"state":"sealed","detail":"\\ud800"}',
    '{"state":"sealed"}'.encode('utf-16'),
):
    verify(reclaim,'test_ambiguous_capture_state_cannot_release_activity','root',material)
verify(maintenance,'test_duplicate_backup_audit_does_not_publish_fabricated_verified_fact','service')
assert len(checks)==23
changed=['src/aitest/infrastructure/file_store/migrations.py','src/aitest/infrastructure/file_store/maintenance.py',
         'tests/unit/test_a_migrations.py','tests/unit/test_a_a07_reclaim_eligibility.py','tests/unit/test_a_maintenance.py']
record={'result':'passed','completed_at':datetime.now(timezone.utc).isoformat(),'loaded_source_root':str(ROOT),
        'scope':'23 new small-file function/vector checks directly with real filesystem and three LocalAPI seams; no full pytest/JUnit, existing full-file regression, core restart or real acceptance inferred',
        'checks':checks,'source':[{'path':path,'sha256':hashlib.sha256((ROOT/path).read_bytes()).hexdigest()} for path in changed]}
(OUT/'light-vectors-result.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'result':'passed','vectors':len(checks),'source_root':str(ROOT)}))
