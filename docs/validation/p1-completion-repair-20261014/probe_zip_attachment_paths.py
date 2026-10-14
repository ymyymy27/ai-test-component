"""In-memory counterexample for ZIP member naming, not an acceptance run.

Uses the actual bundle writer and validated EvidenceFact, with synthetic report
context and a memory object reader. It never extracts the hostile member paths.
"""
import base64
import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root), str(root / "src")]
if len(sys.argv) == 2 and sys.argv[1] != "--save-legacy":
    spec = importlib.util.spec_from_file_location("zip_path_preview", sys.argv[1])
    assert spec is not None and spec.loader is not None
    preview = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = preview
    spec.loader.exec_module(preview)
    ExportRefused, SavedExportService = preview.ExportRefused, preview.SavedExportService
else:
    from aitest.application.review.saved_exports import ExportRefused, SavedExportService
from aitest.contracts.execution_facts import EvidenceFact
from tests.unit.test_reuse_consumption import _outcome

content = b"abc"
digest = "sha256:" + hashlib.sha256(content).hexdigest()
base = _outcome().source_snapshots[0].evidence_refs[0].model_dump(mode="json")

class MemoryObjects:
    def read_bytes(self, reference):
        assert reference.digest == digest and reference.size == len(content)
        return content

service = object.__new__(SavedExportService)
service._objects = MemoryObjects()
service._clock = SimpleNamespace(now=lambda: datetime(2026, 10, 14, tzinfo=UTC))
snapshot = SimpleNamespace(
    report_id="report-path-probe", content_revision=1,
    context=SimpleNamespace(scope_revision="scope-1", scope_name="probe", acceptance_revision="ac-1"),
)
vectors = [("../../escape",), (r"..\..\escape",), ("CON",), ("Evidence-A", "evidence-a")]
rows = []
legacy = []
for identities in vectors:
    facts = tuple(EvidenceFact.model_validate({**base, "evidence_id": identity,
        "object_digest": digest, "object_size": len(content)}) for identity in identities)
    try:
        blob = service._bundle("probe", snapshot, (), evidence_index=[
            {"evidence_id": identity, "status": "included", "sha256": digest}
            for identity in identities], excluded=[], missing=[], facts=None, source_evidence=facts)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            rows.append({"input": identities, "refused": False, "members": archive.namelist(),
                "manifest_files": json.loads(archive.read("manifest.json"))["files"]})
        legacy.append({"input": identities, "artifact_base64": base64.b64encode(blob).decode("ascii"),
            "sha256": "sha256:" + hashlib.sha256(blob).hexdigest(), "size": len(blob)})
    except ExportRefused:
        rows.append({"input": identities, "refused": True})
print(json.dumps(rows, ensure_ascii=False, indent=2))
if "--save-legacy" in sys.argv:
    target = Path(__file__).with_name("zip-legacy-vectors.json")
    assert not target.exists() and len(legacy) == len(vectors)
    target.write_text(json.dumps({"scope": "Actual pre-fix bundle writer, synthetic validated facts/report and memory objects; no extraction or acceptance claim.",
        "source_commit": "dba1d4b51d681cdc5f2e2bacf89a50773a0693ae", "vectors": legacy}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
if not all(row["refused"] for row in rows):
    raise SystemExit(1)
