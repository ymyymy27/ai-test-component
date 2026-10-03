"""规则导入导出的出口动作测试：可携带格式经统一入口往返并落盘。

重点：

1. **导入只得到草稿**：`import_rules` 落盘的是 `rule_draft` 记录，
   响应里明确 `confirmed=false` / `enablement=disabled`；
2. **导出取自已发布记录**：`export_rules` 的摘要来自 `rule_version` 记录，
   与发布时落盘的那一份一致；
3. **导入后再发布**：导入的草稿**未确认**，因此直接发布会被门禁拒绝——
   这条正是"导入不是发布"的可执行证据；
4. **失败指名到字段**：非法 bundle、未知版本、缺规则列表都要被拒。

不使用 `tmp_path`：受限执行环境拒绝 pytest 临时目录工厂在 basetemp 上的目录枚举。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.application.usecase_registry import BUseCaseDependencies
from aitest.contracts.commands import Command
from aitest.infrastructure.file_store.index import FileQueryIndex
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from aitest.interfaces.local.b_registration import register_b_use_cases

PROJECT_ID = "project-io"
WORKSPACE_ID = "ws-io"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 2, 13, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


class _Sequences:
    def __init__(self, root: Path) -> None:
        self._orchestrator = RecoveryOrchestrator(root, instance_id="io-test")

    def current_commit_sequence(self) -> int:
        committed = self._orchestrator.inspect()["committed_sequences"]
        assert isinstance(committed, tuple)
        return max(committed) if committed else 0


@dataclass(frozen=True, slots=True)
class _Stack:
    unit_of_work: PortsUnitOfWork
    reader: PortsRecordReader


def _start(root: Path) -> _Stack:
    raw = FileUnitOfWork(root)
    repository = raw.repo
    return _Stack(
        unit_of_work=PortsUnitOfWork(
            raw, repository=repository, sequence=_Sequences(root)
        ),
        reader=PortsRecordReader(repository),
    )


@pytest.fixture
def workspace_root() -> Iterator[Path]:
    root = Path(tempfile.gettempdir()) / f"aitest-io-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _api(stack: _Stack) -> LocalAPI:
    api = LocalAPI(instance_id="core-io", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(
        api,
        BUseCaseDependencies(
            unit_of_work=stack.unit_of_work,
            reader=stack.reader,
            clock=_FixedClock(),
        ),
    )
    return api


def _session() -> Session:
    return Session(session_id="cli-io", entry_kind=EntryKind.HUMAN_UI)


def _command(action: str, request_id: str, parameters: Mapping[str, object]) -> Command:
    return Command(
        request_id=request_id,
        action=action,
        project_id=PROJECT_ID,
        expected_revision=0,
        intent_id=f"intent-{request_id}",
        parameters=dict(parameters),
    )


def _bundle(rule_id: str = "rule-1") -> dict[str, object]:
    return {
        "schema_version": "aitest.rule-portable/1.0",
        "rules": [
            {
                "schema_version": "aitest.rule-portable/1.0",
                "rule_id": rule_id,
                "revision": 1,
                "scope": "http workflows",
                "text": "check the status code and the persisted body",
                "source": "imported",
                "steps": ["call the endpoint", "read it back"],
                "evidence_requirements": ["raw response"],
                "known_extension_fields": ["call the endpoint", "read it back"],
                "unknown_extension_fields": ["x-custom-check"],
            }
        ],
    }


def _publish_and_export(api: LocalAPI) -> dict[str, object]:
    """先发布一条规则，再把它导出：导出内容应取自已发布记录。"""
    published = api.dispatch(
        _command(
            "publish_rules",
            "req-publish-1",
            {
                "draft": {
                    "rule_id": "rule-1",
                    "revision": 1,
                    "scope": "http workflows",
                    "text": "check the status code and the persisted body",
                    "source": "manual",
                    "steps": ["call the endpoint", "read it back"],
                    "evidence_requirements": ["raw response"],
                    "enablement": "enabled",
                    "confirmed": True,
                }
            },
        ),
        _session(),
    )
    assert published.error is None, published.error
    exported = api.dispatch(
        _command(
            "export_rules",
            "req-export-1",
            {"rule_versions": [{"rule_id": "rule-1", "revision": 1}]},
        ),
        _session(),
    )
    assert exported.error is None, exported.error
    assert exported.result is not None
    return exported.result["bundle"]


def _markdown_document(rule_id: str, text: str) -> str:
    """一份最小但**完整**的规则 Markdown 文档（方言见 `rules_markdown.py`）。"""
    return (
        f"# 规则：{rule_id} @1\n"
        "\n"
        "- 规范版本：aitest.rule-portable/1.0\n"
        "- 适用范围：http workflows\n"
        "- 来源：imported\n"
        "\n"
        "## 规则正文\n"
        "\n"
        f"{text}\n"
        "\n"
        "## 步骤\n"
        "\n"
        "- call the endpoint\n"
        "\n"
        "## 证据要求\n"
        "\n"
        "_（无）_\n"
        "\n"
        "## 未识别字段\n"
        "\n"
        "```json\n"
        "[]\n"
        "```\n"
    )


# ------------------------------------------------------------------ Markdown 形态（B-04）


def test_export_rules_markdown_renders_a_readable_document(
    workspace_root: Path,
) -> None:
    """`export_rules_markdown` 取自已发布记录，渲染成人类可读的 Markdown。"""
    FileQueryIndex(workspace_root).rebuild(())
    api = _api(_start(workspace_root))
    _publish_and_export(api)
    response = api.dispatch(
        _command(
            "export_rules_markdown",
            "req-export-md-1",
            {"rule_versions": [{"rule_id": "rule-1", "revision": 1}]},
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    documents = response.result["documents"]
    assert isinstance(documents, list) and len(documents) == 1
    document = documents[0]
    assert document["rule_id"] == "rule-1"
    assert document["revision"] == 1
    markdown = document["markdown"]
    assert isinstance(markdown, str)
    # 身份、四个标签与四个段落名都在（"给人看"的最低要求）。
    for token in (
        "# 规则：rule-1 @1",
        "- 规范版本：aitest.rule-portable/1.0",
        "- 适用范围：http workflows",
        "- 来源：manual",
        "## 规则正文",
        "## 步骤",
        "## 证据要求",
        "## 未识别字段",
    ):
        assert token in markdown, token


def test_a_markdown_document_round_trips_through_the_entry(
    workspace_root: Path,
) -> None:
    """**导出 → 导入**经统一入口往返：字段语义一致，且落盘的是未确认草稿。"""
    FileQueryIndex(workspace_root).rebuild(())
    api = _api(_start(workspace_root))
    _publish_and_export(api)
    exported = api.dispatch(
        _command(
            "export_rules_markdown",
            "req-export-md-1",
            {"rule_versions": [{"rule_id": "rule-1", "revision": 1}]},
        ),
        _session(),
    )
    assert exported.error is None, exported.error
    assert exported.result is not None
    markdown = exported.result["documents"][0]["markdown"]

    imported = api.dispatch(
        _command("import_rules_markdown", "req-import-md-1", {"markdown": markdown}),
        _session(),
    )
    assert imported.error is None, imported.error
    assert imported.result is not None
    entry = imported.result["imported"][0]
    assert entry["aggregate_kind"] == "rule_draft"
    assert entry["rule_id"] == "rule-1"
    # 导入不是发布：恒为未确认、未启用。
    assert entry["confirmed"] is False
    assert entry["enablement"] == "disabled"

    # 重启后按记录读回：字段与导出前的内容一致。
    committed = _start(workspace_root).reader.read(
        aggregate_kind="rule_draft", record_id="rule-1", revision=1
    )
    assert committed.payload["scope"] == "http workflows"
    assert committed.payload["text"] == "check the status code and the persisted body"
    assert committed.payload["steps"] == ["call the endpoint", "read it back"]
    assert committed.payload["evidence_requirements"] == ["raw response"]
    assert "confirmed" not in committed.payload


def test_import_rules_markdown_accepts_several_documents(
    workspace_root: Path,
) -> None:
    """一次可导入多份；每份各自成一条草稿记录。"""
    api = _api(_start(workspace_root))
    documents = [
        _markdown_document(rule_id, f"text for {rule_id}")
        for rule_id in ("rule-a", "rule-b")
    ]
    response = api.dispatch(
        _command("import_rules_markdown", "req-import-md-2", {"markdown": documents}),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    imported = response.result["imported"]
    assert [item["rule_id"] for item in imported] == ["rule-a", "rule-b"]
    assert all(item["confirmed"] is False for item in imported)


def test_a_broken_markdown_document_is_named_and_nothing_is_stored(
    workspace_root: Path,
) -> None:
    """坏文档**指名报错**，不"跳过坏的继续导入"（否则调用方以为全部都进来了）。"""
    api = _api(_start(workspace_root))
    broken = _markdown_document("rule-a", "ok").replace("## 步骤", "## 步骤清单")
    response = api.dispatch(
        _command("import_rules_markdown", "req-import-md-bad", {"markdown": broken}),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "markdown[0]" in response.error.message
    # 好文档若与坏文档同批，也不得被写入（整批拒绝）。
    restarted = _start(workspace_root)
    with pytest.raises(ValueError, match="unknown revision"):
        restarted.reader.read(aggregate_kind="rule_draft", record_id="rule-a", revision=1)


def test_import_rules_markdown_rejects_a_non_string_entry(
    workspace_root: Path,
) -> None:
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command("import_rules_markdown", "req-import-md-bad", {"markdown": [123]}),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "markdown[0]" in response.error.message


# ------------------------------------------------------------------ 导入


def test_import_rules_stores_a_draft_only(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command("import_rules", "req-import-1", {"bundle": _bundle()}), _session()
    )
    assert response.error is None, response.error
    assert response.result is not None
    imported = response.result["imported"]
    assert isinstance(imported, list) and len(imported) == 1
    entry = imported[0]
    assert entry["aggregate_kind"] == "rule_draft"
    assert entry["rule_id"] == "rule-1"
    assert entry["confirmed"] is False
    assert entry["enablement"] == "disabled"

    # 重启后按记录读回：形状与可携带格式同一套。
    committed = _start(workspace_root).reader.read(
        aggregate_kind="rule_draft", record_id="rule-1", revision=1
    )
    assert committed.payload["project_id"] == PROJECT_ID
    assert committed.payload["unknown_extension_fields"] == ["x-custom-check"]
    assert "confirmed" not in committed.payload
    assert "enablement" not in committed.payload


def test_import_rules_accepts_a_json_string_bundle(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command(
            "import_rules",
            "req-import-1",
            {"bundle": json.dumps(_bundle("rule-2"))},
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["imported"][0]["rule_id"] == "rule-2"


def test_imported_draft_cannot_be_published_without_confirmation(
    workspace_root: Path,
) -> None:
    """导入的草稿**未确认**：直接发布必须被门禁拒绝——"导入不是发布"的可执行证据。"""
    api = _api(_start(workspace_root))
    imported = api.dispatch(
        _command("import_rules", "req-import-1", {"bundle": _bundle()}), _session()
    )
    assert imported.error is None, imported.error

    published = api.dispatch(
        _command(
            "publish_rules",
            "req-publish-1",
            {
                "draft": {
                    "rule_id": "rule-1",
                    "revision": 1,
                    "scope": "http workflows",
                    "text": "check the status code and the persisted body",
                    "source": "imported",
                    "steps": ["call the endpoint", "read it back"],
                    "evidence_requirements": ["raw response"],
                    "enablement": "disabled",
                    "confirmed": False,
                }
            },
        ),
        _session(),
    )
    assert published.error is None, published.error
    assert published.result is not None
    assert published.result["published"] is False
    assert published.result["blocked_by"]


def test_importing_the_same_rule_twice_is_a_revision_conflict(
    workspace_root: Path,
) -> None:
    api = _api(_start(workspace_root))
    first = api.dispatch(
        _command("import_rules", "req-import-1", {"bundle": _bundle()}), _session()
    )
    assert first.error is None, first.error
    second = api.dispatch(
        _command("import_rules", "req-import-2", {"bundle": _bundle()}), _session()
    )
    assert second.error is not None
    assert second.error.code == "B_REVISION_CONFLICT"


# ------------------------------------------------------------------ 导出


def test_export_rules_returns_the_published_content(workspace_root: Path) -> None:
    FileQueryIndex(workspace_root).rebuild(())
    api = _api(_start(workspace_root))
    bundle = _publish_and_export(api)
    rules = bundle["rules"]
    assert isinstance(rules, list) and len(rules) == 1
    rule = rules[0]
    assert rule["rule_id"] == "rule-1"
    assert rule["steps"] == ["call the endpoint", "read it back"]
    # 导出带上"来自哪个已发布版本"，供追溯；它不是"本地已发布"的宣称。
    assert rule["published_digest"].startswith("sha256:")


def test_exported_bundle_can_be_imported_into_another_workspace(
    workspace_root: Path,
) -> None:
    """导出→导入的完整环：内容一致，但导入结果是草稿。"""
    FileQueryIndex(workspace_root).rebuild(())
    api = _api(_start(workspace_root))
    bundle = _publish_and_export(api)

    other = workspace_root.parent / f"{workspace_root.name}-other"
    other.mkdir(parents=True, exist_ok=False)
    try:
        other_api = _api(_start(other))
        imported = other_api.dispatch(
            _command("import_rules", "req-import-1", {"bundle": bundle}), _session()
        )
        assert imported.error is None, imported.error
        assert imported.result is not None
        assert imported.result["imported"][0]["confirmed"] is False

        committed = _start(other).reader.read(
            aggregate_kind="rule_draft", record_id="rule-1", revision=1
        )
        assert committed.payload["steps"] == ["call the endpoint", "read it back"]
        assert committed.payload["text"] == "check the status code and the persisted body"
    finally:
        shutil.rmtree(other, ignore_errors=True)


def test_export_of_an_unpublished_rule_is_rejected(workspace_root: Path) -> None:
    FileQueryIndex(workspace_root).rebuild(())
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command(
            "export_rules",
            "req-export-1",
            {"rule_versions": [{"rule_id": "rule-1", "revision": 1}]},
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "rule-1@1" in response.error.message


# ------------------------------------------------------------------ 失败路径


@pytest.mark.parametrize(
    ("bundle", "named"),
    [
        ({"schema_version": "aitest.rule-portable/9.9", "rules": []}, "version"),
        ({"schema_version": "aitest.rule-portable/1.0"}, "rules"),
        ("not-json", "JSON"),
    ],
)
def test_invalid_bundle_is_rejected(
    workspace_root: Path, bundle: object, named: str
) -> None:
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command("import_rules", "req-import-bad", {"bundle": bundle}), _session()
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert named in response.error.message


def test_import_without_bundle_is_rejected(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command("import_rules", "req-import-bad", {}), _session()
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "bundle" in response.error.message
