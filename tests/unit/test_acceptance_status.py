"""验收登记对账（一期）：`tests/acceptance/p1/status.json` 必须与需求规格第 4 节逐字一致。

本测试只检查“登记是否可信”，不产生也不替代任何验收结论。
权威来源：`docs/项目文档/一期/02-需求/02-需求规格.md` 第 4 节。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REQUIREMENTS = _REPO_ROOT / "docs" / "项目文档" / "一期" / "02-需求" / "02-需求规格.md"
_STATUS = _REPO_ROOT / "tests" / "acceptance" / "p1" / "status.json"

_AC_IDS = tuple(f"P1-AC{index:02d}" for index in range(1, 28))
_FR_IDS = tuple(f"FR{index:02d}" for index in range(1, 19))

_RESULTS = frozenset({"untested", "verified", "not_verified", "blocked"})
_BLOCKER_KINDS = frozenset(
    {
        "program_port",
        "real_storage",
        "execution_facts",
        "user_entry",
        "decision",
        "environment",
        "protocol",
        "platform_contract",
    }
)
_REQUIRED_UPDATE_RULES = frozenset({"scenario_expected", "verified_rule", "no_inference"})


def _load_status() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_STATUS.read_text(encoding="utf-8")))


def _load_requirements_scenarios() -> dict[str, tuple[str, str, list[str]]]:
    """从需求规格第 4 节解析验收场景表，返回 {AC: (场景, 预期, 关联功能)}。"""

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


def test_status_lists_all_scenarios_in_order() -> None:
    assert [row["id"] for row in _results()] == list(_AC_IDS)


def test_scenario_and_expected_match_the_requirements_verbatim() -> None:
    authoritative = _load_requirements_scenarios()
    assert set(authoritative) == set(_AC_IDS)

    for row in _results():
        scenario, expected, functions = authoritative[row["id"]]
        assert row["scenario"] == scenario, f"{row['id']} 场景与需求文档不一致"
        assert row["expected"] == expected, f"{row['id']} 预期与需求文档不一致"
        assert row["related_frs"] == functions, f"{row['id']} 关联功能与需求文档不一致"


def test_result_values_are_within_the_allowed_enumeration() -> None:
    for row in _results():
        assert row["result"] in _RESULTS, f"{row['id']} result 取值非法：{row['result']}"


def test_verified_requires_evidence_and_real_environment() -> None:
    for row in _results():
        if row["result"] != "verified":
            continue
        assert row["evidence"]["path"], f"{row['id']} 标为 verified 却没有证据位置"
        assert row["real_environment_verified"] is True, (
            f"{row['id']} 标为 verified 却不是真实环境验证；"
            "文档一致或单元、合同、夹具通过不得写成真实环境验收"
        )


def test_verified_evidence_files_exist_and_are_readable() -> None:
    for row in _results():
        if row["result"] != "verified":
            continue
        evidence = row["evidence"]["path"]
        path = _REPO_ROOT / evidence
        assert path.is_file(), f"{row['id']} 的证据文件不存在：{evidence}"
        assert path.stat().st_size > 0, f"{row['id']} 的证据文件为空：{evidence}"
        if path.suffix.lower() == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        for source in row.get("source_evidence") or []:
            source_path = _REPO_ROOT / source
            assert source_path.is_file(), f"{row['id']} 的来源证据不存在：{source}"


def test_every_fr_has_at_least_one_acceptance_case() -> None:
    covered: set[str] = set()
    for row in _results():
        covered.update(row["related_frs"])
    missing = sorted(set(_FR_IDS) - covered)
    assert missing == [], "以下功能没有对应验收场景：" + "、".join(missing)


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


def test_update_rules_state_the_boundaries() -> None:
    rules = _load_status()["update_rules"]
    assert set(rules) >= _REQUIRED_UPDATE_RULES


def test_verified_rows_record_owner_and_artifact_versions() -> None:
    for row in _results():
        if row["result"] != "verified":
            continue
        assert isinstance(row.get("owner"), str) and row["owner"].strip(), (
            f"{row['id']} 标为 verified 却没有登记 owner"
        )
        versions = row.get("artifact_versions")
        assert isinstance(versions, list) and versions, (
            f"{row['id']} 标为 verified 却没有登记 artifact_versions"
        )


def test_status_records_baseline_commit() -> None:
    commit = _load_status().get("baseline_commit")
    assert isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{7,40}", commit), (
        "status.json 缺少可识别的 baseline_commit"
    )
