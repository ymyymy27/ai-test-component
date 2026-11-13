"""Internal AI draft for selecting existing pytest scopes.

This is intentionally not a public business contract.  The model selects a
scope and a business expectation; the server owns Case identity, inputs,
links, basis text and verification method.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from aitest.domain.planning.plans import (
    AcceptanceScope,
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseImportance,
    CaseLayer,
    CaseLink,
)

TEST_SELECTION_SCHEMA = "aitest.test-selection/1.0"
PYTEST_BASIS_SCHEMA = "aitest.pytest-basis/1"
PYTEST_METHOD = "pytest-junit"


class TestSelectionError(ValueError):
    code = "B_TEST_SELECTION_INVALID"


class TestScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["file", "dir", "marker", "node"]
    value: str = Field(min_length=1, max_length=4000)

    @field_validator("value")
    @classmethod
    def _no_control(cls, value: str) -> str:
        if "\x00" in value or "\\" in value:
            raise ValueError("scope must be a POSIX path without control characters")
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("scope must stay inside the source snapshot")
        return value


class TestSelectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    acceptance_item_ids: list[str] = Field(min_length=1)
    scope: TestScope
    expected: Literal["all_pass"] = "all_pass"
    mock_scope: list[str] = Field(default_factory=list, max_length=32)
    rationale: str = Field(min_length=1, max_length=2000)

    @field_validator("acceptance_item_ids")
    @classmethod
    def _unique_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value) or len(set(value)) != len(value):
            raise ValueError("acceptance item ids must be non-empty and unique")
        return value


class TestSelectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["aitest.test-selection/1.0"] = "aitest.test-selection/1.0"
    goal: str = Field(min_length=1, max_length=4000)
    selections: list[TestSelectionItem] = Field(min_length=1, max_length=500)


def test_selection_system_prompt() -> str:
    return (
        "You select existing pytest scopes for an AI-assisted local test plan. "
        "Return exactly one JSON object and no prose. Use only scopes visible in "
        "the supplied TEST INVENTORY. Do not invent node IDs, file paths, or "
        "test names. Prefer a directory or file scope over individual tests. "
        "Every selection must name at least one acceptance item. "
        'Schema: {"schema_version":"aitest.test-selection/1.0","goal":"...",'
        '"selections":[{"acceptance_item_ids":["..."],'
        '"scope":{"kind":"file|dir|marker","value":"..."},'
        '"expected":"all_pass","mock_scope":["unknown"],"rationale":"..."}]}'
    )


def parse_test_selection(text: str) -> TestSelectionDraft:
    if len(text.encode("utf-8")) > 128 * 1024:
        raise TestSelectionError("test selection exceeds its 128 KiB budget")
    try:
        raw = json.loads(text)
    except (TypeError, ValueError) as error:
        raise TestSelectionError("test selection is not valid JSON") from error
    if not isinstance(raw, dict):
        raise TestSelectionError("test selection must be one JSON object")
    try:
        return TestSelectionDraft.model_validate(raw)
    except ValidationError as error:
        raise TestSelectionError(f"test selection is invalid: {error}") from error


def _digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _scope_files(
    scope: TestScope, inventory: Mapping[str, object]
) -> tuple[int, list[str], str | None]:
    files = inventory.get("files")
    if not isinstance(files, list):
        return 0, [], "Inventory files are unavailable"
    if scope.kind == "marker":
        return 0, [], "marker scope has no deterministic file count in Stage A"
    if scope.kind == "node":
        file_path, separator, _node = scope.value.partition("::")
        if not separator or not file_path:
            return 0, [], "node scope requires a file path and a ::test node"
        match = next(
            (
                item
                for item in files
                if isinstance(item, Mapping) and item.get("path") == file_path
            ),
            None,
        )
        if match is None:
            return 0, [], "node scope file is not present in the Test Inventory"
        nodes = match.get("nodes")
        if not isinstance(nodes, list):
            return 0, [file_path], "node list is unavailable; rediscover the tests"
        if scope.value not in nodes:
            return 0, [file_path], "node is not present in the Test Inventory"
        return 1, [file_path], None
    matched: list[Mapping[str, object]] = []
    for item in files:
        if not isinstance(item, Mapping):
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        if scope.kind == "file" and path == scope.value:
            matched.append(item)
        elif scope.kind == "dir":
            prefix = scope.value + "/"
            if path == scope.value or path.startswith(prefix):
                matched.append(item)
    count = sum(
        int(value) for item in matched
        if type(value := item.get("test_count", 0)) is int
    )
    if not matched:
        return 0, [], "scope is not present in the Test Inventory"
    if count <= 0:
        return 0, [str(item.get("path")) for item in matched], "scope contains no collected tests"
    return count, [str(item.get("path")) for item in matched], None


def materialize_test_selection(
    selection: TestSelectionDraft,
    inventory: Mapping[str, object],
    *,
    project_id: str,
    environment_id: str,
    scope_id: str,
) -> tuple[AcceptanceScope, tuple[Case, ...], list[str]]:
    """Validate a selection against the Inventory and generate server-owned Cases."""

    if not project_id.strip() or not environment_id.strip() or not scope_id.strip():
        raise TestSelectionError("project, environment and scope identity are required")
    cases: list[Case] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in selection.selections:
        count, matched, issue = _scope_files(item.scope, inventory)
        key = (item.scope.kind, item.scope.value)
        if key in seen:
            raise TestSelectionError("duplicate test scope in one selection")
        seen.add(key)
        if issue is not None:
            if item.scope.kind == "marker":
                warnings.append(issue)
            else:
                raise TestSelectionError(f"{item.scope.value}: {issue}")
        digest = _digest(
            {
                "project_id": project_id,
                "environment_id": environment_id,
                "acceptance_item_ids": sorted(item.acceptance_item_ids),
                "scope": item.scope.model_dump(mode="json"),
            }
        )
        case_id = "case:pytest:" + digest[7:23]
        basis = {
            "schema_version": PYTEST_BASIS_SCHEMA,
            "scope": item.scope.model_dump(mode="json"),
            "expected_test_count": count if count > 0 else None,
            "expected_outcome": item.expected,
        }
        basis_text = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        link = CaseLink(
            acceptance_item_ids=frozenset(item.acceptance_item_ids),
            environment_ids=frozenset({environment_id}),
            no_critical_path_reason="Stage A pytest scope does not declare a critical path",
        )
        cases.append(
            Case(
                case_id=case_id,
                revision=1,
                layer=CaseLayer.L2,
                objective=f"运行 {item.scope.value} 并核对 pytest 结果",
                preconditions=("源码快照与环境已冻结", "pytest 可用"),
                inputs=(item.scope.value,),
                steps=("运行该测试范围并核对结果",),
                expected="该范围全部测试通过",
                verification_method=PYTEST_METHOD,
                links=link,
                assertion_basis=AssertionBasis(
                    1,
                    AssertionBasisState.PRESENT_UNCONFIRMED,
                    basis_text,
                    _digest(basis),
                ),
                independent_verification="按验收项独立核验；pytest 结果不计为独立核验",
                mock_scope=tuple(item.mock_scope or ("unknown",)),
                importance=CaseImportance.P1,
                template_item_id=None,
                data_mutation_units=1,
            )
        )
    if not cases:
        raise TestSelectionError("test selection contains no usable scope")
    scope = AcceptanceScope(
        scope_id=scope_id,
        revision=1,
        name=selection.goal,
        required_case_ids=frozenset(case.case_id for case in cases),
        template_case_ids=frozenset(),
        objective=selection.goal,
    )
    return scope, tuple(sorted(cases, key=lambda case: case.case_id)), warnings


def selection_to_payload(
    selection: TestSelectionDraft,
    cases: Sequence[Case],
    scope: AcceptanceScope,
    warnings: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": TEST_SELECTION_SCHEMA,
        "goal": selection.goal,
        "selection": selection.model_dump(mode="json"),
        "scope_id": scope.scope_id,
        "case_ids": [case.case_id for case in cases],
        "warnings": list(warnings),
    }
