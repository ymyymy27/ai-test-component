"""A-09/B-03：模型材料 JSON 投影的严格解码与编码变体过滤。

本文件是交接反例
``docs/validation/p1-abc-remaining-20261004/pending-model-projection-json-safety.py``
（基线 2567bf9 上 14 failed / 1 passed）的正式回归：

- JSON 键或值中的 ``\\uXXXX`` 转义不得隐藏已登记凭据（一期架构02第13节要求
  过滤覆盖“编码变体”）；
- 重复键、NaN/Infinity、数值溢出、孤立代理字符与破损结构无法严格解析时整项
  登记缺口，其余干净材料仍按原顺序投影；
- 命中凭据后的重新序列化仍须是合法 JSON，且最终 UTF-8 字节不得突破单项预算；
- 未命中的合法 JSON 保留原字节与摘要，不因解码重排产生新投影。
"""

from __future__ import annotations

import json

import pytest

from aitest.application.ports import ProjectionStatus
from aitest.domain.planning.model_outbound import MaterialKind
from aitest.infrastructure.projections import ITEM_LIMIT_BYTES, SafeMaterialProjector
from aitest.infrastructure.security import KnownSecretRegistry


def _project(raw: str, *, registry: KnownSecretRegistry | None = None):
    return SafeMaterialProjector(
        registry=registry if registry is not None else KnownSecretRegistry()
    ).project(
        material={MaterialKind.PROJECT_CONTEXT: raw},
        source_snippets_enabled=False,
    )


# --------------------------------------------------- 转义不能隐藏已登记凭据

@pytest.mark.parametrize("shape", ["value", "key", "nested", "list"])
@pytest.mark.parametrize("secret", ["unusual-local-key", "κρυφό-код"])
def test_json_escape_does_not_hide_a_registered_credential(shape, secret):
    registry = KnownSecretRegistry()
    registry.register(secret)
    values = {
        "value": {"note": secret},
        "key": {secret: "safe"},
        "nested": {"facts": [{"note": secret}]},
        "list": [secret, "safe"],
    }
    raw = json.dumps(values[shape], ensure_ascii=True)
    if secret.isascii():
        raw = raw.replace(secret, "".join(f"\\u{ord(char):04x}" for char in secret))
    assert secret not in raw  # 前提：凭据只以转义形式出现
    result = _project(raw, registry=registry)
    assert result.status is ProjectionStatus.PARTIAL
    assert (MaterialKind.PROJECT_CONTEXT, "material.project_context") in result.excluded
    assert result.projected
    # 安全投影必须仍是合法 JSON，且不含原凭据。
    decoded = json.dumps(json.loads(result.projected[0].projected_text), ensure_ascii=False)
    assert secret not in decoded and "[REDACTED]" in decoded
    assert decoded.count("[REDACTED]") == 1


# --------------------------------------------------- 无法严格解析即登记缺口

@pytest.mark.parametrize("raw", [
    '{"note":"first","note":"second"}', '{"note":NaN}', '{"note":1e9999}',
    '{"note":"\\ud800"}', '{"note":"\\u0066ixture-key",broken}',
    "[" * 2000 + "]" * 2000,
])
def test_ambiguous_structured_material_is_excluded_with_other_clean_material_preserved(raw):
    result = SafeMaterialProjector().project(
        material={MaterialKind.PROJECT_CONTEXT: raw, MaterialKind.TEMPLATE_CONTENT: "clean facts"},
        source_snippets_enabled=False,
    )
    assert result.status is ProjectionStatus.PARTIAL
    assert (MaterialKind.PROJECT_CONTEXT, "material.project_context") in result.excluded
    assert [item.projected_text for item in result.projected] == ["clean facts"]


def test_filtered_key_collision_is_a_gap_not_a_silent_merge():
    registry = KnownSecretRegistry()
    registry.register("first-key")
    registry.register("second-key")
    raw = '{"first-key":"a","second-key":"b"}'
    result = _project(raw, registry=registry)
    assert result.status is ProjectionStatus.PARTIAL
    assert (MaterialKind.PROJECT_CONTEXT, "material.project_context") in result.excluded
    assert result.projected == ()


def test_registry_value_in_json_number_position_is_still_filtered():
    registry = KnownSecretRegistry()
    registry.register("12345")
    raw = '{"note":12345}'
    result = _project(raw, registry=registry)
    assert result.status is ProjectionStatus.PARTIAL
    assert (MaterialKind.PROJECT_CONTEXT, "material.project_context") in result.excluded
    assert "12345" not in result.projected[0].projected_text


# --------------------------------------------------- 过滤膨胀与最终字节预算

def test_registry_filter_expansion_cannot_exceed_final_byte_budget():
    registry = KnownSecretRegistry()
    registry.register("x")
    raw = json.dumps({"note": "x" * (ITEM_LIMIT_BYTES // 3)})
    assert len(raw.encode("utf-8")) <= ITEM_LIMIT_BYTES  # 前提：原字节未超限
    result = _project(raw, registry=registry)
    assert result.status is ProjectionStatus.PARTIAL and not result.projected


def test_already_redacted_json_stays_valid_and_unchanged():
    registry = KnownSecretRegistry()
    registry.register("sk-" + "A" * 24)
    raw = '{"api_key":"[REDACTED]"}'
    result = _project(raw, registry=registry)
    assert result.status is ProjectionStatus.COMPLETE
    assert result.projected[0].projected_text == raw
    assert json.loads(result.projected[0].projected_text) == {"api_key": "[REDACTED]"}


def test_clean_json_preserves_exact_original_projection_bytes_and_digest():
    raw = ' { "fact" : true, "items" : [1, "说明"] } '
    result = _project(raw)
    assert result.status is ProjectionStatus.COMPLETE
    assert result.projected[0].projected_text == raw


def test_policy_revision_incremented_for_encoding_variant_filtering():
    from aitest.infrastructure.projections import POLICY_REVISION

    assert POLICY_REVISION >= 4


# --------------------------------------------------- 纯文本路径保持原语义

def test_plain_text_starting_with_brace_but_not_json_is_a_gap():
    result = SafeMaterialProjector().project(
        material={
            MaterialKind.PROJECT_CONTEXT: "{not json at all}",
            MaterialKind.TEMPLATE_CONTENT: "clean facts",
        },
        source_snippets_enabled=False,
    )
    assert result.status is ProjectionStatus.PARTIAL
    assert (MaterialKind.PROJECT_CONTEXT, "material.project_context") in result.excluded
    assert [item.projected_text for item in result.projected] == ["clean facts"]


def test_plain_text_credential_line_still_dropped_and_others_kept():
    result = SafeMaterialProjector().project(
        material={MaterialKind.CASE_CONTENT: "useful line\napi_key=abc123\nsecond line"},
        source_snippets_enabled=False,
    )
    assert result.status is ProjectionStatus.PARTIAL
    text = result.projected[0].projected_text
    assert "api_key=abc123" not in text
    assert "useful line" in text and "second line" in text
