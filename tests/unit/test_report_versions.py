from pathlib import Path

from aitest.core.reports import _next_report_revision
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
