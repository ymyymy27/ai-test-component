"""Host routing reaches default assembly without exposing the credential body."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure.capabilities import CapabilityState
from aitest.infrastructure.credentials import EnvironmentSecretProvider, SecretManager
from aitest.infrastructure.model_configuration import host_model_configuration


def configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> Path:
    path = tmp_path / "model.json"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("AITEST_MODEL_CONFIGURATION", str(path))
    return path


def document(**changes: object) -> str:
    return json.dumps({
        "schema_version": "aitest.model-provider/1",
        "endpoint": "https://example.com/v1",
        "credential_reference": "model-test",
        **changes,
    })


@pytest.mark.parametrize("raw", [
    document(endpoint="http://example.com"),
    document(endpoint="https://user:password@example.com"),
    document(endpoint="https://example.com/?key=private"),
    document(endpoint="https://example.com/#private"),
    document(endpoint="https://example.com:bad"),
    document(endpoint="https://example.com/\n"),
    document(api_key="must-not-be-accepted"),
    document(credential_reference="bad reference"),
    document().replace('"endpoint":', '"endpoint":"https://other.example", "endpoint":'),
    "x" * 16385,
])
def test_rejects_unsafe_or_ambiguous_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str,
) -> None:
    configure(tmp_path, monkeypatch, raw)
    with pytest.raises(ValueError, match="宿主模型配置无效") as error:
        host_model_configuration()
    assert "private" not in str(error.value)
    assert "must-not-be-accepted" not in str(error.value)


def test_host_config_reaches_default_assembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(tmp_path, monkeypatch, document())
    monkeypatch.setenv("TEST_MODEL_CREDENTIAL", "test-only-model-secret")
    manager = SecretManager([EnvironmentSecretProvider({
        ("model", "model-test"): "TEST_MODEL_CREDENTIAL",
    })])
    assembly = assemble_workspace_core(
        tmp_path / "workspace", instance_id="model-host", secret_manager=manager,
    )
    try:
        assert assembly.gate is not None
        assert assembly.gate.condition("model").state is CapabilityState.READY
        assert assembly.gate.check("request_model_draft").allowed
    finally:
        assembly.lifetime_lock.release()
    for path in (tmp_path / "workspace").rglob("*"):
        if path.is_file():
            assert b"test-only-model-secret" not in path.read_bytes()


def test_bad_config_only_degrades_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(tmp_path, monkeypatch, document(api_key="not-a-real-key"))
    assembly = assemble_workspace_core(tmp_path / "workspace", instance_id="bad-model-host")
    try:
        assert assembly.gate is not None
        assert assembly.gate.condition("model").state is CapabilityState.DEGRADED
        assert not assembly.gate.check("request_model_draft").allowed
        assert assembly.gate.check("save_plan").allowed
    finally:
        assembly.lifetime_lock.release()
