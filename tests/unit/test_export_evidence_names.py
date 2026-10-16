"""Business evidence identity never becomes a platform-specific ZIP member path."""

import io
import json
import re
import zipfile

from tests.unit.test_saved_exports import _export, _report_with_review
from tests.unit.test_saved_reports import _Harness


def test_command_colon_ids_and_case_aliases_export_with_explicit_identity_mapping(tmp_path):
    identities = ("evidence:attempt:stdout:0", "CON", "Evidence", "evidence", "证据")
    harness = _Harness(tmp_path, evidence=identities)
    _report_with_review(harness)
    result = _export(harness, kind="evidence_bundle", attachment_refs=list(identities))
    saved = harness.saved_export(str(result["record_id"]))
    raw = harness.artifact_bytes(
        str(saved["artifact_ref"]), expected_sha256=str(saved["artifact_digest"])
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        entries = manifest["attachments"]
        assert {entry["evidence_id"] for entry in entries} == set(identities)
        assert len({entry["path"].casefold() for entry in entries}) == len(identities)
        for entry in entries:
            assert re.fullmatch(r"evidence/[a-f0-9]{64}\.bin", entry["path"])
            assert archive.read(entry["path"]) == b"abc"
    recalled = _export(harness, kind="evidence_bundle", attachment_refs=list(identities))
    assert recalled == result
