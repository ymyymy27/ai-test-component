"""Observe four additional audit findings with synthetic inputs.

Run from the repository root with:
    uv run python -m docs.validation.p1-audit-20261001.additional_probes
Results describe current behavior, not successful product acceptance.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from aitest.application.planning.preparation import payload_hash
from aitest.application.planning.prepare_run import preparation_payload
from aitest.application.planning.publish import publish_plan
from aitest.domain.execution.runs import OutputStreamName
from aitest.domain.planning.plans import ConfirmationRecord, PlanPublicationStatus
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.support.memory_substrate import MemoryReader, MemoryStore, MemoryUnitOfWork
from tests.unit.test_prepare_run_flow import _build_case, _build_plan, _inputs_from


def run() -> dict[str, object]:
    results: dict[str, object] = {}
    with TemporaryDirectory(prefix="aitest-extra-probes-") as directory:
        spool = FileSpoolStore(Path(directory))
        writer = spool.open_stream(
            run_id="run",
            step_id="step",
            attempt_id="attempt",
            stream_name=OutputStreamName.STDOUT,
            block_size=1,
        )
        # Inspect the private handle only to distinguish stream/manifest fsync calls.
        stream_fd = writer._handle.fileno()
        fsynced: list[int] = []
        real_fsync = os.fsync

        def track_fsync(fd: int) -> None:
            fsynced.append(fd)
            real_fsync(fd)

        try:
            with patch("aitest.infrastructure.file_store.spool.os.fsync", side_effect=track_fsync):
                writer.append(b"synthetic output\n")
                manifest = spool.read_manifest("attempt")
                results["C-DURABILITY-01"] = {
                    "stream_fsync_calls_before_ack": fsynced.count(stream_fd),
                    "other_file_fsync_calls_before_ack": len(fsynced) - fsynced.count(stream_fd),
                    "acknowledged_durable": manifest.cursors[0].durable,
                    "acknowledged_offset": manifest.cursors[0].offset,
                }
        finally:
            writer.close()

    case = _build_case()
    foreign_confirmation = ConfirmationRecord(
        confirmation_id="confirmation-for-other-case",
        case_id="other-case",
        basis_revision=case.assertion_basis.revision,
        basis_text_digest=case.assertion_basis.text_digest,
        confirmed_at_commit="commit-1",
    )
    own_confirmation = replace(foreign_confirmation, case_id=case.case_id)
    results["B-CONFIRMATION-01"] = {
        "case_id": case.case_id,
        "confirmation_case_id": foreign_confirmation.case_id,
        "state_without_confirmation": case.effective_basis_state(()).value,
        "state_with_own_confirmation": case.effective_basis_state((own_confirmation,)).value,
        "effective_basis_state": case.effective_basis_state((foreign_confirmation,)).value,
    }

    plan = replace(_build_plan(case), status=PlanPublicationStatus.DRAFT, confirmation_id=None)
    missing_scope = replace(
        plan.scope, required_case_ids=plan.scope.required_case_ids | {"missing-case"}
    )
    store = MemoryStore()
    missing_result = publish_plan(
        replace(plan, scope=missing_scope),
        project_id="project-1",
        cases=(case,),
        reader=MemoryReader(store),
        unit_of_work=MemoryUnitOfWork(store),
    )
    store = MemoryStore()
    wrong_revision_result = publish_plan(
        plan,
        project_id="project-1",
        cases=(replace(case, revision=2),),
        reader=MemoryReader(store),
        unit_of_work=MemoryUnitOfWork(store),
    )
    results["B-PUBLICATION-01"] = {
        "missing_required_case_id": "missing-case",
        "missing_required_case_published": missing_result.value is not None,
        "frozen_revision": plan.case_revisions[0].revision,
        "provided_revision": 2,
        "wrong_case_revision_published": wrong_revision_result.value is not None,
    }

    inputs = _inputs_from(_build_plan(case), case)
    first = inputs.case_revisions[0]
    second = first.model_copy(
        update={"case_id": "case-2", "revision": 2, "digest": "sha256:case-2"}
    )
    inputs_a = replace(inputs, case_revisions=(first, second))
    inputs_b = replace(
        inputs,
        case_revisions=(
            first.model_copy(update={"revision": 2}),
            second.model_copy(update={"revision": 1}),
        ),
    )
    hash_a = payload_hash(preparation_payload(inputs_a))
    hash_b = payload_hash(preparation_payload(inputs_b))
    results["B-HASH-01"] = {
        "mapping_before": [
            {"case_id": ref.case_id, "revision": ref.revision} for ref in inputs_a.case_revisions
        ],
        "mapping_after": [
            {"case_id": ref.case_id, "revision": ref.revision} for ref in inputs_b.case_revisions
        ],
        "case_revision_mapping_changed": inputs_a.case_revisions != inputs_b.case_revisions,
        "hash_before": hash_a,
        "hash_after": hash_b,
        "same_hash": hash_a == hash_b,
    }
    return results


if __name__ == "__main__":
    observed = run()
    output = Path(__file__).with_name("additional-probe-results.json")
    output.write_text(json.dumps(observed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(observed, ensure_ascii=False, indent=2))
