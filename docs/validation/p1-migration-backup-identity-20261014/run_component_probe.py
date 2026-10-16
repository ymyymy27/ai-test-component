"""Disposable real apply/resume probes; never touch a product workspace."""
from pathlib import Path
import hashlib, json, sys, tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.infrastructure.file_store.migrations import FileMigrationManager, MigrationError
from aitest.infrastructure.file_store.backup import FileBackupStore
out=Path(__file__).resolve().parent
results=[]
steps=('0001-workspace-schema-version','0002-records-intents-container')
for kind in ('unowned_empty_backup','changed_frozen_backup','missing_frozen_backup'):
    with tempfile.TemporaryDirectory(prefix='aitest-migration-identity-after-') as name:
        root=Path(name); Workspace(root)
        (root/'records.json').write_text('{"records":{},"commit":0}',encoding='utf-8')
        manager=FileMigrationManager(root); plan=manager.plan(steps).plan_id
        destination=root/'migrations/backups'/plan
        if kind=='unowned_empty_backup':
            destination.mkdir(parents=True)
            (destination/'backup.json').write_text('{"schema":"aitest.backup/1.0","files":{},"excluded_active":[]}',encoding='utf-8')
        else:
            manager.apply(plan)
            registry=root/'migrations/registry.json'
            data=json.loads(registry.read_text(encoding='utf-8')); data['applied'].pop(steps[1])
            registry.write_text(json.dumps(data),encoding='utf-8')
            if kind=='changed_frozen_backup':
                file=destination/'workspace.json'
                data=json.loads(file.read_text(encoding='utf-8')); data['workspace_id']='different-workspace'
                file.write_text(json.dumps(data),encoding='utf-8')
                manifest=destination/'backup.json'; data=json.loads(manifest.read_text(encoding='utf-8'))
                data['files']['workspace.json']=hashlib.sha256(file.read_bytes()).hexdigest()
                manifest.write_text(json.dumps(data),encoding='utf-8')
            else:
                preserved=root/'original-backup-preserved'
                assert destination.resolve().is_relative_to(root.resolve())
                assert preserved.resolve().is_relative_to(root.resolve())
                destination.rename(preserved)
        before={p:p.read_bytes() for p in (manager._registry_path,manager._journal_path) if p.exists()}
        if kind!='unowned_empty_backup':
            before[manager._cycles_dir/f'{plan}.json']=(manager._cycles_dir/f'{plan}.json').read_bytes()
        try:
            report=manager.apply(plan)
            result={'state':report.state,'executed':list(report.executed),'reused_original_path':report.backup_path==destination,'new_backup_verified':FileBackupStore(root).verify(report.backup_path)['ok'],'original_unowned_manifest_retained':(destination/'backup.json').read_text(encoding='utf-8')=='{"schema":"aitest.backup/1.0","files":{},"excluded_active":[]}'}
            assert kind=='unowned_empty_backup' and not result['reused_original_path']
            assert result['new_backup_verified'] and result['original_unowned_manifest_retained']
            assert (report.backup_path/'records.json').read_bytes()==b'{"records":{},"commit":0}'
        except MigrationError as error:
            result={'error':type(error).__name__,'message':str(error),'registry_journal_cycle_unchanged':all(p.read_bytes()==value for p,value in before.items())}
            assert kind!='unowned_empty_backup' and result['registry_journal_cycle_unchanged']
            if kind=='missing_frozen_backup':
                result['backup_recreated']=destination.exists()
                assert not result['backup_recreated'] and (preserved/'backup.json').is_file()
        results.append({'kind':kind,'result':result})
(out/'after.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
