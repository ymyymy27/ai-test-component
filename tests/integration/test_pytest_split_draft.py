"""Real-core split of a pytest failure cluster into a child issue draft."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from aitest.application.project.onboarding import OnboardingForm
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from tests.integration.test_pytest_defect_chain import (
    _dispatch,
    _execute,
    _run_stage,
    _selection,
    _verify,
)
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_default_start_source_chain import _chain_environment


def test_pytest_cluster_can_split_selected_failures_real_core(tmp_path: Path) -> None:
    source = tmp_path / "source"
    tests = source / "tests"
    tests.mkdir(parents=True)
    marker = tmp_path / "marker"
    (tests / "test_order.py").write_text(
        "from pathlib import Path\n\n"
        f"MARKER = Path({str(marker)!r})\n\n"
        "def test_create():\n"
        "    assert MARKER.exists(), 'expected marker to exist'\n\n"
        "def test_cancel():\n"
        "    assert MARKER.exists(), 'expected marker to exist'\n",
        encoding="utf-8",
    )
    core = assemble_workspace_core(
        tmp_path / "workspace",
        instance_id="pytest-split-fixture",
        environment_resolver=_chain_environment(),
    )
    form = OnboardingForm(
        project_id="guided-" + str(uuid4()),
        workspace_id=core.workspace.workspace_id,
        source=str(source),
        entry="tests",
        name="订单检查",
        goal="验证订单",
        data_policy="仅本地测试",
        dependency_declaration="仅标准库",
        test_mode=True,
        run_tier="on_demand",
    )
    receipts: dict[str, object] = {}
    try:
        _run_stage(core, form, receipts, ("project", "discover"))
        selection_form = form.model_copy(update={
            "proposal": _selection(),
            "test_inventory": receipts["discover"],
        })
        _run_stage(core, selection_form, receipts, ("commit", "prepare", "register"))
        run_id = receipts["register"]["run_id"]
        view = core.api.dispatch(Command(
            action="read_view", project_id=form.project_id, request_id=uuid4().hex,
            parameters={"view": "run", "run_id": run_id},
        ), RELAY)
        assert view.error is None, view.error
        step_id = view.result["view"]["steps"][0]["step_id"]

        facts = _execute(core, form, run_id, step_id, "pytest-split-execute", retry=False)
        attempt_id = facts.current_attempt_by_step[step_id]
        _verify(
            core, form, run_id, step_id, attempt_id, facts.snapshot_commit_id,
            "pytest-split-verify",
        )
        rendered = core.api.dispatch(Command(
            action="read_view", project_id=form.project_id, request_id=uuid4().hex,
            parameters={"view": "run", "run_id": run_id},
        ), RELAY)
        assert rendered.error is None, rendered.error
        drafts = rendered.result["view"]["pytest_issue_drafts"]
        assert len(drafts) == 1, drafts
        parent = drafts[0]
        assert len(parent["node_ids"]) == 2, parent

        created = _dispatch(core, Command(
            action="update_issue", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="split-parent", expected_revision=0, target=parent["issue_id"],
            parameters={
                "issue_id": parent["issue_id"],
                "title": parent["title"],
                "links": {
                    "run_id": run_id,
                    "step_id": step_id,
                    "signature": parent["signature"],
                    "pytest_cluster": {
                        "run_id": run_id,
                        "step_id": step_id,
                        "signature": parent["signature"],
                    },
                    "layers": ["L2"],
                },
            },
        ))
        assert created.error is None, created.error

        first_node = parent["node_ids"][0]
        child_id = "issue:pytest:split:" + uuid4().hex[:16]
        split = _dispatch(core, Command(
            action="update_issue", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="split-child", expected_revision=0, target=child_id,
            parameters={
                "issue_id": child_id,
                "title": f"{parent['title']}（拆分）",
                "links": {
                    "pytest_split": {
                        "source_issue_id": parent["issue_id"],
                        "node_ids": [first_node],
                    },
                },
            },
        ))
        assert split.error is None, split.error

        child = core.api.dispatch(Command(
            action="read_view", project_id=form.project_id, request_id=uuid4().hex,
            parameters={"view": "issue", "record_id": child_id, "revision": 1},
        ), RELAY)
        assert child.error is None, child.error
        child_record = child.result["view"]["record"]
        assert child_record["status"] == "draft"
        assert child_record["links"]["split_from"] == parent["issue_id"]
        child_cluster = child_record["links"]["pytest_cluster"]
        assert [item["node_id"] for item in child_cluster["failures"]] == [first_node]
        assert child_record["evidence_refs"]

        source = core.api.dispatch(Command(
            action="read_view", project_id=form.project_id, request_id=uuid4().hex,
            parameters={"view": "issue", "record_id": parent["issue_id"], "revision": 1},
        ), RELAY)
        assert source.error is None, source.error
        assert len(source.result["view"]["record"]["links"]["pytest_cluster"]["failures"]) == 2
    finally:
        core.lifetime_lock.release()
