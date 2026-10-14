"""Check the common saved-artifact guard with actual pre-fix fixture bytes.

Only memory objects and synthetic saved-record metadata are used. No extraction,
business writes, second pytest suite, or real-environment acceptance occurs.
"""
import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root), str(root / "src")]
if len(sys.argv) == 2:
    spec = importlib.util.spec_from_file_location("zip_legacy_preview", sys.argv[1])
    assert spec is not None and spec.loader is not None
    preview = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = preview
    spec.loader.exec_module(preview)
    ExportRefused, SavedExportService = preview.ExportRefused, preview.SavedExportService
else:
    from aitest.application.review.saved_exports import ExportRefused, SavedExportService

class MemoryObjects:
    def __init__(self, content, digest):
        self.content, self.digest = content, digest

    def read_bytes(self, reference):
        assert reference.digest == self.digest and reference.size == len(self.content)
        return self.content

rows = []
for vector in json.loads(Path(__file__).with_name("zip-legacy-vectors.json").read_text(encoding="utf-8"))["vectors"]:
    content = base64.b64decode(vector["artifact_base64"], validate=True)
    assert "sha256:" + hashlib.sha256(content).hexdigest() == vector["sha256"]
    assert len(content) == vector["size"]
    service = object.__new__(SavedExportService)
    service._objects = MemoryObjects(content, vector["sha256"])
    payload = {"project_id": "project-1", "kind": "evidence_bundle", "artifact_digest": vector["sha256"],
        "object_ref": {"project_id": "project-1", "digest": vector["sha256"], "size": len(content),
            "media_type": "application/zip", "relative_path": "objects/project-1/" + vector["sha256"][7:]}}
    service._current = lambda _kind, _identity: payload
    try:
        service._verify_saved_artifact("project-1", "legacy-export")
        rows.append({"input": vector["input"], "refused": False})
    except ExportRefused:
        rows.append({"input": vector["input"], "refused": True})
print(json.dumps(rows, ensure_ascii=False, indent=2))
if not all(row["refused"] for row in rows):
    raise SystemExit(1)
