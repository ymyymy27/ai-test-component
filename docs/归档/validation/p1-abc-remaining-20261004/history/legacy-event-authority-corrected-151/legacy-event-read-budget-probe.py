"""Actual sparse files prove recovery refuses an oversized body before opening it.

Run: .venv/Scripts/python.exe docs/validation/p1-abc-remaining-20261004/legacy-event-read-budget-probe.py
Only temporary workspaces are written. This is supplemental validation, not real acceptance.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from aitest.infrastructure.file_store.commit_manifest import CommitMaterialError
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork


def check(kind):
    with TemporaryDirectory(prefix="aitest-legacy-event-budget-") as directory:
        root = Path(directory)
        journal = FileEventJournal(root, instance_id="budget-core")
        unit = FileUnitOfWork(root, journal=journal)
        unit.begin("request", "project", intent_id="intent")
        unit.stage_record(
            aggregate_kind="case", record_id="saved", expected_revision=0,
            payload={"project_id": "project"},
        )
        with patch.object(journal, "commit_boundary", side_effect=OSError("controlled failure")):
            try:
                unit.commit("request")
            except OSError:
                pass
            else:
                raise AssertionError("failure injection did not run")
        unit.rollback("request")
        target, budget = {
            "authority": (root / "records.json", 64 * 1024 * 1024),
            "staging": (root / "event-log/staging/1.jsonl", 64 * 1024 * 1024),
            "boundary": (root / "event-log/boundaries/1.json", 64 * 1024),
        }[kind]
        with target.open("wb") as handle:
            handle.truncate(budget + 1)
        body_reads = 0
        original_open = Path.open

        def observed_open(path, *args, **kwargs):
            nonlocal body_reads
            if path == target:
                body_reads += 1
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", observed_open):
            try:
                journal.reconcile(committed_sequences={1})
            except CommitMaterialError:
                pass
            else:
                raise AssertionError("oversized recovery input was accepted")
        assert body_reads == 0, "oversized body was opened before budget rejection"
        assert target.stat().st_size == budget + 1, "original material changed"
        assert journal.read().events == (), "oversized material published an event"
        print(json.dumps({"material": kind, "budget": budget, "size": budget + 1,
                          "body_reads": body_reads, "original_retained": True,
                          "published_events": 0, "result": "passed"}))


if __name__ == "__main__":
    for material in ("authority", "staging", "boundary"):
        check(material)
