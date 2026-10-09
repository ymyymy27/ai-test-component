"""生产动作解析器的构造与反例。

用最小冻结载荷构造 `Run`/`Step`/`prepared`/`step_content`，覆盖：身份与摘要自洽、
冻结唯一适配器键、参数逐字、冻结步骤截止、start 准入摘要必需，以及
"步骤入口适配器与绑定不一致""缺准入""缺绑定/快照/环境""尝试序号或副作用类别非法"等拒绝。
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from aitest.application.execution.action_resolver import (
    ActionResolutionBlocked,
    SavedActionResolver,
)
from aitest.domain.execution.authorization import ResolvedExecutionAction
from aitest.domain.execution.runs import (
    AdapterKind,
    PlanRevisionRef,
    RegisteredEntryRef,
    Run,
    RunTier,
    SideEffectClass,
    Step,
    StepLevel,
    StepRevisionRef,
)
from aitest.domain.project.context import IsolationMode

_DIGEST = "sha256:" + "a" * 64
_SNAPSHOT = "step-revision-1"


def _plan() -> PlanRevisionRef:
    return PlanRevisionRef(revision_id="plan-1", revision_no=1, digest=_DIGEST)


def _run() -> Run:
    return Run(
        run_id="run-1",
        project_id="project-1",
        origin_workspace_id="workspace-1",
        intent_id="run-intent",
        tier=RunTier.FULL,
        driver="planned",
        conclusion_ceiling="passable",
        plan_revision_ref=_plan(),
        environment_ref="environment-1@1",
        environment_isolation_mode=IsolationMode.VENV,
        rules_revision="rules-1@1",
    )


def _step(adapter: AdapterKind = AdapterKind.COMMAND) -> Step:
    return Step(
        step_id="step-1",
        run_id="run-1",
        ordinal=1,
        case_id="case-1",
        level=StepLevel.L1,
        step_revision_ref=StepRevisionRef(
            step_revision_id=_SNAPSHOT, revision_no=1, digest=_DIGEST
        ),
        registered_entry_ref=RegisteredEntryRef(
            entry_id="public-command",
            adapter_kind=adapter,
            entrypoint="tools/run.py",
            arguments=("tests/acceptance",),
        ),
    )


def _prepared(*, adapter_versions: Mapping[str, str] | None = None) -> dict[str, object]:
    return {
        "execution_source": {
            "registered_entry": "public-command",
            "entry_arguments": ("tests/acceptance",),
            "cwd_mapping": "workdir:.",
            "allowed_env_keys": ("PYTHONPATH",),
            "secret_refs": ("MODEL_API_KEY",),
            "test_config_ref": "config:default@1",
            "adapter_versions": dict(adapter_versions or {"command": "1.0.0"}),
            "resolved_input_digest": _DIGEST,
        },
        "snapshot": {"source_snapshot_id": _SNAPSHOT, "content_identity": _DIGEST},
        "environment": {
            "resolution": {
                "carrier_id": "carrier-1",
                "executable_path": "C:/venv/Scripts/python.exe",
                "executable_digest": _DIGEST,
                "interpreter_version": "3.13.13",
                "base_executable_path": "C:/Python313/python.exe",
                "base_executable_digest": _DIGEST,
                "probe_prefix": "python -I",
                "base_prefix": "python -I",
                "configuration_digest": _DIGEST,
                "dependency_roots": ("C:/venv/Lib/site-packages",),
                "dependency_set_digest": _DIGEST,
            },
            "step_timeout_seconds": 120,
        },
    }


def _admission(digest: str = _DIGEST, snapshot: str = _SNAPSHOT) -> dict[str, object]:
    return {"snapshot_id": snapshot, "source_binding_digest": digest, "workdir": "C:/work"}


def _resolver(
    *,
    admission: Mapping[str, object] | None = None,
    index: object = 1,
    side_effect: object = SideEffectClass.READ_ONLY,
) -> SavedActionResolver:
    return SavedActionResolver(
        attempt_index=lambda run, step: index,  # type: ignore[return-value]
        side_effect_class=lambda run, step: side_effect,  # type: ignore[return-value]
        admission=lambda run, step, **kwargs: admission if admission is not None else _admission(),
    )


def test_resolves_a_self_consistent_action() -> None:
    action = _resolver().resolve(
        run=_run(),
        step=_step(),
        intent_id="intent-1",
        prepared=_prepared(),
        step_content={"step_id": "step-1", "run_id": "run-1"},
    )
    assert isinstance(action, ResolvedExecutionAction)
    assert action.request.registered_entry.adapter_kind is AdapterKind.COMMAND
    assert action.request.registered_entry.arguments == ("tests/acceptance",)
    assert action.attempt.adapter_kind is AdapterKind.COMMAND
    assert action.attempt.adapter_version == "1.0.0"
    assert action.attempt.attempt_index == 1
    assert action.attempt.timeout_ms == 120_000 == action.request.timeout_ms
    assert action.request.source_binding_digest == _DIGEST
    assert action.request.resolved_input_digest == _DIGEST
    assert action.attempt.authorization_ref == action.request.authorization_ref
    assert action.request.authorization_ref.consumed_by_attempt_id is None
    assert action.environment_content_identity and action.source_content_identity


def test_step_entry_adapter_must_match_the_frozen_binding() -> None:
    with pytest.raises(ActionResolutionBlocked):
        _resolver().resolve(
            run=_run(),
            step=_step(AdapterKind.PYTHON_CHECKS),
            intent_id="intent-1",
            prepared=_prepared(),
            step_content={},
        )


def test_arguments_and_adapter_key_come_from_the_frozen_binding() -> None:
    with pytest.raises(ActionResolutionBlocked):  # 多键：无法唯一确定适配器
        _resolver().resolve(
            run=_run(),
            step=_step(),
            intent_id="intent-1",
            prepared=_prepared(adapter_versions={"command": "1.0.0", "http": "1.0.0"}),
            step_content={},
        )


@pytest.mark.parametrize(
    "prepared",
    [
        {},
        {"execution_source": {}},
        {"execution_source": _prepared()["execution_source"], "snapshot": {}},
        {
            "execution_source": _prepared()["execution_source"],
            "snapshot": {"source_snapshot_id": _SNAPSHOT, "content_identity": _DIGEST},
            "environment": {},
        },
    ],
)
def test_missing_frozen_sections_are_refused(prepared: dict[str, object]) -> None:
    with pytest.raises(ActionResolutionBlocked):
        _resolver().resolve(
            run=_run(), step=_step(), intent_id="intent-1", prepared=prepared, step_content={}
        )


def test_missing_start_admission_is_refused() -> None:
    resolver = SavedActionResolver(
        attempt_index=lambda run, step: 1,
        side_effect_class=lambda run, step: SideEffectClass.READ_ONLY,
    )
    with pytest.raises(ActionResolutionBlocked):
        resolver.resolve(
            run=_run(), step=_step(), intent_id="intent-1", prepared=_prepared(), step_content={}
        )


@pytest.mark.parametrize(
    "admission",
    [
        _admission(snapshot="other-snapshot"),
        {"snapshot_id": _SNAPSHOT, "source_binding_digest": "sha256:short"},
        {"snapshot_id": _SNAPSHOT},
    ],
)
def test_admission_must_match_snapshot_and_carry_exact_digest(
    admission: dict[str, object],
) -> None:
    with pytest.raises(ActionResolutionBlocked):
        _resolver(admission=admission).resolve(
            run=_run(), step=_step(), intent_id="intent-1", prepared=_prepared(), step_content={}
        )


def test_frozen_step_timeout_must_be_positive_seconds() -> None:
    prepared = _prepared()
    environment = dict(prepared["environment"])  # type: ignore[arg-type]
    environment["step_timeout_seconds"] = 0
    prepared["environment"] = environment
    with pytest.raises(ActionResolutionBlocked):
        _resolver().resolve(
            run=_run(), step=_step(), intent_id="intent-1", prepared=prepared, step_content={}
        )


def test_attempt_index_and_side_effect_class_must_be_domain_values() -> None:
    for resolver in (_resolver(index=0), _resolver(side_effect="read_only")):
        with pytest.raises(ActionResolutionBlocked):
            resolver.resolve(
                run=_run(),
                step=_step(),
                intent_id="intent-1",
                prepared=_prepared(),
                step_content={},
            )


def test_step_content_and_intent_must_match_the_frozen_scope() -> None:
    for content, intent in (
        ({"step_id": "other-step"}, "intent-1"),
        ({"run_id": "other-run"}, "intent-1"),
        ({}, ""),
    ):
        with pytest.raises(ActionResolutionBlocked):
            _resolver().resolve(
                run=_run(),
                step=_step(),
                intent_id=intent,
                prepared=_prepared(),
                step_content=content,
            )

def test_default_side_effect_class_is_unknown_not_read_only() -> None:
    from aitest.application.execution.action_resolver import default_side_effect_class

    assert default_side_effect_class(_run(), _step()) is SideEffectClass.UNKNOWN


class _Attempt:
    def __init__(self, step_id: str, attempt_id: str) -> None:
        self.step_id = step_id
        self.attempt_id = attempt_id


class _Facts:
    def __init__(self, attempts: object) -> None:
        self.attempts = attempts


def test_facts_attempt_index_counts_only_this_step() -> None:
    from aitest.application.execution.action_resolver import FactsAttemptIndex

    index = FactsAttemptIndex(
        lambda project, run: _Facts([_Attempt("step-1", "a1"), _Attempt("step-2", "a2")])
    )
    assert index(_run(), _step()) == 2


def test_facts_attempt_index_fails_closed() -> None:
    from aitest.application.execution.action_resolver import FactsAttemptIndex

    def boom(project: str, run: str) -> object:
        raise OSError("facts unavailable")

    for reader in (
        lambda project, run: _Facts(None),
        boom,
        lambda project, run: _Facts([_Attempt("step-1", "")]),
        lambda project, run: _Facts([_Attempt("step-1", "a"), _Attempt("step-1", "a")]),
    ):
        with pytest.raises(ActionResolutionBlocked):
            FactsAttemptIndex(reader)(_run(), _step())
