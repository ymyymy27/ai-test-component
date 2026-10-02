"""Additional contract probes on 9bd4337; synthetic data in temporary workspaces.

Run: uv run --no-sync python -m docs.validation.p1-audit-20261002.deep_probes
No real credentials, model endpoint, project data, or business system are used.
The command probe starts disposable Python processes and cleans up its own handles.
"""

from __future__ import annotations

import ctypes
import io
import json
import subprocess
import sys
import tempfile
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from aitest.application.planning.persistence import load_case, save_case
from aitest.contracts.commands import Command
from aitest.contracts.secrets import ResolvedSecret
from aitest.infrastructure.adapters.execution import command as command_module
from aitest.infrastructure.adapters.model import HttpModelProvider
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from tests.unit.test_a_model_provider import _call
from tests.unit.test_command_adapter import _adapter, _request, _wait_for_terminal
from tests.unit.test_planning_persistence_real_store import _case, _start


def cross_project(root: Path) -> dict[str, object]:
    stack = _start(root)
    first = save_case(_case(), project_id="project-a", unit_of_work=stack.unit_of_work)
    second = save_case(
        _case(revision=2),
        project_id="project-b",
        unit_of_work=stack.unit_of_work,
        expected_revision=1,
    )
    loaded = load_case(stack.reader, project_id="project-b", case_id="case-1", revision=2)
    history = [
        stack.reader.read(aggregate_kind="case", record_id="case-1", revision=rev).payload[
            "project_id"
        ]
        for rev in (1, 2)
    ]
    assert (first.revision, second.revision, loaded.revision) == (1, 2, 2)
    assert history == ["project-a", "project-b"]
    return {
        "accepted_cross_project_revision": True,
        "lineage_projects": history,
        "old_revision_preserved": True,
        "boundary": "B save_case through A real file-backed ports; temporary projects",
    }


def revision_mismatch(root: Path) -> dict[str, object]:
    stack = _start(root)
    first = save_case(_case(revision=9), project_id="project-a", unit_of_work=stack.unit_of_work)
    loaded = load_case(stack.reader, project_id="project-a", case_id="case-1", revision=1)
    second = save_case(
        _case(revision=9),
        project_id="project-a",
        unit_of_work=stack.unit_of_work,
        expected_revision=1,
    )
    again = load_case(stack.reader, project_id="project-a", case_id="case-1", revision=2)
    assert (first.revision, second.revision, loaded.revision, again.revision) == (1, 2, 9, 9)
    return {
        "storage_revisions": [first.revision, second.revision],
        "loaded_case_revisions": [loaded.revision, again.revision],
        "boundary": "real save/load path; no invalid dataclass or edited storage",
    }


def failed_commit(root: Path) -> dict[str, object]:
    first = FileUnitOfWork(root)
    first.begin("request-a", "project-a")
    first.stage_record(
        aggregate_kind="case",
        record_id="case-a",
        expected_revision=0,
        payload={"project_id": "project-a"},
    )
    with patch.object(
        first.repo, "commit_transaction", side_effect=OSError("synthetic prepublish failure")
    ):
        try:
            first.commit("request-a")
        except OSError:
            pass
        else:
            raise AssertionError("failure injection did not fire")
    second = FileUnitOfWork(root)
    second.begin("request-b", "project-b")
    try:
        second.stage_record(
            aggregate_kind="case",
            record_id="case-b",
            expected_revision=0,
            payload={"project_id": "project-b"},
        )
        original_save = second.repo._save
        retry_result: dict[str, object] = {}
        competing_lock_held = False

        def interleaved_save(data: dict[str, object]) -> None:
            nonlocal competing_lock_held
            competing_lock_held = second._lock_context is not None
            retry_result.update(first.commit("request-a"))
            original_save(data)

        with patch.object(second.repo, "_save", side_effect=interleaved_save):
            second_result = second.commit("request-b")
        first_record_lost = first.repo.current_revision("case", "case-a") == 0
        assert retry_result["state"] == "committed" and competing_lock_held and first_record_lost
        return {
            "competing_transaction_lock_held": competing_lock_held,
            "retry_without_reacquiring_lock": first._lock_context is None,
            "retry_state": retry_result["state"],
            "commit_sequences": [retry_result["commit_sequence"], second_result["commit_sequence"]],
            "successful_retry_record_lost_after_competing_publish": first_record_lost,
            "boundary": (
                "two real UOW objects; failure injection and deterministic publish interleaving"
            ),
        }
    finally:
        if second.project is not None:
            second.rollback("request-b")
        if first.project is not None:
            first.rollback("request-a")


def transaction_protocol(root: Path) -> dict[str, object]:
    raw = FileUnitOfWork(root)
    api = LocalAPI("synthetic-instance", raw.workspace.workspace_id, transaction_port=raw)
    session = Session("synthetic-session", EntryKind.INTERACTIVE_CLI)
    begun = api.dispatch(
        Command(request_id="request-begin", action="begin", project_id="project-a"), session
    )
    try:
        raw.stage_record(
            aggregate_kind="case",
            record_id="case-a",
            expected_revision=0,
            payload={"project_id": "project-a"},
        )
        same = api.dispatch(Command(request_id="request-begin", action="commit"), session)
        different = api.dispatch(Command(request_id="request-commit", action="commit"), session)
        rollback = api.dispatch(Command(request_id="request-rollback", action="rollback"), session)
        assert begun.error is None and same.error is not None
        assert different.error is not None and rollback.error is not None
        assert same.error.code == "REQUEST_CONFLICT"
        return {
            "begin_state": begun.result["state"],
            "same_request_commit_error": same.error.code,
            "different_request_commit_error": different.error.message,
            "different_request_rollback_error": rollback.error.message,
            "transaction_still_active": raw.project is not None,
            "boundary": "real LocalAPI dispatch; staging directly isolates identity conflict",
        }
    finally:
        raw.rollback("request-begin")


def rejected_restore(root: Path) -> dict[str, object]:
    backup = root / "bad-backup"
    backup.mkdir(parents=True)
    (backup / "backup.json").write_text(
        json.dumps({"files": {"../outside.json": "synthetic-digest"}}),
        encoding="utf-8",
    )
    target = root / "target"
    report = RecoveryOrchestrator(root / "workspace", instance_id="synthetic").restore_backup(
        backup=backup,
        target=target,
    )
    assert report.restore is not None and report.restore.state == "rejected"
    assert report.state == "repaired" and report.integrity_ok is True and not target.exists()
    return {
        "restore_state": report.restore.state,
        "restore_verified": report.restore.verified,
        "orchestrator_state": report.state,
        "orchestrator_integrity_ok": report.integrity_ok,
        "target_created": target.exists(),
        "boundary": "invalid relative manifest; no restored files or external paths written",
    }


class SyntheticHTTPSHandler(urllib.request.BaseHandler):
    """Replace socket I/O only; retain urllib's real redirect and error processing."""

    handler_order = 100

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, bool]] = []

    def https_open(self, request: urllib.request.Request) -> object:
        self.seen.append(
            (
                request.full_url,
                request.get_method(),
                request.get_header("Authorization") == "Bearer synthetic-audit-token",
            )
        )
        headers = Message()
        if len(self.seen) == 1:
            headers["Location"] = "https://unconfirmed.invalid/received"
            response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
            response.msg = "Found"
            return response
        body = json.dumps({"id": "synthetic", "choices": [{"message": {"content": "safe draft"}}]})
        response = urllib.response.addinfourl(
            io.BytesIO(body.encode()), headers, request.full_url, 200
        )
        response.msg = "OK"
        return response


def model_redirect() -> dict[str, object]:
    handler = SyntheticHTTPSHandler()
    opener = urllib.request.build_opener(handler)
    secret = ResolvedSecret("model", "synthetic", "synthetic", "synthetic-audit-token")
    try:
        with patch("urllib.request._opener", opener):
            result = HttpModelProvider("https://confirmed.invalid", secret=secret).call(
                _call(endpoint="https://confirmed.invalid"),
            )
        assert result.status.value == "ok" and len(handler.seen) == 2 and handler.seen[1][2]
        return {
            "initial_url": handler.seen[0][0],
            "redirect_url": handler.seen[1][0],
            "redirect_method": handler.seen[1][1],
            "authorization_forwarded": handler.seen[1][2],
            "provider_result": result.status.value,
            "boundary": "real urllib redirect flow, synthetic HTTPS I/O, no network or real token",
        }
    finally:
        secret.clear()


def unsealed_capture(root: Path) -> dict[str, object]:
    spool = FileSpoolStore(root)
    adapter = _adapter(spool, block_size=4)
    marker = root / "child.pid"
    # Parent remains alive long enough for Windows Job assignment, then exits first.
    child_script = (
        "import time; print('prefix', flush=True); time.sleep(30); print('tail', flush=True)"
    )
    parent_script = (
        "import subprocess,sys,time,pathlib; time.sleep(0.25); "
        f"p=subprocess.Popen([sys.executable,'-c',{child_script!r}]); "
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); "
        "time.sleep(0.2)"
    )
    handle = adapter.start(_request("python", ("-c", parent_script)))
    runtime = adapter._runtime_for(handle)
    job_assigned = runtime.job_handle is not None
    try:
        _wait_for_terminal(adapter, handle)
        captured = adapter.collect(handle)
        alive_at_return = sum(thread.is_alive() for thread in runtime.threads)
        summary_count = len(list((root / "spool" / "attempt-1").glob("redaction-*.json")))
        saved_bytes = sum(cursor.offset for cursor in captured.output_cursors)
        assert captured.complete and captured.capture_completeness.value == "complete"
        assert alive_at_return > 0 and summary_count < 2
        if marker.exists() and sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", marker.read_text(), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        for thread in runtime.threads:
            thread.join(timeout=3)
        later = spool.read_manifest("attempt-1")
        return {
            "collection_complete": captured.complete,
            "capture_completeness": captured.capture_completeness.value,
            "reader_threads_alive_at_return": alive_at_return,
            "redaction_summaries_at_return": summary_count,
            "saved_bytes_in_returned_collection": saved_bytes,
            "saved_bytes_after_cleanup_and_seal": sum(cursor.offset for cursor in later.cursors),
            "windows_job_assigned": job_assigned,
            "boundary": (
                "real disposable Python child inherited streams on current Windows; "
                "no business action"
            ),
        }
    finally:
        adapter._cleanup_group(runtime)
        if runtime.process.poll() is None:
            runtime.process.kill()
            runtime.process.wait(timeout=3)
        if marker.exists():
            child_pid = int(marker.read_text())
            # This PID was created by this probe, not discovered from unrelated processes.
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        for thread in runtime.threads:
            thread.join(timeout=3)


def windows_job_access() -> dict[str, object]:
    if sys.platform != "win32":
        return {"state": "not_verified", "reason": "requires native Windows"}
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    original_job = None
    corrected_job = None
    try:
        ctypes.set_last_error(0)
        original_job = command_module._assign_windows_job(process.pid)
        original_error = ctypes.get_last_error()
        kernel32 = command_module._kernel32()
        native_open = kernel32.OpenProcess

        def open_with_terminate(access: int, inherit: bool, pid: int) -> int:
            return native_open(access | 0x0001, inherit, pid)

        # Memory-only control: add PROCESS_TERMINATE for this probe's own child.
        with (
            patch.object(command_module, "_kernel32", return_value=kernel32),
            patch.object(kernel32, "OpenProcess", side_effect=open_with_terminate),
        ):
            corrected_job = command_module._assign_windows_job(process.pid)
        assert original_job is None and original_error == 5 and corrected_job is not None
        return {
            "original_job_assigned": original_job is not None,
            "original_win32_error": original_error,
            "with_process_terminate_job_assigned": corrected_job is not None,
            "boundary": "native Windows API on one disposable Python child; in-memory control only",
        }
    finally:
        command_module._close_job(original_job)
        command_module._close_job(corrected_job)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="aitest-deep-audit-") as directory:
        root = Path(directory)
        return {
            "A-OWNERSHIP-01-cross-project-lineage": cross_project(root / "ownership"),
            "B-REVISION-01-payload-storage": revision_mismatch(root / "revision"),
            "A-UOW-01-failed-commit-retry": failed_commit(root / "commit"),
            "A-PROTOCOL-01-transaction-identity": transaction_protocol(root / "protocol"),
            "A-RECOVERY-01-rejected-restore": rejected_restore(root / "restore"),
            "A-MODEL-01-redirect-authorization": model_redirect(),
            "C-CAPTURE-01-parent-exit-before-stream-seal": unsealed_capture(root / "capture"),
            "C-PROCESS-01-windows-job-access": windows_job_access(),
        }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
