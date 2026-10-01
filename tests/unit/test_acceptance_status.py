"""验收登记对账：`tests/acceptance/p1/status.json` 必须与需求文档第 5 节逐字一致。

本测试只检查"登记是否可信"，不产生也不替代任何验收结论。
权威来源：`docs/项目文档/一期/需求文档/01-需求文档.md` 第 5 节（验收场景唯一维护点）。
设计与字段含义见 `docs/文档-feix-a/B包/15-验收框架设计说明.md`。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REQUIREMENTS = _REPO_ROOT / "docs" / "项目文档" / "一期" / "需求文档" / "01-需求文档.md"
_STATUS = _REPO_ROOT / "tests" / "acceptance" / "p1" / "status.json"

_AC_IDS = tuple(f"P1-AC{index:02d}" for index in range(1, 36))

# 《一期工程四部分拆分与低对接实施方案》第 5 节的验收牵头包归属。
_EXPECTED_OWNER = {
    "A": ("P1-AC28",),
    "B": ("P1-AC01", "P1-AC03", "P1-AC12", "P1-AC17",
          "P1-AC20", "P1-AC30", "P1-AC31", "P1-AC32"),
    "C": ("P1-AC04", "P1-AC05", "P1-AC06", "P1-AC07", "P1-AC08", "P1-AC09",
          "P1-AC13", "P1-AC19", "P1-AC24", "P1-AC25"),
    "D": ("P1-AC02", "P1-AC10", "P1-AC11", "P1-AC14", "P1-AC15", "P1-AC16",
          "P1-AC18", "P1-AC21", "P1-AC22", "P1-AC23", "P1-AC26", "P1-AC27",
          "P1-AC29", "P1-AC33", "P1-AC34", "P1-AC35"),
}

_RESULTS = frozenset({"untested", "verified", "not_verified", "blocked"})
_OWNERS = frozenset({"A", "B", "C", "D"})
_BLOCKER_KINDS = frozenset({
    "package_port",
    "real_storage",
    "execution_facts",
    "user_entry",
    "decision",
    "environment",
    "protocol",
})

# B 牵头的 8 项必须先完成前置条件分析，不允许留空。
_B_OWNED = _EXPECTED_OWNER["B"]


def _load_status() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_STATUS.read_text(encoding="utf-8")))


def _load_requirements_scenarios() -> dict[str, tuple[str, str, list[str]]]:
    """从需求文档第 5 节解析验收场景表，返回 {AC: (场景, 预期, 关联功能)}。"""

    scenarios: dict[str, tuple[str, str, list[str]]] = {}
    for line in _REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*(P1-AC\d{2})\s*\|(.*)$", line)
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


def _results() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _load_status()["results"])


def test_status_json_lists_all_thirty_five_scenarios_in_order() -> None:
    ids = [row["id"] for row in _results()]
    assert ids == list(_AC_IDS)


def test_scenario_and_expected_match_the_requirements_verbatim() -> None:
    authoritative = _load_requirements_scenarios()
    assert set(authoritative) == set(_AC_IDS)

    for row in _results():
        scenario, expected, functions = authoritative[row["id"]]
        assert row["scenario"] == scenario, f"{row['id']} 场景与需求文档不一致"
        assert row["expected"] == expected, f"{row['id']} 预期与需求文档不一致"
        assert row["related_frs"] == functions, f"{row['id']} 关联功能与需求文档不一致"


def test_owner_matches_the_split_plan() -> None:
    expected = {ac: package for package, acs in _EXPECTED_OWNER.items() for ac in acs}
    assert len(expected) == 35

    for row in _results():
        assert row["owner"] == expected[row["id"]], f"{row['id']} 牵头包与实施方案第 5 节不一致"


def test_result_values_are_within_the_allowed_enumeration() -> None:
    for row in _results():
        assert row["result"] in _RESULTS, f"{row['id']} result 取值非法：{row['result']}"


def test_owner_is_one_of_the_four_packages() -> None:
    for row in _results():
        assert row["owner"] in _OWNERS, f"{row['id']} owner 取值非法：{row['owner']}"


def test_each_package_owns_exactly_the_scenarios_from_the_split_plan() -> None:
    """牵头包归属逐项锁定：改归属必须同时改实施方案与本映射。"""

    for package, ac_ids in _EXPECTED_OWNER.items():
        owned = tuple(row["id"] for row in _results() if row["owner"] == package)
        assert owned == ac_ids, f"{package} 包的牵头 AC 与实施方案第 5 节不一致"


def test_verified_requires_evidence_and_real_environment() -> None:
    for row in _results():
        if row["result"] != "verified":
            continue
        assert row["evidence"]["path"], f"{row['id']} 标为 verified 却没有证据位置"
        assert row["real_environment_verified"] is True, (
            f"{row['id']} 标为 verified 却不是真实环境验证；"
            "文档一致或单元、合同、夹具通过不得写成真实环境验收"
        )


def test_untested_rows_carry_no_actual_result() -> None:
    for row in _results():
        if row["result"] == "untested":
            assert row["actual"] is None, f"{row['id']} 未测试却登记了实际结果"


def test_non_untested_rows_explain_themselves() -> None:
    for row in _results():
        if row["result"] == "untested":
            continue
        has_reason = bool(row["unverified_reason"])
        assert has_reason or row["actual"], f"{row['id']} 既没有实际结果也没有未验证原因"


def test_blocker_kinds_are_from_the_enumeration() -> None:
    for row in _results():
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


def test_mirrored_cases_match_results() -> None:
    document = _load_status()
    results = document["results"]
    cases = document["cases"]
    assert [case["id"] for case in cases] == [row["id"] for row in results]
    assert [case["result"] for case in cases] == [row["result"] for row in results]


@pytest.mark.parametrize("ac_id", _B_OWNED)
def test_b_owned_scenarios_have_prerequisite_analysis(ac_id: str) -> None:
    row = next(item for item in _results() if item["id"] == ac_id)
    assert row["prerequisites"], f"{ac_id} 缺少前置条件清单"
    assert row["artifact_versions"], f"{ac_id} 缺少软件与制品版本要求"
    assert row["evidence"]["method"], f"{ac_id} 缺少证据获取方式"
    assert row["blockers"], f"{ac_id} 缺少结构化阻塞项"
    assert row["real_environment_verified"] is False, (
        f"{ac_id} 不应声明真实环境已验证：真实存储、执行事实与入口尚未交付"
    )


def test_update_rules_state_the_boundaries() -> None:
    rules = _load_status()["update_rules"]
    expected = {"owner_field", "scenario_expected", "b_scope", "verified_rule", "no_inference"}
    assert set(rules) == expected
