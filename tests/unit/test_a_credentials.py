"""Unit tests for credential resolution (infrastructure/credentials.py)."""

from __future__ import annotations

import sys

import pytest

from aitest.contracts.secrets import ResolvedSecret
from aitest.infrastructure.credentials import (
    EnvironmentSecretProvider,
    SecretManager,
    SecretUnavailable,
    WindowsCredentialProvider,
)


@pytest.fixture
def env_provider(monkeypatch: pytest.MonkeyPatch) -> EnvironmentSecretProvider:
    monkeypatch.setenv("A_TEST_MODEL_KEY", "super-secret-value")
    return EnvironmentSecretProvider({("model", "deepseek"): "A_TEST_MODEL_KEY"})


@pytest.fixture
def manager(env_provider: EnvironmentSecretProvider) -> SecretManager:
    return SecretManager((env_provider,))


def test_env_provider_resolves_registered(env_provider: EnvironmentSecretProvider) -> None:
    assert (
        env_provider.resolve(purpose="model", reference="deepseek")
        == "super-secret-value"
    )


def test_env_provider_refuses_unregistered(
    env_provider: EnvironmentSecretProvider,
) -> None:
    with pytest.raises(SecretUnavailable):
        env_provider.resolve(purpose="model", reference="other")


def test_env_provider_missing_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A_TEST_MODEL_KEY", raising=False)
    provider = EnvironmentSecretProvider({("model", "deepseek"): "A_TEST_MODEL_KEY"})
    with pytest.raises(SecretUnavailable):
        provider.resolve(purpose="model", reference="deepseek")


def test_manager_resolves_with_source(manager: SecretManager) -> None:
    secret = manager.resolve("deepseek", purpose="model")
    assert secret.reveal() == "super-secret-value"
    assert secret.source == "environment"
    assert secret.purpose == "model"


def test_manager_refuses_unknown_purpose(manager: SecretManager) -> None:
    with pytest.raises(SecretUnavailable):
        manager.resolve("deepseek", purpose="database")


def test_manager_unavailable_when_no_provider_resolves() -> None:
    manager = SecretManager((EnvironmentSecretProvider({}),))
    with pytest.raises(SecretUnavailable):
        manager.resolve("deepseek", purpose="model")


def test_has_secret(manager: SecretManager) -> None:
    assert manager.has_secret("deepseek", purpose="model") is True
    assert manager.has_secret("missing", purpose="model") is False


def test_resolved_secret_repr_never_exposes_value(manager: SecretManager) -> None:
    secret = manager.resolve("deepseek", purpose="model")
    rendered = repr(secret) + str(secret)
    assert "super-secret-value" not in rendered
    assert secret.reveal() == "super-secret-value"


def test_resolved_secret_clear(manager: SecretManager) -> None:
    secret: ResolvedSecret = manager.resolve("deepseek", purpose="model")
    secret.clear()
    assert secret.reveal() == ""


def test_register_env_mapping_on_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A_TEST_HTTP_TOKEN", "token-123")
    manager = SecretManager.default()
    manager.register_env_mapping({("http", "service"): "A_TEST_HTTP_TOKEN"})

    secret = manager.resolve("service", purpose="http")
    assert secret.reveal() == "token-123"


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="仅 Windows")
def test_windows_provider_available_and_missing_raises() -> None:
    provider = WindowsCredentialProvider()
    assert provider.available is True
    with pytest.raises(SecretUnavailable):
        provider.resolve(purpose="model", reference="definitely-not-present")


@pytest.mark.parametrize(
    "reference",
    [
        "evil{0}",
        "evil{}",
        "back\\slash",
        "space name",
        "nul\x00char",
        "",
        "a" * 600,
    ],
)
def test_windows_target_rejects_unsafe_components(reference: str) -> None:
    """模板注入、反斜杠、控制字符、空段、超长目标一律拒绝。"""
    with pytest.raises(SecretUnavailable):
        WindowsCredentialProvider._build_target("model", reference)


def test_windows_target_accepts_registered_shape() -> None:
    target = WindowsCredentialProvider._build_target("model", "deepseek/v1")
    assert target == "aitest/model/deepseek/v1"


def test_windows_provider_unavailable_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux", raising=False)
    provider = WindowsCredentialProvider()
    assert provider.available is False
    with pytest.raises(SecretUnavailable):
        provider.resolve(purpose="model", reference="deepseek/v1")
