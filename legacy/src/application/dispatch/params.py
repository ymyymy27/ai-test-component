"""命令参数解析原语。

这些函数原先全部定义在 `usecase_registry.py` 里，33 个 handler 共用。
抽出后保留同名（含前导下划线），registry 直接 import，调用点无需改名。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from aitest.application.dispatch.errors import BUseCaseError

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_StrEnumT = TypeVar("_StrEnumT", bound=StrEnum)


def _as_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a non-empty string")
    return value


def _as_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be an object")
    return value


def _as_limit(value: object) -> int:
    if value is None:
        return 50
    if isinstance(value, bool) or not isinstance(value, int):
        raise BUseCaseError("B_INVALID_PARAMETER", "limit must be an integer")
    if value < 1:
        raise BUseCaseError("B_INVALID_PARAMETER", "limit must be >= 1")
    return value


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _as_text(value, name)


def _stage_result(
    *,
    aggregate_kind: str,
    record_id: str,
    revision: int,
) -> Mapping[str, object]:
    """写动作的成功结果：只报这次真正提交的事实（不报会误导的 commit_seq）。"""

    return {
        "aggregate_kind": aggregate_kind,
        "record_id": record_id,
        "revision": revision,
    }


def _command_project_id(command: object) -> str:
    return _as_text(getattr(command, "project_id", None), "project_id")


def _command_expected_revision(command: object) -> int | None:
    value: object = getattr(command, "expected_revision", None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BUseCaseError("B_INVALID_PARAMETER", "expected_revision must be an integer")
    return value


def _command_parameters(command: object) -> Mapping[str, object]:
    return _as_mapping(getattr(command, "parameters", None), "parameters")


def _command_action(command: object) -> str:
    return _as_text(getattr(command, "action", None), "action")


def _require_owned_by_command(
    payload: Mapping[str, object],
    *,
    command: object,
    name: str,
    aggregate_kind: str,
) -> None:
    """正文自带的项目必须与命令的项目一致（检查项 B-12）。"""

    declared = payload.get("project_id")
    if declared is None:
        return
    if declared != _command_project_id(command):
        raise BUseCaseError(
            "B_INVALID_PARAMETER",
            f"{aggregate_kind} {name} declares project_id {declared!r}, which does not "
            "match the command project",
        )


def _required(parameters: Mapping[str, object], name: str) -> object:
    if name not in parameters:
        raise BUseCaseError("B_INVALID_PARAMETER", f"missing required parameter: {name}")
    return parameters[name]


def _model_of(model: type[_ModelT], value: object, name: str) -> _ModelT:
    """按 contracts 模型解析参数；失败时报字段名而不是堆栈。"""

    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", f"invalid {name}: {error.error_count()} field error(s)"
        ) from error


def _models_of(model: type[_ModelT], value: object, name: str) -> tuple[_ModelT, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a list")
    return tuple(_model_of(model, item, f"{name}[{index}]") for index, item in enumerate(value))


def _text_list(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a list of strings")
    out: list[str] = []
    for index, item in enumerate(value):
        out.append(_as_text(item, f"{name}[{index}]"))
    return tuple(out)


def _enum_of(enum: type[_StrEnumT], value: object, name: str) -> _StrEnumT:
    text = _as_text(value, name)
    try:
        return enum(text)
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum)
        raise BUseCaseError(
            "B_INVALID_PARAMETER", f"invalid {name}: {text!r}; allowed: {allowed}"
        ) from error


def _enum_or(enum: type[_StrEnumT], value: object, name: str, default: _StrEnumT) -> _StrEnumT:
    """可选枚举：缺省时用领域默认值，给了就必须合法。"""

    if value is None:
        return default
    return _enum_of(enum, value, name)


def _bool_of(value: object, name: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a boolean")
    return value


def _int_of(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be an integer")
    return value


def _revision_of(value: object, name: str) -> int:
    """修订号：整数且 >= 1（`InputRevisions` 的既有约定是"修订从 1 起"）。"""

    number = _int_of(value, name)
    if number < 1:
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be >= 1")
    return number


def _payload_revision_or_none(command: object, *, default: int, name: str) -> int | None:
    """取"我看到的修订"，允许调用方用 0 表示新建。"""

    value = getattr(command, "expected_revision", None)
    if value is None:
        return None
    number = _int_of(value, name)
    if number == 0:
        return None
    return number


def _expected_revision_or_none(command: object) -> int | None:
    """命令里的"我看到的修订"，0 表示新建；发布基线只能由调用方给出。"""

    value = getattr(command, "expected_revision", None)
    if value is None:
        return None
    number = _int_of(value, "expected_revision")
    if number == 0:
        return None
    return number


def _optional_revision(value: object, name: str) -> int | None:
    """可选修订：None 表示不适用（不是 0、不是未知）。"""

    if value is None:
        return None
    return _revision_of(value, name)


def _optional_commit(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _as_text(value, name)


__all__ = [
    "_as_limit",
    "_as_mapping",
    "_as_text",
    "_bool_of",
    "_command_action",
    "_command_expected_revision",
    "_command_parameters",
    "_command_project_id",
    "_enum_of",
    "_enum_or",
    "_expected_revision_or_none",
    "_int_of",
    "_model_of",
    "_models_of",
    "_optional_commit",
    "_optional_revision",
    "_optional_text",
    "_payload_revision_or_none",
    "_require_owned_by_command",
    "_required",
    "_revision_of",
    "_stage_result",
    "_text_list",
]
