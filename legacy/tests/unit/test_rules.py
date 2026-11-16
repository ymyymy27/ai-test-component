import pytest

from aitest.domain.planning.rules import (
    RuleDraft,
    RuleEnablement,
    RuleRevisionRef,
    RuleVersion,
    validate_draft_publication,
)


def _draft(**overrides: object) -> RuleDraft:
    base: dict[str, object] = {
        "rule_id": "rule-1",
        "revision": 1,
        "scope": "适用于L2函数级检查",
        "text": "断言必须绑定独立预期，不得以被测实现自身输出为预期",
        "source": "manual",
    }
    return RuleDraft(**(base | overrides))  # type: ignore[arg-type]


def _version(**overrides: object) -> RuleVersion:
    base: dict[str, object] = {
        "rule_id": "rule-1",
        "revision": 1,
        "scope": "适用于L2函数级检查",
        "text": "断言必须绑定独立预期",
        "steps": ("读取实际值", "与独立预期比对"),
        "evidence_requirements": ("实际值", "独立预期"),
        "source": "manual",
        "confirmation_id": "confirm-1",
        "digest": "sha256:abc",
    }
    return RuleVersion(**(base | overrides))  # type: ignore[arg-type]


def test_draft_requires_identity_scope_text_and_source() -> None:
    with pytest.raises(ValueError, match="rule_id"):
        _draft(rule_id=" ")
    with pytest.raises(ValueError, match="scope"):
        _draft(scope="")
    with pytest.raises(ValueError, match="text"):
        _draft(text="  ")
    with pytest.raises(ValueError, match="source"):
        _draft(source="")
    with pytest.raises(ValueError, match="revision"):
        _draft(revision=0)


def test_unconfirmed_draft_must_not_be_enabled() -> None:
    """导入规则未确认发布不生效（需求 P1-FR05）。

    构造即被拒绝，因此拼不出"未确认却已启用"的草稿。
    """
    with pytest.raises(ValueError, match="unconfirmed"):
        _draft(enablement=RuleEnablement.ENABLED, confirmed=False)


def test_enabled_draft_requires_confirmation() -> None:
    with pytest.raises(ValueError, match="unconfirmed"):
        _draft(enablement=RuleEnablement.ENABLED, confirmed=False)
    enabled = _draft(enablement=RuleEnablement.ENABLED, confirmed=True)
    assert enabled.enablement is RuleEnablement.ENABLED


def test_unknown_extension_fields_are_never_executable() -> None:
    """未知执行字段不得静默启用（需求 P1-FR05）。"""
    draft = _draft(
        steps=("读取实际值", "与独立预期比对"),
        unknown_extension_fields=("execute_shell", "post_hook"),
    )
    assert draft.executable_steps() == ("读取实际值", "与独立预期比对")
    assert "execute_shell" not in draft.executable_steps()
    assert "post_hook" not in draft.executable_steps()


def test_unknown_extension_fields_must_not_overlap_steps() -> None:
    with pytest.raises(ValueError, match="must not be executable steps"):
        _draft(steps=("读取实际值",), unknown_extension_fields=("读取实际值",))


def test_unknown_extension_fields_are_unique() -> None:
    with pytest.raises(ValueError, match="unknown_extension_fields"):
        _draft(unknown_extension_fields=("a", "a"))


def test_draft_has_no_publication_path() -> None:
    """发布是独立的人工动作：草稿对象上不得出现发布方法。"""
    draft = _draft()
    for name in ("publish", "to_version", "publish_version"):
        assert not hasattr(draft, name)


def test_publication_gate_requires_confirmation_and_confirmation_id() -> None:
    validate_draft_publication(_draft(confirmed=True), "confirm-1")
    with pytest.raises(ValueError, match="unconfirmed"):
        validate_draft_publication(_draft(confirmed=False), "confirm-1")
    with pytest.raises(ValueError, match="confirmation_id"):
        validate_draft_publication(_draft(confirmed=True), " ")


def test_published_version_requires_identity_and_content() -> None:
    with pytest.raises(ValueError, match="confirmation_id"):
        _version(confirmation_id="")
    with pytest.raises(ValueError, match="digest"):
        _version(digest=" ")
    with pytest.raises(ValueError, match="revision"):
        _version(revision=0)


def test_published_version_is_immutable() -> None:
    version = _version()
    with pytest.raises(AttributeError):
        version.text = "changed"  # type: ignore[misc]


def test_rule_revision_ref_references_an_exact_revision() -> None:
    version = _version()
    ref = RuleRevisionRef.of(version)
    assert ref == RuleRevisionRef(rule_id="rule-1", revision=1, digest="sha256:abc")
    with pytest.raises(ValueError, match="revision"):
        RuleRevisionRef(rule_id="rule-1", revision=0, digest="sha256:abc")
