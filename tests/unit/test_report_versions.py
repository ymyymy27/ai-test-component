import subprocess
from pathlib import Path

from aitest.core.reports import _local_source_commit, _next_report_revision, _service_report_rows
from aitest.models import Report
from aitest.storage.files import write_model
from aitest.storage.workspace import Workspace


def test_report_revision_increments_per_run(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    reports_dir = ws.project_dir("proj_1") / "reports"
    reports_dir.mkdir(parents=True)
    write_model(
        reports_dir / "rep_1.json",
        Report(report_id="rep_1", run_id="run_1", outcome="passed", revision=1),
    )
    write_model(
        reports_dir / "rep_2.json",
        Report(report_id="rep_2", run_id="run_1", outcome="failed", revision=2),
    )
    write_model(
        reports_dir / "rep_3.json",
        Report(report_id="rep_3", run_id="run_2", outcome="passed", revision=7),
    )
    assert _next_report_revision(ws, "proj_1", "run_1") == 3
    assert _next_report_revision(ws, "proj_1", "run_3") == 1


def test_service_report_rows_supports_single_service_shape() -> None:
    rows = _service_report_rows(
        {"start": "python app/server.py", "health": "http://127.0.0.1:18765/health"}
    )
    assert rows == [
        {"name": "default", "health_configured": True, "start_configured": True}
    ]
    assert _service_report_rows({"backend": {"start": "x", "health": "y"}}) == [
        {"name": "backend", "health_configured": True, "start_configured": True}
    ]


def test_local_source_commit_falls_back_to_git_head(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert _local_source_commit(repo) == head
    assert _local_source_commit(tmp_path / "missing") is None
