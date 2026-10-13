"""项目列表显示偏好：**只**记录隐藏/恢复（P1-AC34，架构 06／功能 §16）。

合同（功能文档 P1-FR17 段、设计草案 §3.4）：

    ProjectViewPreference 只记录隐藏/恢复偏好，隐藏不停止运行、不删业务数据。
    隐藏和恢复分别调用核心既有 ProjectViewPreference 用例；隐藏当前项目时保留
    其活动运行与查询路径，不静默切到另一个项目；恢复只恢复列表可见性，不启动测试。

因此本模块的边界非常窄：

- 记录形状只有"项目 + 是否隐藏 + 本次提交序号"；**不**保存业务结论、筛选游标或
  任何项目内容副本；
- 写入只落这一条记录：项目、绑定、运行、报告、问题与历史引用**都不动**；
- 只有**已保存的项目**才能被隐藏/恢复（不能凭偏好造出项目，也不能隐藏别的项目）；
- 沿用与其他写动作同一套"调用方所见修订"乐观锁（`0` = 新建）；
- 恢复只把 `hidden` 置回 false，不触发任何测试或运行动作。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

VIEW_PREFERENCE_AGGREGATE: Final[Literal["project_view_preference"]] = "project_view_preference"
VIEW_PREFERENCE_SCHEMA = "aitest.project-view-preference/1.0"
PROJECT_AGGREGATE: Final[Literal["project"]] = "project"


class PreferenceError(ValueError):
    """偏好用例错误；错误码沿用已登记集合。"""

    code = "INVALID_REQUEST"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ProjectViewPreference:
    """一条项目显示偏好：只表达"是否在列表中隐藏"。"""

    project_id: str
    revision: int
    hidden: bool
    updated_at_commit: str

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise PreferenceError("project_id must be nonempty text")
        if type(self.revision) is not int or self.revision < 1:
            raise PreferenceError("preference revision must be a positive integer")
        if type(self.hidden) is not bool:
            raise PreferenceError("hidden must be a boolean")
        if not isinstance(self.updated_at_commit, str) or not self.updated_at_commit.strip():
            raise PreferenceError("updated_at_commit must be nonempty text")


def preference_to_payload(preference: ProjectViewPreference) -> dict[str, object]:
    return {
        "schema_version": VIEW_PREFERENCE_SCHEMA,
        "project_id": preference.project_id,
        "revision": preference.revision,
        "hidden": preference.hidden,
        "updated_at_commit": preference.updated_at_commit,
    }


def preference_from_payload(payload: Mapping[str, object]) -> ProjectViewPreference:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != VIEW_PREFERENCE_SCHEMA:
        raise PreferenceError("saved view preference schema cannot be verified")
    revision = payload.get("revision")
    hidden = payload.get("hidden")
    if type(revision) is not int or revision < 1:
        raise PreferenceError("saved view preference revision is unreadable")
    if type(hidden) is not bool:
        raise PreferenceError("saved view preference hidden flag is unreadable")
    return ProjectViewPreference(
        project_id=_text(payload.get("project_id"), "project_id"),
        revision=revision,
        hidden=hidden,
        updated_at_commit=_text(payload.get("updated_at_commit"), "updated_at_commit"),
    )


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PreferenceError(f"{name} must be nonempty text")
    return value


class PreferenceUnitOfWork(Protocol):
    def stage_record(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
        require_unchanged: bool = False,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class SavedPreference:
    project_id: str
    revision: int
    hidden: bool


def save_view_preference(
    *,
    project_id: str,
    hidden: bool,
    unit_of_work: Any,
    reader: Any,
    expected_revision: int | None,
    commit_sequence: str,
) -> SavedPreference:
    """把隐藏/恢复偏好写成 `project_view_preference` 的新修订。

    只暂存这一条记录：隐藏/恢复**不**改项目、绑定、运行或历史引用，也不停任何运行。
    """

    from aitest.application.planning.substrate import current_record, transaction

    if not isinstance(project_id, str) or not project_id.strip():
        raise PreferenceError("project_id must be nonempty text")
    if type(hidden) is not bool:
        raise PreferenceError("hidden must be a boolean")
    if not isinstance(commit_sequence, str) or not commit_sequence.strip():
        raise PreferenceError("updated_at_commit must be nonempty text")
    # 只有已保存项目才能被隐藏/恢复：偏好不能凭空造项目。
    saved_project = current_record(
        reader, project_id=project_id, aggregate_kind=PROJECT_AGGREGATE, record_id=project_id
    )
    if saved_project is None:
        raise PreferenceError(f"project {project_id} is not saved")
    with transaction(unit_of_work, project_id) as tx:
        revision = (expected_revision or 0) + 1
        staged = tx.stage_record(
            aggregate_kind=VIEW_PREFERENCE_AGGREGATE,
            record_id=project_id,
            expected_revision=expected_revision,
            payload=preference_to_payload(
                ProjectViewPreference(
                    project_id=project_id,
                    revision=revision,
                    hidden=hidden,
                    updated_at_commit=commit_sequence,
                )
            ),
        )
        tx.commit()
    return SavedPreference(
        project_id=project_id,
        revision=getattr(staged, "revision", revision),
        hidden=hidden,
    )


__all__ = [
    "PROJECT_AGGREGATE",
    "PreferenceError",
    "ProjectViewPreference",
    "SavedPreference",
    "VIEW_PREFERENCE_AGGREGATE",
    "VIEW_PREFERENCE_SCHEMA",
    "preference_from_payload",
    "preference_to_payload",
    "save_view_preference",
]
