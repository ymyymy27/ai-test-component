"""项目与绑定的 payload 序列化：不适用键真正省略且可往返。

依据：`docs/文档-feix-a/B包/10-准备意图与幂等规则设计说明.md` 第 3.7 节；
需求 P1-AC25（"界面与报告中不出现仓库、分支、提交、远端任何内容，也不显示为空值或未知"）。
"""

import json

import pytest

from aitest.application.project.serialization import (
    GIT_ONLY_KEYS,
    PLAIN_ONLY_KEYS,
    binding_from_payload,
    binding_to_payload,
    project_from_payload,
    project_to_payload,
)
from aitest.domain.project.context import (
    BindingForm,
    ImplementationStatus,
    LocalProject,
    LocalProjectBinding,
    Module,
)


def _git_binding(**overrides: object) -> LocalProjectBinding:
    base: dict[str, object] = {
        "binding_id": "binding-1",
        "binding_revision": 1,
        "project_id": "p1",
        "canonical_path": r"C:\work\project",
        "binding_form": BindingForm.GIT,
        "repository_id": "origin",
        "branch": "main",
        "base_commit": "abc123",
    }
    base.update(overrides)
    return LocalProjectBinding(**base)  # type: ignore[arg-type]


def _plain_binding(**overrides: object) -> LocalProjectBinding:
    base: dict[str, object] = {
        "binding_id": "binding-1",
        "binding_revision": 1,
        "project_id": "p1",
        "canonical_path": r"C:\work\plain",
        "binding_form": BindingForm.PLAIN,
        "manifest_digest": "deadbeef",
    }
    base.update(overrides)
    return LocalProjectBinding(**base)  # type: ignore[arg-type]


def _module(module_id: str = "m1", **overrides: object) -> Module:
    base: dict[str, object] = {
        "module_id": module_id,
        "project_id": "p1",
        "name": "core",
        "responsibility": "compute",
        "interface_note": "pure functions",
        "inputs": ("numbers",),
        "outputs": ("sum",),
    }
    base.update(overrides)
    return Module(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------- 不适用键真正省略


def test_git_payload_omits_the_plain_manifest_key() -> None:
    payload = binding_to_payload(_git_binding())
    assert PLAIN_ONLY_KEYS[0] not in payload


def test_plain_payload_omits_every_repository_key() -> None:
    payload = binding_to_payload(_plain_binding())
    for key in GIT_ONLY_KEYS:
        assert key not in payload


def test_omitted_keys_are_absent_not_null() -> None:
    """关键点：不是 `key: None`，而是**键根本不存在**。"""
    payload = binding_to_payload(_plain_binding())
    assert "repository_id" not in payload
    assert set(payload) & set(GIT_ONLY_KEYS) == set()


def test_payload_carries_only_the_applicable_form_keys() -> None:
    git_payload = binding_to_payload(_git_binding())
    plain_payload = binding_to_payload(_plain_binding())
    assert set(git_payload) & set(GIT_ONLY_KEYS) == set(GIT_ONLY_KEYS)
    assert set(plain_payload) & set(PLAIN_ONLY_KEYS) == set(PLAIN_ONLY_KEYS)
    assert set(git_payload) & set(PLAIN_ONLY_KEYS) == set()


def test_absent_owner_key_is_omitted_rather_than_null() -> None:
    assert "local_owner" not in binding_to_payload(_git_binding())
    assert (
        binding_to_payload(_git_binding(local_owner="feix-a"))["local_owner"] == "feix-a"
    )


def test_binding_form_is_written_as_its_value() -> None:
    assert binding_to_payload(_git_binding())["binding_form"] == "git"
    assert binding_to_payload(_plain_binding())["binding_form"] == "plain"


# ------------------------------------------------------------- 往返


def test_git_binding_round_trips() -> None:
    binding = _git_binding(local_owner="feix-a", confirmed=True)
    assert binding_from_payload(binding_to_payload(binding)) == binding


def test_plain_binding_round_trips() -> None:
    binding = _plain_binding(confirmed=True)
    assert binding_from_payload(binding_to_payload(binding)) == binding


def test_payload_is_json_serialisable() -> None:
    binding = _plain_binding()
    encoded = json.dumps(binding_to_payload(binding), ensure_ascii=False)
    assert json.loads(encoded)["binding_form"] == "plain"


# ------------------------------------------------------------- 反向解析的拒绝


def test_reading_a_git_payload_with_a_manifest_key_is_rejected() -> None:
    payload = binding_to_payload(_git_binding())
    payload["manifest_digest"] = "deadbeef"
    with pytest.raises(ValueError, match="must omit manifest_digest"):
        binding_from_payload(payload)


def test_reading_a_plain_payload_with_a_repository_key_is_rejected() -> None:
    payload = binding_to_payload(_plain_binding())
    payload["branch"] = "main"
    with pytest.raises(ValueError, match="must omit branch"):
        binding_from_payload(payload)


def test_missing_form_key_is_rejected_rather_than_filled_with_null() -> None:
    """存时省略、读时补空这条不一致路径必须被抓到。"""
    payload = binding_to_payload(_plain_binding())
    del payload["manifest_digest"]
    with pytest.raises(ValueError, match="requires manifest_digest"):
        binding_from_payload(payload)


def test_null_form_key_is_rejected() -> None:
    payload = binding_to_payload(_git_binding())
    payload["branch"] = None
    with pytest.raises(ValueError, match="requires branch"):
        binding_from_payload(payload)


def test_unknown_binding_form_is_rejected() -> None:
    payload = binding_to_payload(_git_binding())
    payload["binding_form"] = "svn"
    with pytest.raises(ValueError, match="unknown binding_form"):
        binding_from_payload(payload)


def test_path_traversal_in_payload_is_rejected() -> None:
    payload = binding_to_payload(_git_binding())
    payload["canonical_path"] = r"C:\work\..\escape"
    with pytest.raises(ValueError):
        binding_from_payload(payload)


def test_non_integer_binding_revision_is_rejected() -> None:
    payload = binding_to_payload(_git_binding())
    payload["binding_revision"] = "1"
    with pytest.raises(ValueError, match="binding_revision"):
        binding_from_payload(payload)


# ------------------------------------------------------------- 项目 payload


def test_project_payload_round_trips_with_modules() -> None:
    project = LocalProject(
        local_project_id="p1",
        workspace_id="w1",
        name="demo",
        goal="verify",
        created_at_commit="commit-1",
        modules=(_module("m1"), _module("m2", name="api", owner="feix-a")),
    )
    assert project_from_payload(project_to_payload(project)) == project


def test_project_payload_uses_the_business_alias_for_project_id() -> None:
    project = LocalProject(
        local_project_id="p1",
        workspace_id="w1",
        name="demo",
        goal="verify",
        created_at_commit="commit-1",
    )
    payload = project_to_payload(project)
    assert payload["project_id"] == payload["local_project_id"] == "p1"


def test_module_owner_is_omitted_when_absent() -> None:
    project = LocalProject(
        local_project_id="p1",
        workspace_id="w1",
        name="demo",
        goal="verify",
        created_at_commit="commit-1",
        modules=(_module("m1"),),
    )
    payload = project_to_payload(project)
    assert "owner" not in payload["modules"][0]


def test_module_implementation_status_round_trips() -> None:
    project = LocalProject(
        local_project_id="p1",
        workspace_id="w1",
        name="demo",
        goal="verify",
        created_at_commit="commit-1",
        modules=(
            _module("m1", implementation_status=ImplementationStatus.COMPLETE),
        ),
    )
    restored = project_from_payload(project_to_payload(project))
    assert restored.modules[0].implementation_status is ImplementationStatus.COMPLETE


def test_unknown_module_status_is_rejected() -> None:
    payload = project_to_payload(
        LocalProject(
            local_project_id="p1",
            workspace_id="w1",
            name="demo",
            goal="verify",
            created_at_commit="commit-1",
            modules=(_module("m1"),),
        )
    )
    payload["modules"][0]["implementation_status"] = "done"
    with pytest.raises(ValueError, match="unknown implementation_status"):
        project_from_payload(payload)


def test_project_without_modules_round_trips() -> None:
    project = LocalProject(
        local_project_id="p1",
        workspace_id="w1",
        name="demo",
        goal="verify",
        created_at_commit="commit-1",
    )
    assert project_from_payload(project_to_payload(project)) == project
