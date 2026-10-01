"""`prepare_run` 编排：四态分支与四条硬性约束。

依据：`docs/文档-feix-a/B包/11-薄底座与prepare_run编排设计说明.md` 第 4 节；
需求 P1-AC17（上下文缺失列缺口并阻塞）、P1-FR07（同意图去重与明确重跑）。
"""

import pytest

from aitest.application.planning.preparation import (
    PAYLOAD_FIELDS,
    InputRevisions,
    preparation_identity_digest,
    preparation_intent_id,
)
from aitest.application.planning.prepare_run import (
    PreparationInputs,
    preparation_payload,
    prepare_run,
)
from aitest.application.planning.substrate import PreparationConflictError
from aitest.contracts.prepared_run import (
    AssertionBasisEntry,
    AssertionBasisStateFact,
    BindingFormFact,
    CaseLinks,
    CaseRevisionRef,
    EnvironmentIsolationModeFact,
    EnvironmentRefFact,
    ExecutionSourceBinding,
    FrozenCase,
    FrozenCaseStep,
    GapEntry,
    PlanRevisionRef,
    PreparedRun,
    PreparedRunStatusFact,
    RuleVersionRef,
    RunDriverFact,
    RunTierFact,
    SkippedScopeEntry,
    SnapshotRef,
    TemplateVersionRef,
)
from tests.support.memory_substrate import (
    FixedClock,
    MemoryReader,
    MemoryStore,
    MemoryUnitOfWork,
)


def _revisions(**overrides: int) -> InputRevisions:
    base = {
        "project_revision": 1,
        "binding_revision": 1,
        "snapshot_revision": 1,
        "environment_revision": 1,
        "plan_revision": 1,
        "rules_revision": 1,
        "template_revision": 1,
        "scope_revision": 1,
    }
    base.update(overrides)
    return InputRevisions(**base)


def _inputs(**overrides: object) -> PreparationInputs:
    """构造一份**合同合法**的 full 档输入；逐项改坏用于分支测试。"""
    base: dict[str, object] = {
        "project_id": "p1",
        "workspace_id": "ws-1",
        "binding_id": "binding-1",
        "binding_revision": 1,
        "binding_form": BindingFormFact.GIT,
        "client_id": "c1",
        "prepare_request_id": "req-1",
        "input_revisions": _revisions(),
        "snapshot": SnapshotRef(
            source_snapshot_id="snap-1",
            purpose="prepare",
            content_identity="sha256:content-1",
        ),
        "selected_paths": ("src/ticket",),
        "environment": EnvironmentRefFact(
            environment_id="env-1",
            revision=1,
            isolation_mode=EnvironmentIsolationModeFact.VENV,
            interpreter_identity="cpython-3.13.3-windows-amd64",
            dependency_set_digest="sha256:deps-1",
        ),
        "execution_source": ExecutionSourceBinding(
            registered_entry="python -m pytest",
            entry_arguments=("tests/acceptance",),
            cwd_mapping="<project-root> -> workdirs/run-1",
            test_config_ref="pyproject.toml#tool.pytest",
            resolved_input_digest="sha256:resolved-1",
        ),
        "plan_revision": PlanRevisionRef(
            revision_id="plan-1", revision_no=1, digest="sha256:plan-1"
        ),
        "acceptance_scope_revision": 1,
        "rule_versions": (
            RuleVersionRef(rule_id="rule-1", revision=1, digest="sha256:rule-1"),
        ),
        "template_versions": (
            TemplateVersionRef(
                template_id="ticket-workflow", version="1.0.0", digest="sha256:tpl-1"
            ),
        ),
        "case_revisions": (
            CaseRevisionRef(case_id="case-1", revision=1, digest="sha256:case-1"),
        ),
        "frozen_cases": (
            FrozenCase(
                case_id="case-1",
                revision=1,
                layer="L2",
                required=True,
                independent_verification="read-only query against the same ticket ID",
                importance="P0",
                steps=(
                    FrozenCaseStep(
                        step_id="step-1",
                        layer="L2",
                        objective="create a ticket",
                        expected="the ticket is persisted",
                    ),
                ),
                links=CaseLinks(
                    acceptance_item_ids=("ai-1",),
                    module_ids=("module-1",),
                    environment_ids=("env-1",),
                    critical_path_ids=("path-1",),
                ),
            ),
        ),
        "assertion_bases": (
            AssertionBasisEntry(
                case_id="case-1",
                basis_revision=1,
                basis_text_digest="sha256:basis-1",
                assertion_basis_state=AssertionBasisStateFact.PRESENT_UNCONFIRMED,
            ),
        ),
        "git_base_commit": "9c44344bcd612df1a7d033efa1e7a47c810c49cf",
        "git_diff_digest": "sha256:diff-1",
        "run_tier": RunTierFact.FULL,
        "initial_driver": RunDriverFact.PLANNED,
        "template_required_case_ids": ("case-1",),
        "frozen_required_case_ids": ("case-1",),
        "selected_case_ids": ("case-1",),
    }
    base.update(overrides)
    return PreparationInputs(**base)  # type: ignore[arg-type]


def _world() -> tuple[MemoryUnitOfWork, MemoryReader, FixedClock]:
    store = MemoryStore()
    return MemoryUnitOfWork(store), MemoryReader(store), FixedClock()


def _store_existing(
    unit_of_work: MemoryUnitOfWork, prepared: PreparedRun
) -> None:
    """把一份 `PreparedRun` 当作已存记录写入底座，供幂等分支复读。"""
    unit_of_work.open(prepared.project_id)
    unit_of_work.stage_record(
        aggregate_kind="prepared_run",
        record_id=prepared.prepared_run_id,
        expected_revision=None,
        payload=prepared.model_dump(mode="json"),
    )
    unit_of_work.commit()


# ------------------------------------------------------------- 新建分支


def test_first_prepare_creates_a_prepared_run() -> None:
    unit_of_work, reader, clock = _world()
    result = prepare_run(
        _inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )

    assert result.status is PreparedRunStatusFact.PREPARED
    assert result.intent_id == preparation_intent_id(
        project_id="p1", client_id="c1", prepare_request_id="req-1"
    )
    assert result.intent_id != result.prepare_request_id
    assert result.created_at_commit == "commit-1"
    assert result.blocking_reasons == ()
    assert result.conclusion_ceiling.value == "passable"


def test_prepare_registers_the_intent_in_the_same_commit() -> None:
    unit_of_work, reader, clock = _world()
    result = prepare_run(
        _inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )

    stored = reader.find_preparation(
        project_id="p1", client_id="c1", prepare_request_id="req-1"
    )
    assert stored is not None
    assert stored.intent_id == result.intent_id
    assert stored.created_at_commit == result.created_at_commit


def test_intent_id_is_not_the_transport_request_id() -> None:
    unit_of_work, reader, clock = _world()
    result = prepare_run(
        _inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )
    assert result.intent_id != result.prepare_request_id


def test_created_at_commit_comes_from_the_commit_sequence() -> None:
    unit_of_work, reader, clock = _world()
    # 先产生一个提交，让序号不是 1。
    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    unit_of_work.commit()

    result = prepare_run(
        _inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )
    assert result.created_at_commit == "commit-2"


def test_created_at_uses_the_clock_not_the_system_time() -> None:
    unit_of_work, reader, clock = _world()
    result = prepare_run(
        _inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )
    assert result.created_at == clock.now()


# ------------------------------------------------------------- 幂等分支


def test_second_prepare_with_the_same_input_reuses_the_intent() -> None:
    unit_of_work, reader, clock = _world()
    first = prepare_run(
        _inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )

    second = prepare_run(
        _inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )
    assert second.intent_id == first.intent_id
    assert second.payload_hash == first.payload_hash


def test_replay_returns_the_stored_run_and_does_not_commit_again() -> None:
    unit_of_work, reader, clock = _world()
    first = prepare_run(
        _inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )
    _store_existing(unit_of_work, first)
    after_store = unit_of_work.commit_seq()

    replayed = prepare_run(
        _inputs(),
        unit_of_work=unit_of_work,
        reader=reader,
        clock=clock,
        existing=first,
    )
    assert replayed is first
    assert unit_of_work.commit_seq() == after_store


# ------------------------------------------------------------- 冲突分支


def test_same_request_with_different_input_conflicts() -> None:
    unit_of_work, reader, clock = _world()
    prepare_run(_inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock)

    with pytest.raises(PreparationConflictError) as error:
        prepare_run(
            _inputs(selected_paths=("src/other",)),
            unit_of_work=unit_of_work,
            reader=reader,
            clock=clock,
        )
    assert error.value.existing_intent_id == preparation_intent_id(
        project_id="p1", client_id="c1", prepare_request_id="req-1"
    )


def test_conflict_writes_nothing() -> None:
    unit_of_work, reader, clock = _world()
    prepare_run(_inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock)
    after_first = unit_of_work.commit_seq()

    with pytest.raises(PreparationConflictError):
        prepare_run(
            _inputs(selected_paths=("src/other",)),
            unit_of_work=unit_of_work,
            reader=reader,
            clock=clock,
        )
    assert unit_of_work.commit_seq() == after_first


# ------------------------------------------------------------- 需重新准备


def test_changed_source_revision_blocks_and_requires_reprepare() -> None:
    unit_of_work, reader, clock = _world()
    prepare_run(_inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock)

    # 摘要相同（业务输入未变）但来源修订前进 → 不能复用。
    result = prepare_run(
        _inputs(input_revisions=_revisions(snapshot_revision=9)),
        unit_of_work=unit_of_work,
        reader=reader,
        clock=clock,
    )
    assert result.status is PreparedRunStatusFact.BLOCKED
    assert any(
        reason.code == "needs_reprepare" for reason in result.blocking_reasons
    )
    assert any(
        rule.source_kind == "snapshot_revision" for rule in result.invalidation_rules
    )


def test_reprepare_does_not_write_new_bytes_into_the_old_intent() -> None:
    """关键约束：来源变化时**不调用** stage_preparation，提交序号不前进。"""
    unit_of_work, reader, clock = _world()
    prepare_run(_inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock)
    after_first = unit_of_work.commit_seq()

    prepare_run(
        _inputs(input_revisions=_revisions(plan_revision=4)),
        unit_of_work=unit_of_work,
        reader=reader,
        clock=clock,
    )
    assert unit_of_work.commit_seq() == after_first

    stored = reader.find_preparation(
        project_id="p1", client_id="c1", prepare_request_id="req-1"
    )
    assert stored is not None
    assert stored.request.input_revisions.plan_revision == 1


def test_reprepare_keeps_the_original_intent() -> None:
    unit_of_work, reader, clock = _world()
    first = prepare_run(
        _inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )
    blocked = prepare_run(
        _inputs(input_revisions=_revisions(environment_revision=7)),
        unit_of_work=unit_of_work,
        reader=reader,
        clock=clock,
    )
    assert blocked.intent_id == first.intent_id


# ------------------------------------------------------------- 缺口阻塞


def test_context_gaps_block_without_raising() -> None:
    """P1-AC17：上下文缺失时列缺口并阻塞，**不编造依赖与结论**。"""
    unit_of_work, reader, clock = _world()
    result = prepare_run(
        _inputs(
            context_gaps=(
                GapEntry(
                    gap_id="gap-1",
                    kind="missing_dependency_registration",
                    subject="module-1",
                    blocking=True,
                    detail="module dependencies are not registered",
                ),
            )
        ),
        unit_of_work=unit_of_work,
        reader=reader,
        clock=clock,
    )
    assert result.status is PreparedRunStatusFact.BLOCKED
    assert result.blocking_reasons[0].code == "missing_dependency_registration"
    assert result.blocking_reasons[0].message == "module dependencies are not registered"


def test_context_gaps_do_not_register_a_preparation_record() -> None:
    unit_of_work, reader, clock = _world()
    prepare_run(
        _inputs(
            context_gaps=(
                GapEntry(
                    gap_id="gap-1",
                    kind="missing_environment",
                    subject="env-1",
                    blocking=True,
                    detail="no environment carrier",
                ),
            )
        ),
        unit_of_work=unit_of_work,
        reader=reader,
        clock=clock,
    )
    assert (
        reader.find_preparation(
            project_id="p1", client_id="c1", prepare_request_id="req-1"
        )
        is None
    )
    assert unit_of_work.commit_seq() == "commit-0"


# ------------------------------------------------------------- 输入摘要


def test_payload_contains_only_business_inputs() -> None:
    payload = preparation_payload(_inputs())
    for name in ("request_id", "retry_count", "received_at"):
        assert name not in payload
    assert payload["run_tier"] == "full"
    assert payload["case_revision_ids"] == ["case-1"]


def test_payload_hash_is_stable_across_repeated_calls() -> None:
    unit_of_work, reader, clock = _world()
    first = prepare_run(
        _inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )
    second = prepare_run(
        _inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )
    assert first.payload_hash == second.payload_hash


def test_transport_only_difference_does_not_change_the_hash() -> None:
    """同号重传不得被判成"输入不同"。"""
    assert preparation_payload(_inputs()) == preparation_payload(_inputs())


def test_observed_source_revision_does_not_change_the_hash() -> None:
    """来源修订不进摘要，否则会被误报成"同键异输入冲突"而不是"需重新准备"。"""
    assert preparation_payload(_inputs()) == preparation_payload(
        _inputs(input_revisions=_revisions(snapshot_revision=9))
    )


def test_request_side_change_does_change_the_hash() -> None:
    assert preparation_payload(_inputs()) != preparation_payload(
        _inputs(selected_paths=("src/other",))
    )


def test_quick_tier_yields_a_partial_ceiling() -> None:
    unit_of_work, reader, clock = _world()
    result = prepare_run(
        _inputs(
            run_tier=RunTierFact.QUICK,
            template_required_case_ids=(),
            frozen_required_case_ids=(),
            selected_case_ids=("case-1",),
            assertion_bases=(),
        ),
        unit_of_work=unit_of_work,
        reader=reader,
        clock=clock,
    )
    assert result.conclusion_ceiling.value == "partial"


# ------------------------------------------------------------- 输入校验


def test_prepare_requires_identity_fields() -> None:
    unit_of_work, reader, clock = _world()
    for field_name in (
        "project_id",
        "workspace_id",
        "binding_id",
        "client_id",
        "prepare_request_id",
    ):
        with pytest.raises(ValueError, match=field_name):
            prepare_run(
                _inputs(**{field_name: "  "}),
                unit_of_work=unit_of_work,
                reader=reader,
                clock=clock,
            )


# ------------------------------------------------- 摘要口径（B-PREPARE-01/02）


def test_payload_carries_exactly_the_declared_fields() -> None:
    """摘要键集合与 `PAYLOAD_FIELDS` **逐字一致**，防止两处再次分叉。"""
    assert set(preparation_payload(_inputs())) == set(PAYLOAD_FIELDS)


def test_changing_the_selection_changes_the_digest() -> None:
    """B-PREPARE-01：改了本轮选定用例，摘要必须变。

    修前 `selected_case_ids` 不在摘要里，换选择会得到同一个 digest，
    于是同键重传被误判成"幂等复用"，拿回上一次的准备结果。
    """
    base = preparation_payload(_inputs())
    narrowed = preparation_payload(_inputs(selected_case_ids=()))
    assert base != narrowed


def test_skipping_a_case_changes_the_digest() -> None:
    base = preparation_payload(_inputs())
    skipped = preparation_payload(
        _inputs(skipped_scope=(SkippedScopeEntry(case_id="case-1", reason="not ready"),))
    )
    assert base != skipped


def test_observed_source_identity_is_not_in_the_digest() -> None:
    """B-PREPARE-02：来源字节身份是**观察结果**，不进摘要。

    进了摘要的话，源码一变会先撞 `PreparationConflictError`（同键异输入冲突），
    而正确结论是"依据需重新准备"。来源漂移由 `InputRevisions` 单独比对。
    """
    payload = preparation_payload(_inputs())
    assert "snapshot_content_identity" not in payload
    other = _inputs(
        snapshot=SnapshotRef(
            source_snapshot_id="snap-2",
            purpose="prepare",
            content_identity="sha256:content-CHANGED",
        )
    )
    assert preparation_payload(other) == payload


def test_published_plan_revision_is_not_in_the_digest() -> None:
    """计划重发布属于"依据变化"，同理不得进摘要。"""
    payload = preparation_payload(_inputs())
    republished = _inputs(
        plan_revision=PlanRevisionRef(
            revision_id="plan-2", revision_no=2, digest="sha256:plan-2"
        )
    )
    assert preparation_payload(republished) == payload


def test_source_drift_needs_reprepare_not_a_conflict() -> None:
    """端到端：来源修订变了 → `needs_reprepare`，不是冲突。"""
    unit_of_work, reader, clock = _world()
    first = prepare_run(_inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock)
    assert first.status is PreparedRunStatusFact.PREPARED

    drifted = prepare_run(
        _inputs(
            input_revisions=_revisions(snapshot_revision=2),
            snapshot=SnapshotRef(
                source_snapshot_id="snap-2",
                purpose="prepare",
                content_identity="sha256:content-2",
            ),
        ),
        unit_of_work=unit_of_work,
        reader=reader,
        clock=clock,
    )
    assert drifted.status is PreparedRunStatusFact.BLOCKED
    assert any(
        reason.code == "needs_reprepare" for reason in drifted.blocking_reasons
    )


# ------------------------------------------------- 身份命名空间（B-02）


def test_intent_and_prepared_ids_are_namespaced() -> None:
    """同一 `prepare_request_id` 在不同项目/客户端下必须是**不同对象**。"""
    intents: set[str] = set()
    prepared_runs: set[str] = set()
    for project_id, client_id in (("p1", "c1"), ("p2", "c1"), ("p1", "c2")):
        store = MemoryStore()
        result = prepare_run(
            _inputs(project_id=project_id, client_id=client_id),
            unit_of_work=MemoryUnitOfWork(store),
            reader=MemoryReader(store),
            clock=FixedClock(),
        )
        digest = preparation_identity_digest(
            project_id=project_id,
            client_id=client_id,
            prepare_request_id="req-1",
        )
        assert digest in result.intent_id
        assert digest in result.prepared_run_id
        intents.add(result.intent_id)
        prepared_runs.add(result.prepared_run_id)
    assert len(intents) == 3
    assert len(prepared_runs) == 3


def test_identical_requests_in_different_projects_do_not_collide() -> None:
    """存储层的修订计数按 `record_id` 全局计，身份不带命名空间就会互相覆盖。"""
    store = MemoryStore()
    reader = MemoryReader(store)
    unit_of_work = MemoryUnitOfWork(store)
    for project_id in ("p1", "p2"):
        result = prepare_run(
            _inputs(project_id=project_id),
            unit_of_work=unit_of_work,
            reader=reader,
            clock=FixedClock(),
        )
        assert result.status is PreparedRunStatusFact.PREPARED
    for project_id in ("p1", "p2"):
        assert (
            reader.find_preparation(
                project_id=project_id, client_id="c1", prepare_request_id="req-1"
            )
            is not None
        )
