import json
from pathlib import Path

from aitest.infrastructure.file_store.generation_layout import record_generation


def test_generation_layout_records_references_not_payloads(tmp_path: Path) -> None:
    manifest = {
        "schema": "aitest.commit-manifest/1",
        "generation_id": "gen 1",
        "commit_sequence": 3,
        "record_header": {"root": "abc"},
        "created": [{"aggregate_kind": "case", "record_id": "c1", "revision": 1}],
        "index_root": {"root": "idx"},
        "event_root": {"root": "ev"},
        "parent_manifest": "sha256:prev",
    }

    base = record_generation(tmp_path, manifest)

    assert base == tmp_path / "generations" / "gen_1"
    records = json.loads((base / "records.json").read_text(encoding="utf-8"))
    assert records["schema"] == "aitest.generation/1.0"
    assert records["created"][0]["record_id"] == "c1"
    assert records["record_header"] == {"root": "abc"}
    assert json.loads((base / "indexes.json").read_text(encoding="utf-8"))["index_root"] == {
        "root": "idx"
    }
    assert json.loads((base / "events.json").read_text(encoding="utf-8"))["event_root"] == {
        "root": "ev"
    }
    assert json.loads((base / "manifests.json").read_text(encoding="utf-8"))[
        "parent_manifest"
    ] == "sha256:prev"
