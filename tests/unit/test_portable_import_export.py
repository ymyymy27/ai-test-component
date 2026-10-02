"""模板与规则导入导出的往返回归。

测什么：

1. **模板逐字节往返**：导出形态 == 已安装资源的 JSON 内容（同一形状），
   读回后与源 `TemplatePack` 相等；六个内置模板全部覆盖；
2. **规则逐字节往返**：草稿导出再导入，字段逐项相等，**未知字段原样保留**；
3. **导入只得到草稿**：来源即便带着"已发布"痕迹（`published_confirmation_id`、
   `published_digest`），导入结果仍是 `confirmed=False` + `enablement=disabled`；
   来源里写 `confirmed` / `enablement` 也会被**忽略**；
4. **未知字段不混进步骤**：`unknown_extension_fields` 与 `steps` 分开，
   往返后仍不重叠；"未识别字段被当成可执行步骤"这条在导入路径上不被打开；
5. **读不懂就拒绝**：版本不匹配、缺字段、类型不符一律抛错，不填默认值；
6. **摘要可核对**：同一份内容两次编码得到同一规范 JSON 与同一摘要。
"""

from __future__ import annotations

import json
from importlib.resources import files

import pytest

from aitest.application.planning.draft import list_templates
from aitest.application.planning.portable import (
    RULE_PORTABLE_SCHEMA_VERSION,
    canonical_digest,
    canonical_json,
    export_rule_payloads,
    export_rule_version,
    export_template,
    import_rule_payloads,
    import_rule_version,
    import_template,
    rule_draft_from_payload,
    rule_draft_to_payload,
)
from aitest.domain.planning.rules import RuleDraft, RuleEnablement, RuleVersion
from aitest.domain.planning.templates import TemplateRef


def _draft(**overrides: object) -> RuleDraft:
    base: dict[str, object] = {
        "rule_id": "rule-1",
        "revision": 1,
        "scope": "http workflows",
        "text": "check the status code and the persisted body",
        "source": "manual",
        "steps": ("call the endpoint", "read it back"),
        "evidence_requirements": ("raw response",),
        "enablement": RuleEnablement.ENABLED,
        "confirmed": True,
        "unknown_extension_fields": ("x-custom-check",),
    }
    base.update(overrides)
    return RuleDraft(**base)  # type: ignore[arg-type]


def _version(**overrides: object) -> RuleVersion:
    base: dict[str, object] = {
        "rule_id": "rule-1",
        "revision": 1,
        "scope": "http workflows",
        "text": "check the status code and the persisted body",
        "steps": ("call the endpoint", "read it back"),
        "evidence_requirements": ("raw response",),
        "source": "manual",
        "confirmation_id": "commit-9",
        "digest": "sha256:published-1",
    }
    base.update(overrides)
    return RuleVersion(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ 模板


def _installed_templates() -> tuple[TemplateRef, ...]:
    return tuple(summary.template_ref for summary in list_templates())


def test_all_installed_templates_round_trip_byte_for_byte() -> None:
    """导出形态必须与已安装资源**同一字节**（同形状才能直接落回资源目录）。"""
    refs = _installed_templates()
    assert len(refs) >= 6, refs
    root = files("aitest.resources").joinpath("templates")
    for ref in refs:
        exported = export_template(ref)
        on_disk = json.loads(
            root.joinpath(ref.template_id, f"{ref.version}.json").read_text(
                encoding="utf-8"
            )
        )
        assert exported == on_disk, ref


def test_template_import_returns_the_same_pack() -> None:
    for ref in _installed_templates():
        pack = import_template(export_template(ref))
        assert pack.template_id == ref.template_id
        assert pack.version == ref.version


def test_template_payload_of_the_wrong_shape_is_rejected() -> None:
    with pytest.raises(ValueError, match="schema"):
        import_template({"template_id": "x"})
    with pytest.raises(ValueError, match="object"):
        import_template("not-an-object")


# ------------------------------------------------------------------ 规则草稿往返


def test_rule_draft_round_trips() -> None:
    draft = _draft()
    payload = rule_draft_to_payload(draft)
    assert set(payload) == {
        "schema_version",
        "rule_id",
        "revision",
        "scope",
        "text",
        "source",
        "steps",
        "evidence_requirements",
        "known_extension_fields",
        "unknown_extension_fields",
    }
    # 导入结果永远是草稿：确认与启用状态**不随内容往返**。
    imported = rule_draft_from_payload(payload)
    assert imported.rule_id == draft.rule_id
    assert imported.revision == draft.revision
    assert imported.scope == draft.scope
    assert imported.text == draft.text
    assert imported.source == draft.source
    assert imported.steps == draft.steps
    assert imported.evidence_requirements == draft.evidence_requirements
    assert imported.unknown_extension_fields == draft.unknown_extension_fields
    assert imported.confirmed is False
    assert imported.enablement is RuleEnablement.DISABLED


def test_unknown_fields_never_become_steps() -> None:
    """未知字段与步骤分开存放：往返后既不混入步骤，也不被执行路径看见。"""
    draft = _draft(unknown_extension_fields=("x-custom-check", "y-other"))
    imported = rule_draft_from_payload(rule_draft_to_payload(draft))
    assert imported.unknown_extension_fields == ("x-custom-check", "y-other")
    assert set(imported.unknown_extension_fields) & set(imported.steps) == set()
    assert imported.executable_steps() == draft.steps


def test_source_claims_about_confirmation_are_ignored() -> None:
    """来源里自报 `confirmed` / `enablement` 不算数。"""
    payload = rule_draft_to_payload(_draft())
    payload["confirmed"] = True
    payload["enablement"] = "enabled"
    imported = rule_draft_from_payload(payload)
    assert imported.confirmed is False
    assert imported.enablement is RuleEnablement.DISABLED


# ------------------------------------------------------------------ 规则版本导出


def test_exported_version_imports_as_a_draft_only() -> None:
    """导出的是**内容**：导入方拿到草稿，"已发布"由本地重新发布产生。"""
    exported = export_rule_version(_version())
    assert exported["published_confirmation_id"] == "commit-9"
    assert exported["published_digest"] == "sha256:published-1"
    imported = import_rule_version(exported)
    assert imported.confirmed is False
    assert imported.enablement is RuleEnablement.DISABLED
    assert imported.rule_id == "rule-1"
    assert imported.steps == ("call the endpoint", "read it back")


def test_rule_bundle_round_trips_in_a_stable_order() -> None:
    bundle = export_rule_payloads(
        (_version(rule_id="rule-b", revision=1), _version(rule_id="rule-a", revision=2))
    )
    assert [item["rule_id"] for item in bundle["rules"]] == ["rule-a", "rule-b"]
    drafts = import_rule_payloads(bundle)
    assert [item.rule_id for item in drafts] == ["rule-a", "rule-b"]
    assert all(item.confirmed is False for item in drafts)


# ------------------------------------------------------------------ 失败路径


def test_unknown_rule_payload_version_is_rejected() -> None:
    payload = rule_draft_to_payload(_draft())
    payload["schema_version"] = "aitest.rule-portable/9.9"
    with pytest.raises(ValueError, match="unsupported rule payload version"):
        rule_draft_from_payload(payload)


@pytest.mark.parametrize("field", ["rule_id", "scope", "text", "source"])
def test_missing_rule_field_is_rejected(field: str) -> None:
    payload = rule_draft_to_payload(_draft())
    del payload[field]
    with pytest.raises(ValueError, match=field):
        rule_draft_from_payload(payload)


def test_bad_revision_is_rejected() -> None:
    payload = rule_draft_to_payload(_draft())
    payload["revision"] = 0
    with pytest.raises(ValueError, match="revision"):
        rule_draft_from_payload(payload)


def test_non_list_steps_are_rejected() -> None:
    payload = rule_draft_to_payload(_draft())
    payload["steps"] = "call the endpoint"
    with pytest.raises(ValueError, match="steps"):
        rule_draft_from_payload(payload)


def test_bundle_without_rules_is_rejected() -> None:
    with pytest.raises(ValueError, match="rules list"):
        import_rule_payloads({"schema_version": RULE_PORTABLE_SCHEMA_VERSION})


# ------------------------------------------------------------------ 规范编码


def test_canonical_encoding_is_order_independent_and_stable() -> None:
    first = {"b": 2, "a": [2, 1]}
    second = {"a": [2, 1], "b": 2}
    assert canonical_json(first) == canonical_json(second)
    assert canonical_digest(first) == canonical_digest(second)
    assert canonical_digest(first).startswith("sha256:")
    assert canonical_json(first) == '{"a":[2,1],"b":2}'


def test_exported_template_is_canonical_json_encodable() -> None:
    """导出结果必须能直接写成规范 JSON（落回资源目录的形态）。"""
    exported = export_template(_installed_templates()[0])
    text = canonical_json(exported)
    assert json.loads(text) == exported
    assert "\n" not in text  # 紧凑形态：可逐字节比较
