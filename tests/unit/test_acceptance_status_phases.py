"""验收登记对账（二期/三期）：`tests/acceptance/p{2,3}/status.json` 与需求文档第 5 节逐字一致。

本测试只检查“登记是否可信”，不产生也不替代任何验收结论。

- 权威来源：`docs/项目文档/二期/需求文档/01-需求文档.md`、
  `docs/项目文档/三期/需求文档/01-需求文档.md` 第 5 节（各期验收场景的唯一维护点）。
- 一期登记由 `tests/unit/test_acceptance_status.py` 对账，其牵头包归属来自
  《一期工程四部分拆分与低对接实施方案》第 5 节。
- 二期/三期**尚无牵头包拆分方案**，因此 `owner` 允许为 `null`；一旦填写必须是有效包名，
  不允许用默认值或空串掩盖“还没定归属”。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# (期次, 编号前缀, 期望条目数)
_PHASES = ((2, "P2", 9), (3, "P3", 8))

_RESULTS = frozenset({"untested", "verified", "not_verified", "blocked"})
_BLOCKER_KINDS = frozenset({
    "package_port",
    "real_storage",
    "execution_facts",
    "user_entry",
    "decision",
    "environment",
    "protocol",
    "platform_contract",
})
_REQUIRED_UPDATE_RULES = frozenset({
    "owner_field",
    "scenario_expected",
    "verified_rule",
    "no_inference",
})
_PHASE_DIRECTORY = {2: "二期", 3: "三期"}


def _requirements_path(phase: int) -> Path:
    return (
        _REPO_ROOT
        / "docs"
        / "项目文档"
        / _PHASE_DIRECTORY[phase]
        / "需求文档"
        / "01-需求文档.md"
    )


def _status_path(phase: int) -> Path:
    return _REPO_ROOT / "tests" / "acceptance" / f"p{phase}" / "status.json"


def _load_status(phase: int) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_status_path(phase).read_text(encoding="utf-8")))


def _load_requirements_scenarios(phase: int, prefix: str) -> dict[str, tuple[str, str, list[str]]]:
    """从需求文档第 5 节解析验收场景表，返回 {AC: (场景, 预期, 关联功能)}。"""

    pattern = re.compile(rf"^\|\s*({prefix}-AC\d{{2}})\s*\|(.*)$")
    scenarios: dict[str, tuple[str, str, list[str]]] = {}
    for line in _requirements_path(phase).read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match is None:
            continue
        cells = [cell.strip() for cell in match.group(2).split("|")]
        while cells and cells[-1] == "":
            cells.pop()
        if len(cells) < 3:
            continue
        scenario, expected, functions = cells[0], cells[1], cells[2]
        scenarios.setdefault(
            match.group(1),
            (scenario, expected, [item.strip() for item in functions.split("、") if item.strip()]),
        )
    return scenarios


def _results(phase: int) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _load_status(phase)["results"])


@pytest.mark.parametrize("phase,prefix,expected_count", _PHASES)
def test_status_lists_every_scenario_in_requirements_order(
    phase: int, prefix: str, expected_count: int
) -> None:
    authoritative = _load_requirements_scenarios(phase, prefix)
    assert len(authoritative) == expected_count, (
        f"{prefix} 需求文档第 5 节场景数不是 {expected_count}"
    )

    ids = [row["id"] for row in _results(phase)]
    assert ids == list(authoritative), f"{prefix} 登记条目与需求文档编号顺序不一致"


@pytest.mark.parametrize("phase,prefix,expected_count", _PHASES)
def test_scenario_and_expected_match_the_requirements_verbatim(
    phase: int, prefix: str, expected_count: int
) -> None:
    del expected_count
    authoritative = _load_requirements_scenarios(phase, prefix)
    for row in _results(phase):
        scenario, expected, functions = authoritative[row["id"]]
        assert row["scenario"] == scenario, f"{row['id']} 场景与需求文档不一致"
        assert row["expected"] == expected, f"{row['id']} 预期与需求文档不一致"
        assert row["related_frs"] == functions, f"{row['id']} 关联功能与需求文档不一致"


@pytest.mark.parametrize("phase,prefix,expected_count", _PHASES)
def test_phase_field_and_directory_agree(phase: int, prefix: str, expected_count: int) -> None:
    del prefix, expected_count
    document = _load_status(phase)
    assert document["phase"] == phase
    assert document["scope"] == "product_acceptance_not_scaffold_tests"
    assert document["baseline_commit"].strip()


@pytest.mark.parametrize("phase,prefix,expected_count", _PHASES)
def test_update_rules_state_the_boundaries(phase: int, prefix: str, expected_count: int) -> None:
    del prefix, expected_count
    rules = _load_status(phase)["update_rules"]
    assert set(rules) >= _REQUIRED_UPDATE_RULES
    assert rules["verified_rule"]


@pytest.mark.parametrize("phase,prefix,expected_count", _PHASES)
def test_owner_is_null_or_a_real_package(phase: int, prefix: str, expected_count: int) -> None:
    """二期/三期牵头包拆分方案未建立：允许 null，但不允许空串或占位值冒充已归属。"""

    del prefix, expected_count
    for row in _results(phase):
        owner = row["owner"]
        assert owner is None or (isinstance(owner, str) and owner.strip()), (
            f"{row['id']} 的 owner 既不是 null 也不是有效包名：{owner!r}"
        )


@pytest.mark.parametrize("phase,prefix,expected_count", _PHASES)
def test_result_values_are_within_the_allowed_enumeration(
    phase: int, prefix: str, expected_count: int
) -> None:
    del prefix, expected_count
    for row in _results(phase):
        assert row["result"] in _RESULTS, f"{row['id']} result 取值非法：{row['result']}"


@pytest.mark.parametrize("phase,prefix,expected_count", _PHASES)
def test_verified_requires_evidence_and_real_environment(
    phase: int, prefix: str, expected_count: int
) -> None:
    del prefix, expected_count
    for row in _results(phase):
        if row["result"] != "verified":
            continue
        assert row["evidence"]["path"], f"{row['id']} 标为 verified 却没有证据位置"
        assert row["real_environment_verified"] is True, (
            f"{row['id']} 标为 verified 却不是真实环境验证；"
            "平台合同未定时不得用桩件结果代填"
        )


@pytest.mark.parametrize("phase,prefix,expected_count", _PHASES)
def test_untested_rows_carry_no_actual_result(phase: int, prefix: str, expected_count: int) -> None:
    del prefix, expected_count
    for row in _results(phase):
        if row["result"] == "untested":
            assert row["actual"] is None, f"{row['id']} 未测试却登记了实际结果"


@pytest.mark.parametrize("phase,prefix,expected_count", _PHASES)
def test_every_row_states_why_it_is_not_verified(
    phase: int, prefix: str, expected_count: int
) -> None:
    """登记必须能回答“为什么还不是 verified”，不允许留空让人误读为已具备。"""

    del prefix, expected_count
    for row in _results(phase):
        if row["result"] == "verified":
            continue
        assert row["unverified_reason"] or row["actual"], (
            f"{row['id']} 既没有实际结果也没有未验证原因"
        )


@pytest.mark.parametrize("phase,prefix,expected_count", _PHASES)
def test_blocker_kinds_are_from_the_enumeration(
    phase: int, prefix: str, expected_count: int
) -> None:
    del prefix, expected_count
    for row in _results(phase):
        blockers = row["blockers"]
        if blockers is None:
            continue
        assert blockers, f"{row['id']} 的 blockers 为空列表，应写 null 或非空清单"
        for blocker in blockers:
            assert blocker["kind"] in _BLOCKER_KINDS, (
                f"{row['id']} 的阻塞类型非法：{blocker['kind']}"
            )
            assert blocker["detail"].strip(), f"{row['id']} 的阻塞项缺少说明"
            assert blocker["resolved_by"].strip(), f"{row['id']} 的阻塞项未写由谁解决"


@pytest.mark.parametrize("phase,prefix,expected_count", _PHASES)
def test_mirrored_cases_match_results(phase: int, prefix: str, expected_count: int) -> None:
    del prefix, expected_count
    document = _load_status(phase)
    results = document["results"]
    cases = document["cases"]
    assert [case["id"] for case in cases] == [row["id"] for row in results]
    assert [case["result"] for case in cases] == [row["result"] for row in results]
    assert all(case["evidence_path"] is None for case in cases)
