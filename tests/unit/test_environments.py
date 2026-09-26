import dataclasses

import pytest

from aitest.domain.project.context import (
    EnvironmentRef,
    IsolationMode,
    ResolvedEnvironment,
    SecretRef,
)


def _environment(**overrides: object) -> EnvironmentRef:
    base: dict[str, object] = {
        "environment_id": "env-1",
        "revision": 1,
        "interpreter_requirement": "3.13",
        "dependency_declaration": "uv.lock",
    }
    return EnvironmentRef(**(base | overrides))  # type: ignore[arg-type]


def test_default_isolation_is_a_virtual_environment() -> None:
    environment = _environment()
    assert environment.isolation_mode is IsolationMode.VENV
    assert not environment.isolation_is_explicitly_disabled


def test_explicitly_disabled_isolation_is_a_legal_fact() -> None:
    """显式不隔离是合法事实，不是缺配置（P1-FR07）。"""
    environment = _environment(
        isolation_mode=IsolationMode.NONE,
        isolation_confirmed=True,
    )
    assert environment.isolation_is_explicitly_disabled


def test_non_default_isolation_must_be_explicitly_confirmed() -> None:
    for mode in (IsolationMode.NONE, IsolationMode.UNMANAGED):
        with pytest.raises(ValueError, match="explicitly confirmed"):
            _environment(isolation_mode=mode, isolation_confirmed=False)


def test_unknown_data_isolation_is_none_not_false() -> None:
    """未知记 None，不得用 False 冒充"未隔离"。"""
    assert _environment().data_isolated is None
    assert _environment(data_isolated=False).data_isolated is False


def test_environment_requires_identity_and_declaration() -> None:
    with pytest.raises(ValueError, match="environment_id"):
        _environment(environment_id=" ")
    with pytest.raises(ValueError, match="revision"):
        _environment(revision=0)
    with pytest.raises(ValueError, match="interpreter_requirement"):
        _environment(interpreter_requirement="")
    with pytest.raises(ValueError, match="dependency_declaration"):
        _environment(dependency_declaration="")


def test_timeouts_must_be_positive_when_set() -> None:
    with pytest.raises(ValueError, match="request_timeout_seconds"):
        _environment(request_timeout_seconds=0)
    with pytest.raises(ValueError, match="step_timeout_seconds"):
        _environment(step_timeout_seconds=-1)


def test_network_targets_and_secret_keys_are_unique() -> None:
    with pytest.raises(ValueError, match="network_targets"):
        _environment(network_targets=("https://a", "https://a"))
    with pytest.raises(ValueError, match="secret env_key"):
        _environment(
            secret_refs=(
                SecretRef(purpose="model", env_key="MODEL_KEY"),
                SecretRef(purpose="http", env_key="MODEL_KEY"),
            )
        )


def test_secret_refs_are_declared_per_purpose() -> None:
    environment = _environment(
        secret_refs=(
            SecretRef(purpose="model", env_key="MODEL_KEY"),
            SecretRef(purpose="database_verification", env_key="DB_DSN"),
        ),
    )
    assert {ref.purpose for ref in environment.secret_refs} == {
        "model",
        "database_verification",
    }


def test_secret_ref_has_no_field_able_to_carry_a_credential_body() -> None:
    """凭据正文不进配置、日志、面板、导出：类型层面就无法表达正文。"""
    names = {item.name for item in dataclasses.fields(SecretRef)}
    assert names == {"purpose", "env_key", "ref"}


def test_secret_ref_requires_purpose_and_env_key() -> None:
    with pytest.raises(ValueError, match="purpose"):
        SecretRef(purpose="", env_key="MODEL_KEY")
    with pytest.raises(ValueError, match="env_key"):
        SecretRef(purpose="model", env_key="")


def test_environment_ref_carries_declarations_only() -> None:
    """声明与解析结果分离：已解析事实只能在 ResolvedEnvironment 上。"""
    declared = {item.name for item in dataclasses.fields(EnvironmentRef)}
    assert "interpreter_identity" not in declared
    assert "dependency_set_digest" not in declared
    resolved = {item.name for item in dataclasses.fields(ResolvedEnvironment)}
    assert {"interpreter_identity", "dependency_set_digest"} <= resolved


def test_resolved_environment_requires_resolved_facts() -> None:
    with pytest.raises(ValueError, match="interpreter_identity"):
        ResolvedEnvironment("env-1", 1, IsolationMode.VENV, "", "digest")
    with pytest.raises(ValueError, match="dependency_set_digest"):
        ResolvedEnvironment("env-1", 1, IsolationMode.VENV, "cpython-3.13.3", "")
