"""冻结执行来源绑定的形状校验（A-08/B-04 输入侧）。

`ExecutionSourceBinding` 是 prepare 冻结的预期执行来源，也是 start 时"期望→实际"
核对的依据。空白或重复的允许环境键/SecretRef，以及空的适配器版本，都不能被当成
真实来源约束；本文件固定这些形状要求，并明确**不**限制必需的入口参数（空字符串
是合法参数值）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aitest.contracts.prepared_run import ExecutionSourceBinding


def binding(**overrides: object) -> ExecutionSourceBinding:
    values: dict[str, object] = {
        "registered_entry": "public-python",
        "entry_arguments": ("-m", "pytest", "tests/acceptance"),
        "cwd_mapping": "workdir:/",
        "allowed_env_keys": ("PYTHONPATH",),
        "secret_refs": ("MODEL_API_KEY",),
        "test_config_ref": "config:default@1",
        "adapter_versions": {"command": "1.0.0"},
        "resolved_input_digest": "sha256:resolved-input-1",
    }
    values.update(overrides)
    return ExecutionSourceBinding(**values)  # type: ignore[arg-type]


def test_valid_binding_round_trips_exactly() -> None:
    original = binding()
    assert ExecutionSourceBinding.model_validate_json(
        original.model_dump_json()
    ) == original


@pytest.mark.parametrize("field", ["allowed_env_keys", "secret_refs"])
@pytest.mark.parametrize(
    "values",
    [
        ("",),
        ("  ",),
        ("PYTHONPATH", "PYTHONPATH"),
        ("MODEL_API_KEY", ""),
        (1,),
    ],
)
def test_allow_lists_must_be_nonempty_and_unique(field: str, values: tuple[object, ...]) -> None:
    with pytest.raises(ValidationError):
        binding(**{field: values})


@pytest.mark.parametrize(
    "versions",
    [
        {"": "1.0.0"},
        {"   ": "1.0.0"},
        {"command": ""},
        {"command": "   "},
        {"command": 1},
    ],
)
def test_adapter_versions_require_nonempty_keys_and_values(versions: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        binding(adapter_versions=versions)


def test_required_entry_arguments_may_contain_an_empty_value() -> None:
    """空字符串是合法命令行参数值，形状校验不得把它当成缺失。"""
    parsed = binding(entry_arguments=("-c", "", "--flag"))
    assert parsed.entry_arguments == ("-c", "", "--flag")


def test_unknown_binding_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        binding(extra_mapping="any")


def test_required_identities_stay_nonempty() -> None:
    for field in ("registered_entry", "cwd_mapping", "test_config_ref", "resolved_input_digest"):
        for value in ("", 1, None):
            with pytest.raises(ValidationError):
                binding(**{field: value})
