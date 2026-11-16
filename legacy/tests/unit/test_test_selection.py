from __future__ import annotations

import json

import pytest

from aitest.application.planning.test_selection import (
    TestSelectionError as SelectionError,
)
from aitest.application.planning.test_selection import (
    materialize_test_selection,
    parse_test_selection,
)


def _inventory() -> dict[str, object]:
    return {
        "schema_version": "aitest.test-inventory/1.0",
        "files": [
            {
                "path": "pkgA/tests/test_order.py",
                "test_count": 2,
                "nodes": [
                    "pkgA/tests/test_order.py::test_create",
                    "pkgA/tests/test_order.py::test_cancel",
                ],
            },
            {
                "path": "pkgB/tests/test_order.py",
                "test_count": 3,
                "nodes": [
                    "pkgB/tests/test_order.py::test_create",
                    "pkgB/tests/test_order.py::test_pay",
                    "pkgB/tests/test_order.py::test_refund",
                ],
            },
        ],
        "summary": {"file_count": 2, "test_count": 5, "collection_error_count": 0},
    }


def _selection(value: str = "pkgA/tests/test_order.py", kind: str = "file") -> str:
    return json.dumps(
        {
            "schema_version": "aitest.test-selection/1.0",
            "goal": "验证订单",
            "selections": [
                {
                    "acceptance_item_ids": ["AC-ORDER-01"],
                    "scope": {"kind": kind, "value": value},
                    "expected": "all_pass",
                    "mock_scope": ["unknown"],
                    "rationale": "覆盖订单创建",
                }
            ],
        }
    )


def test_selection_materializes_server_owned_case() -> None:
    selection = parse_test_selection(_selection())
    scope, cases, warnings = materialize_test_selection(
        selection, _inventory(), project_id="p", environment_id="e", scope_id="s"
    )
    assert warnings == []
    assert scope.required_case_ids == frozenset({cases[0].case_id})
    case = cases[0]
    assert case.inputs == ("pkgA/tests/test_order.py",)
    assert case.steps == ("运行该测试范围并核对结果",)
    assert case.verification_method == "pytest-junit"
    basis = json.loads(case.assertion_basis.text)
    assert basis["schema_version"] == "aitest.pytest-basis/1"
    assert basis["expected_test_count"] == 2
    assert basis["expected_outcome"] == "all_pass"
    assert case.links.acceptance_item_ids == frozenset({"AC-ORDER-01"})


def test_selected_modules_are_frozen_into_the_case_link() -> None:
    raw = json.loads(_selection())
    raw["selections"][0]["module_ids"] = ["module-order"]
    selection = parse_test_selection(json.dumps(raw))
    _, cases, _ = materialize_test_selection(
        selection, _inventory(), project_id="p", environment_id="e", scope_id="s"
    )
    assert cases[0].links.module_ids == frozenset({"module-order"})


def test_unknown_scope_is_rejected() -> None:
    selection = parse_test_selection(_selection("pkgA/tests/missing.py"))
    with pytest.raises(SelectionError):
        materialize_test_selection(
            selection, _inventory(), project_id="p", environment_id="e", scope_id="s"
        )


def test_directory_scope_counts_files() -> None:
    selection = parse_test_selection(_selection("pkgB/tests", "dir"))
    _, cases, _ = materialize_test_selection(
        selection, _inventory(), project_id="p", environment_id="e", scope_id="s"
    )
    basis = json.loads(cases[0].assertion_basis.text)
    assert basis["expected_test_count"] == 3


def test_duplicate_scope_is_rejected() -> None:
    raw = json.loads(_selection())
    raw["selections"].append(raw["selections"][0])
    with pytest.raises(SelectionError):
        materialize_test_selection(
            parse_test_selection(json.dumps(raw)),
            _inventory(),
            project_id="p",
            environment_id="e",
            scope_id="s",
        )


def test_node_scope_requires_an_exact_inventory_node() -> None:
    node = "pkgA/tests/test_order.py::test_create"
    selection = parse_test_selection(_selection(node, "node"))
    _, cases, _ = materialize_test_selection(
        selection, _inventory(), project_id="p", environment_id="e", scope_id="s"
    )
    assert cases[0].inputs == (node,)
    basis = json.loads(cases[0].assertion_basis.text)
    assert basis["expected_test_count"] == 1
    assert basis["scope"] == {"kind": "node", "value": node}


def test_node_scope_rejects_a_node_not_in_the_inventory() -> None:
    node = "pkgA/tests/test_order.py::test_missing"
    selection = parse_test_selection(_selection(node, "node"))
    with pytest.raises(SelectionError):
        materialize_test_selection(
            selection, _inventory(), project_id="p", environment_id="e", scope_id="s"
        )


def test_node_scope_requires_a_listed_file() -> None:
    node = "pkgA/tests/missing.py::test_create"
    selection = parse_test_selection(_selection(node, "node"))
    with pytest.raises(SelectionError):
        materialize_test_selection(
            selection, _inventory(), project_id="p", environment_id="e", scope_id="s"
        )
