"""Closure backup and verification; no business material deletion."""
from pathlib import Path
import hashlib, json, shutil
class FileBackupStore:
    def __init__(self, root: Path): self.root=root.resolve()
    def create(self, destination: Path) -> Path:
        destination=destination.resolve(); destination.mkdir(parents=True,exist_ok=True)
        for name in ("workspace.json","records.json","indexes.json","current.json","commit.json","events.json","transactions.json"):
            source=self.root/name
            if source.exists(): shutil.copy2(source,destination/name)
        for name in ("objects","spool","checkpoints","transactions","events","manifests"):
            source=self.root/name
            if source.exists(): shutil.copytree(source,destination/name,dirs_exist_ok=True)
        manifest={str(p.relative_to(destination)):hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.rglob("*") if p.is_file()}
        (destination/"backup.json").write_text(json.dumps({"schema":"aitest.backup/1.0","files":manifest},indent=2),encoding="utf-8"); return destination
    def verify(self, backup: Path) -> dict[str, object]:
        raw=json.loads((backup/"backup.json").read_text(encoding="utf-8")); errors=[]
        for name,digest in raw["files"].items():
            path=backup/name
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest: errors.append(name)
        return {"ok":not errors,"errors":errors}
