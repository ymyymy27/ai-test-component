"""`PreparedRun` 功能夹具：由真实链路生成、可复现、覆盖 Sprint 1 上下文对象。

用途是实施方案第 4 节的**交接一**："先以 B 的真实 `PreparedRun` 替换 C 的夹具，
核对准备/启动间来源变化"。

与 Sprint 0 的合同级夹具（`fixtures/prepared_run/`）的区别：

| | 合同级夹具（Sprint 0） | 功能夹具（本次） |
| --- | --- | --- |
| 来源 | **手写** JSON | 由 `build_scenario()` **真实编排**产出 |
| 覆盖 | 合同字段形状 | 合同字段 + Sprint 1 的项目/绑定/模块/环境/依赖图 |
| 漂移 | 手改才变，可能与代码脱节 | 有"重新生成并逐字节比对"的测试锁定 |

**这些夹具不是用手改的**：改代码后重新生成即可（见 `test_fixtures_can_be_regenerated`）。
"""

import json
from pathlib import Path

import pytest

from aitest.contracts.prepared_run import (
    BindingFormFact,
    PreparedRun,
    PreparedRunStatusFact,
)
from tests.support.prepared_run_factory import SCENARIOS, build_scenario, fixture_payload

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "prepared_run_functional"

#: 四个场景的期望形态；改这里就必须重新生成夹具。
EXPECTED = {
    "git": (PreparedRunStatusFact.PREPARED, BindingFormFact.GIT),
    "plain": (PreparedRunStatusFact.PREPARED, BindingFormFact.PLAIN),
    "blocked": (PreparedRunStatusFact.BLOCKED, BindingFormFact.GIT),
    "needs_reprepare": (PreparedRunStatusFact.BLOCKED, BindingFormFact.GIT),
}


def _load(name: str) -> PreparedRun:
    path = FIXTURE_DIR / f"{name}.json"
    return PreparedRun.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_fixture_parses_against_the_frozen_contract(name: str) -> None:
    prepared = _load(name)
    assert prepared.schema_version == "aitest.prepared-run/1.0"


@pytest.mark.parametrize("name", SCENARIOS)
def test_fixture_carries_the_expected_status_and_binding_form(name: str) -> None:
    expected_status, expected_form = EXPECTED[name]
    prepared = _load(name)
    assert prepared.status is expected_status
    assert prepared.binding_form is expected_form


@pytest.mark.parametrize("name", SCENARIOS)
def test_fixtures_can_be_regenerated_byte_for_byte(name: str) -> None:
    """夹具由代码生成：改代码后重新生成即可，**不要手改夹具**。

    这条测试是"夹具与代码不漂移"的守卫。失败时说明代码行为变了，
    应重新生成并**在 PR 里说明为什么变了**。
    """
    path = FIXTURE_DIR / f"{name}.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert fixture_payload(name) == on_disk


def test_git_fixture_carries_the_repository_identity() -> None:
    prepared = _load("git")
    assert prepared.git_base_commit is not None
    assert prepared.git_diff_digest is not None
    assert prepared.plain_manifest_digest is None
    assert prepared.refetch_dependencies == ("git-lfs:assets/*",)


def test_plain_fixture_omits_every_git_field() -> None:
    """需求 P1-AC25：`plain` 项目不出现仓库/分支/提交内容，**也不是空值占位**。"""
    raw = json.loads((FIXTURE_DIR / "plain.json").read_text(encoding="utf-8"))
    for key in ("git_base_commit", "git_diff_digest"):
        assert key not in raw, f"plain fixture must omit {key}"
    assert raw["plain_manifest_digest"]
    prepared = _load("plain")
    assert prepared.binding_form is BindingFormFact.PLAIN


def test_fixtures_cover_the_sprint_one_project_context_objects() -> None:
    """这是本夹具相对 Sprint 0 合同级夹具的**核心增益**：覆盖 Sprint 1 的对象。"""
    prepared = _load("git")
    # 项目身份与绑定
    assert prepared.project_id
    assert prepared.workspace_id
    assert prepared.binding_id
    assert prepared.binding_revision >= 1
    # 环境（解析事实，不是声明）
    assert prepared.environment.isolation_mode.value == "venv"
    assert prepared.environment.interpreter_identity
    assert prepared.environment.dependency_set_digest
    # 执行来源绑定
    assert prepared.execution_source.registered_entry
    assert prepared.execution_source.resolved_input_digest
    # 计划 / 范围 / 规则 / 模板 / 用例修订引用
    assert prepared.plan_revision.revision_no >= 1
    assert prepared.acceptance_scope_revision >= 1
    assert prepared.rule_versions
    assert prepared.template_versions
    assert len(prepared.case_revisions) == 2
    # 冻结用例与其关联（模块/环境/关键链路）
    assert len(prepared.frozen_cases) == 2
    links = prepared.frozen_cases[0].links
    assert set(links.module_ids) == {"module-ticket", "module-store"}
    assert links.environment_ids == ("env-local",)
    assert links.critical_path_ids == ("path-ticket-write-read",)


def test_git_fixture_carries_a_confirmed_and_an_unconfirmed_basis() -> None:
    """断言依据三态在夹具里真实可见：一条 `confirmed` 带确认引用，一条未确认。"""
    prepared = _load("git")
    by_case = {entry.case_id: entry for entry in prepared.assertion_bases}
    assert by_case["case-change-status"].assertion_basis_state.value == "confirmed"
    assert by_case["case-change-status"].confirmation_refs
    assert (
        by_case["case-create-ticket"].assertion_basis_state.value
        == "present_unconfirmed"
    )
    assert by_case["case-create-ticket"].confirmation_refs == ()


def test_blocked_fixture_names_its_blocking_reason() -> None:
    """`blocked` 必须带原因，且**不是**用抛异常表达（需求 P1-AC17）。"""
    prepared = _load("blocked")
    assert prepared.blocking_reasons
    assert "dependency carrier" in prepared.blocking_reasons[0].message
    assert prepared.status is PreparedRunStatusFact.BLOCKED


def test_reprepare_fixture_names_the_changed_source() -> None:
    """来源变化 → 阻塞并列出**变化的来源**，且**不把新字节塞进旧意图**。"""
    prepared = _load("needs_reprepare")
    assert prepared.status is PreparedRunStatusFact.BLOCKED
    assert any(reason.code == "needs_reprepare" for reason in prepared.blocking_reasons)
    assert any(
        rule.source_kind == "snapshot_revision" for rule in prepared.invalidation_rules
    )


def test_reprepare_fixture_keeps_the_original_preparation_record() -> None:
    """重放不新建准备记录：提交序号停在登记时那一次。"""
    scenario = build_scenario("needs_reprepare")
    recorded = scenario.reader.find_preparation(
        project_id="project-ticket",
        client_id="client-trae",
        prepare_request_id="prepare-request-1",
    )
    assert recorded is not None
    assert recorded.request.input_revisions.snapshot_revision == 1
    assert scenario.unit_of_work.commit_seq() == "commit-4"


def test_prepared_fixtures_reference_the_published_plan() -> None:
    prepared = _load("git")
    assert prepared.plan_revision.revision_id == "plan-ticket"
    assert prepared.conclusion_ceiling.value == "passable"
    assert prepared.run_tier.value == "full"


def test_fixture_directory_holds_exactly_the_known_scenarios() -> None:
    on_disk = {path.stem for path in FIXTURE_DIR.glob("*.json")}
    assert on_disk == set(SCENARIOS)


def test_fixture_values_are_deterministic_across_runs() -> None:
    """夹具不得依赖系统时间或随机数：两次生成必须完全一致。"""
    assert fixture_payload("git") == fixture_payload("git")
    assert fixture_payload("plain") == fixture_payload("plain")
