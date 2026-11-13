"""Real-core pytest failure -> issue draft -> confirmation -> fix -> regression.

This is a component-level integration test: it drives the assembled workspace
core with real pytest subprocesses. It does not claim real Trae acceptance.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from aitest.application.project.onboarding import OnboardingForm, wizard_commands
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.approvals import EntryKind, TrustedActor, UserInteraction
from aitest.interfaces.local.native_confirmation import dispatch_native
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_default_start_source_chain import _chain_environment
from tests.unit.test_default_step_execution import execution_command


def _selection() -> dict[str, object]:
    return {
        "schema_version": "aitest.test-selection/1.0",
        "goal": "验证订单",
        "selections": [{
            "acceptance_item_ids": ["AC-ORDER-01"],
            "scope": {"kind": "file", "value": "tests/test_order.py"},
            "expected": "all_pass",
            "mock_scope": ["unknown"],
            "rationale": "运行订单测试",
        }],
    }


def _dispatch(core, command: Command):
    if command.action == "discover_tests":
        return core.api.dispatch(command, RELAY)
    return dispatch_native(
        core.api, command, prompt=lambda text: True, records=core.unit_of_work.repo,
    )


def _run_stage(core, form, receipts, stages):
    for stage in stages:
        for item in wizard_commands(form, stage, receipts):
            command = Command.model_validate(
                {key: value for key, value in item.items() if key != "key"}
                | {"request_id": uuid4().hex, "project_id": form.project_id}
            )
            result = _dispatch(core, command)
            assert result.error is None, (item["action"], result.error)
            receipts[item["key"]] = result.result


def _authorize(service, project, action, parameters, suffix: str):
    actor = TrustedActor(
        service.workspace_id, project, f"authorization-{suffix}", EntryKind.HUMAN_UI, True
    )
    with service.approvals.actors.bind(actor):
        challenge = service.approvals.prepare(
            project_id=project,
            action_intent_id=action.request.intent_id,
            preparation_intent_id=f"review-{suffix}-{action.request.intent_id}",
            request_id=f"review-{suffix}",
            action="authorize_step",
            target=action.request.step_id,
            parameters=parameters,
        )
    actor = TrustedActor(
        service.workspace_id,
        project,
        actor.session_id,
        EntryKind.HUMAN_UI,
        True,
        UserInteraction(
            f"gesture-{suffix}",
            actor.session_id,
            challenge.challenge_id,
            challenge.basis.input_digest,
        ),
    )
    with service.approvals.actors.bind(actor):
        service.grant(
            project_id=project,
            intent_id=action.request.intent_id,
            request_id=f"save-{suffix}",
            parameters=parameters,
            challenge_id=challenge.challenge_id,
        )


def _execute(core, form, run_id: str, step_id: str, intent: str, *, retry: bool):
    service = core.execution_authorizations
    parameters = service.prepare(
        project_id=form.project_id, run_id=run_id, step_id=step_id,
        intent_id=intent, request_id=f"{intent}-resolve",
    )
    _, action = service.resolver.read(form.project_id, parameters["execution_action_id"])
    _authorize(service, form.project_id, action, parameters, intent)
    if retry:
        command = Command(
            action="retry_step",
            project_id=form.project_id,
            request_id=f"{intent}-execute",
            intent_id=action.request.intent_id,
            expected_revision=0,
            target=step_id,
            parameters=parameters,
        )
    else:
        command = execution_command(form.project_id, action, parameters)
    executed = core.api.dispatch(command, RELAY)
    assert executed.error is None, executed.error
    return ExecutionFacts.model_validate(executed.result["execution_facts"])


def _verify(core, form, run_id: str, step_id: str, attempt_id: str, commit: str, intent: str):
    result = core.api.dispatch(Command(
        action="verify_pending", project_id=form.project_id,
        request_id=uuid4().hex, intent_id=intent, expected_revision=0,
        target=step_id,
        parameters={
            "run_id": run_id,
            "step_id": step_id,
            "attempt_id": attempt_id,
            "base_snapshot_commit_id": commit,
        },
    ), RELAY)
    assert result.error is None, result.error
    return result


def test_pytest_failure_draft_and_issue_lifecycle_real_core(tmp_path: Path) -> None:
    source = tmp_path / "source"
    tests = source / "tests"
    tests.mkdir(parents=True)
    marker = tmp_path / "marker-does-not-exist-yet"
    (tests / "test_order.py").write_text(
        "from pathlib import Path\n\n"
        f"MARKER = Path({str(marker)!r})\n\n"
        "def test_total():\n"
        "    assert MARKER.exists(), 'expected marker to exist'\n",
        encoding="utf-8",
    )
    core = assemble_workspace_core(
        tmp_path / "workspace",
        instance_id="pytest-defect-fixture",
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
        step = view.result["view"]["steps"][0]
        step_id = step["step_id"]

        facts = _execute(core, form, run_id, step_id, "pytest-execute", retry=False)
        attempt_id = facts.current_attempt_by_step[step_id]
        _verify(
            core, form, run_id, step_id, attempt_id, facts.snapshot_commit_id,
            "pytest-verify-failed",
        )

        failed_view = core.api.dispatch(Command(
            action="read_view", project_id=form.project_id, request_id=uuid4().hex,
            parameters={"view": "run", "run_id": run_id},
        ), RELAY)
        assert failed_view.error is None, failed_view.error
        rendered = failed_view.result["view"]
        failures = rendered["steps"][0]["pytest_failures"]
        assert len(failures) == 1
        assert "expected marker to exist" in (failures[0]["message"] or "")
        assert failures[0]["location_unknown"] is False
        assert "test_order.py" in (failures[0]["file"] or "")
        drafts = rendered["pytest_issue_drafts"]
        assert len(drafts) == 1, drafts
        draft = drafts[0]
        assert draft["step_id"] == step_id
        assert draft["attempt_id"] == attempt_id
        assert draft["node_ids"] == ["tests.test_order::test_total"]
        assert draft["evidence_refs"], draft

        report_id = "report-" + uuid4().hex
        report_created = _dispatch(core, Command(
            action="create_report", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="pytest-report", expected_revision=0,
            parameters={"report_id": report_id, "run_id": run_id, "issue_ids": []},
        ))
        assert report_created.error is None, report_created.error
        report_view = core.api.dispatch(Command(
            action="read_view", project_id=form.project_id, request_id=uuid4().hex,
            parameters={"view": "report", "record_id": report_id, "revision": 1},
        ), RELAY)
        assert report_view.error is None, report_view.error
        report = report_view.result["view"]["record"]
        gap_codes = {str(item["code"]) for item in report["decision"]["gaps"]}
        assert "critical_mock" in gap_codes, gap_codes
        assert report["pytest_failures"][0]["node_id"] == "tests.test_order::test_total"
        assert report["pytest_failures"][0]["location_unknown"] is False

        issue_id = draft["issue_id"]
        created = _dispatch(core, Command(
            action="update_issue", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="pytest-draft", expected_revision=0, target=issue_id,
            parameters={
                "issue_id": issue_id,
                "title": draft["title"],
                "links": {
                    "run_id": run_id,
                    "step_id": step_id,
                    "signature": draft["signature"],
                    "pytest_cluster": {
                        "run_id": run_id,
                        "step_id": step_id,
                        "signature": draft["signature"],
                    },
                    "layers": ["L2"],
                },
            },
        ))
        assert created.error is None, created.error

        issue_view = core.api.dispatch(Command(
            action="read_view", project_id=form.project_id, request_id=uuid4().hex,
            parameters={"view": "issue", "record_id": issue_id, "revision": 1},
        ), RELAY)
        assert issue_view.error is None, issue_view.error
        payload = issue_view.result["view"]["record"]
        assert payload["status"] == "draft"
        cluster = payload["links"]["pytest_cluster"]
        assert cluster["schema_version"] == "aitest.pytest-issue-draft/1.0"
        assert len(cluster["failures"]) == 1
        assert cluster["failures"][0]["node_id"] == "tests.test_order::test_total"
        assert cluster["failures"][0]["location_unknown"] is False
        assert payload["evidence_refs"]

        awaiting = _dispatch(core, Command(
            action="update_issue", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="pytest-awaiting", expected_revision=1, target=issue_id,
            parameters={
                "issue_id": issue_id,
                "awaiting_confirmation": True,
                "evidence_refs": list(payload["evidence_refs"]),
            },
        ))
        assert awaiting.error is None, awaiting.error
        confirmed = _dispatch(core, Command(
            action="update_issue", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="pytest-confirm", expected_revision=2, target=issue_id,
            parameters={
                "issue_id": issue_id,
                "confirm": {"severity": "P1", "owner": "developer"},
            },
        ))
        assert confirmed.error is None, confirmed.error
        started = _dispatch(core, Command(
            action="update_issue", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="pytest-start", expected_revision=3, target=issue_id,
            parameters={"issue_id": issue_id, "start": True},
        ))
        assert started.error is None, started.error

        linked_report = "report-" + uuid4().hex
        linked_created = _dispatch(core, Command(
            action="create_report", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="pytest-linked-report", expected_revision=0,
            parameters={"report_id": linked_report, "run_id": run_id,
                        "issue_ids": [issue_id]},
        ))
        assert linked_created.error is None, linked_created.error
        exported = _dispatch(core, Command(
            action="export_report", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="pytest-export", expected_revision=0,
            parameters={"report_id": linked_report, "kind": "markdown_summary",
                        "review_ids": []},
        ))
        assert exported.error is None, exported.error
        export_record = core.unit_of_work.repo.read(
            aggregate_kind="report_export",
            record_id=exported.result["record_id"],
            revision=1,
        ).payload
        markdown = (core.workspace.root / str(export_record["artifact_ref"])).read_text(
            encoding="utf-8"
        )
        assert issue_id in markdown
        assert "critical_mock" in markdown

        basis_text = _case_basis_text(core, facts)
        fix_version = _observed_source_digest(facts)
        fixed = _dispatch(core, Command(
            action="record_fix", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="pytest-fix", expected_revision=4, target=issue_id,
            parameters={
                "issue_id": issue_id,
                "fix_version": fix_version,
                "regression_method": "pytest scope rerun",
                "closure_criteria": basis_text,
            },
        ))
        assert fixed.error is None, fixed.error
        arranged = _dispatch(core, Command(
            action="request_regression", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="pytest-regression", expected_revision=5, target=issue_id,
            parameters={"issue_id": issue_id, "note": "fix marker"},
        ))
        assert arranged.error is None, arranged.error
        assert arranged.result["revision"] == 6

        # The original failing attempt cannot close the issue.
        stale = _dispatch(core, Command(
            action="close_issue", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="pytest-close-stale", expected_revision=6, target=issue_id,
            parameters={
                "issue_id": issue_id,
                "case_id": facts.steps[0].case_id,
                "run_id": run_id,
                "attempt_id": attempt_id,
                "assertion_ref": facts.steps[0].assertion_refs[0],
            },
        ))
        assert stale.error is not None

        marker.write_text("fixed", encoding="utf-8")
        retried = _execute(core, form, run_id, step_id, "pytest-retry", retry=True)
        new_attempt = retried.current_attempt_by_step[step_id]
        assert new_attempt != attempt_id
        _verify(
            core, form, run_id, step_id, new_attempt, retried.snapshot_commit_id,
            "pytest-verify-passed",
        )
        step_fact = next(item for item in retried.steps if item.step_id == step_id)
        closed = _dispatch(core, Command(
            action="close_issue", project_id=form.project_id, request_id=uuid4().hex,
            intent_id="pytest-close", expected_revision=6, target=issue_id,
            parameters={
                "issue_id": issue_id,
                "case_id": step_fact.case_id,
                "run_id": run_id,
                "attempt_id": new_attempt,
                "assertion_ref": step_fact.assertion_refs[0],
                "assertion_outcome": "passed",
            },
        ))
        assert closed.error is None, closed.error
        assert closed.result["revision"] == 7
    finally:
        core.lifetime_lock.release()


def _case_basis_text(core, facts: ExecutionFacts) -> str:
    plan_ref = facts.plan_revision
    plan = core.unit_of_work.repo.read(
        aggregate_kind="plan", record_id=plan_ref.revision_id,
        revision=plan_ref.revision_no,
    ).payload
    refs = plan["case_revisions"]
    assert isinstance(refs, list) and refs
    case = core.unit_of_work.repo.read(
        aggregate_kind="case", record_id=refs[0]["case_id"], revision=refs[0]["revision"],
    ).payload
    text = case["assertion_basis"]["text"]
    assert isinstance(text, str) and text
    return text


def _observed_source_digest(facts: ExecutionFacts) -> str:
    digests = {
        item.observed_source_digest
        for item in facts.source_verifications
        if not item.gap_ids and item.state.value == "verified"
    }
    assert len(digests) == 1, digests
    return digests.pop()
