"""Known credentials must be removed from decoded JSON before outbound bytes exist."""

import json

import pytest

from aitest.application.ports import ProjectionStatus
from aitest.domain.planning.model_outbound import MaterialKind
from aitest.infrastructure.projections import ITEM_LIMIT_BYTES, SafeMaterialProjector
from aitest.infrastructure.security import KnownSecretRegistry


@pytest.mark.parametrize("shape", ["value", "key", "nested", "list"])
@pytest.mark.parametrize("secret", ["unusual-local-key", "κρυφό-код"])
def test_json_escape_does_not_hide_a_registered_credential(shape, secret):
    registry = KnownSecretRegistry()
    registry.register(secret)
    values = {
        "value": {"note": secret}, "key": {secret: "safe"},
        "nested": {"facts": [{"note": secret}]}, "list": [secret, "safe"],
    }
    raw = json.dumps(values[shape], ensure_ascii=True)
    if secret.isascii():
        raw = raw.replace(secret, "".join(f"\\u{ord(char):04x}" for char in secret))
    result = SafeMaterialProjector(registry=registry).project(
        material={MaterialKind.PROJECT_CONTEXT: raw}, source_snippets_enabled=False,
    )
    assert result.status is ProjectionStatus.PARTIAL
    assert (MaterialKind.PROJECT_CONTEXT, "material.project_context") in result.excluded
    assert result.projected
    decoded = json.dumps(json.loads(result.projected[0].projected_text), ensure_ascii=False)
    assert secret not in decoded and "[REDACTED]" in decoded


@pytest.mark.parametrize("raw", [
    '{"note":"first","note":"second"}', '{"note":NaN}', '{"note":1e9999}',
    '{"note":"\\ud800"}', '{"note":"\\u0066ixture-key",broken}',
])
def test_ambiguous_structured_material_is_excluded_with_other_clean_material_preserved(raw):
    result = SafeMaterialProjector().project(
        material={MaterialKind.PROJECT_CONTEXT: raw, MaterialKind.TEMPLATE_CONTENT: "clean facts"},
        source_snippets_enabled=False,
    )
    assert result.status is ProjectionStatus.PARTIAL
    assert (MaterialKind.PROJECT_CONTEXT, "material.project_context") in result.excluded
    assert [item.projected_text for item in result.projected] == ["clean facts"]


def test_registry_filter_expansion_cannot_exceed_final_byte_budget():
    registry = KnownSecretRegistry()
    registry.register("x")
    raw = json.dumps({"note": "x" * (ITEM_LIMIT_BYTES // 3)})
    result = SafeMaterialProjector(registry=registry).project(
        material={MaterialKind.PROJECT_CONTEXT: raw}, source_snippets_enabled=False,
    )
    assert result.status is ProjectionStatus.PARTIAL and not result.projected


def test_clean_json_preserves_exact_original_projection_bytes_and_digest():
    raw = ' { "fact" : true, "items" : [1, "说明"] } '
    result = SafeMaterialProjector().project(
        material={MaterialKind.PROJECT_CONTEXT: raw}, source_snippets_enabled=False,
    )
    assert result.status is ProjectionStatus.COMPLETE
    assert result.projected[0].projected_text == raw
