"""源码内容身份：形式互斥与 `content_identity` 计算口径。

依据：`AB-001` 第 11.1 节（形式互斥）、第 11.3 节（`content_identity` 计算口径，
**B 主责**）；检查文档 B-04"实际变更来源与正式 `SourceSnapshot` 转换未装配"。

本文件逐条锁定合同口径：

1. **形式互斥**：`git` 必须有基准提交＋差异摘要、不得有清单摘要；`plain` 反之；
   另一形态的字段**必须省略**而不是给空值；
2. **身份输入**：`source_form`、按路径升序规范化的 `files`、形态身份；
3. **不参与计算**：`mtime_hint`、`source_scope`、`exclusion_rules`、
   `refetch_*`、`snapshot_id`、`purpose`、目录遍历顺序；
4. **可复现**：同一输入必须得到同一 `content_identity`（夹具据此做逐字节断言）。
"""

from __future__ import annotations

import pytest

from aitest.domain.project.context import (
    BindingForm,
    SourceFileDigest,
    SourceForm,
    SourceManifest,
    source_content_identity,
)


def _file(relative_path: str = "src/app.py", **overrides: object) -> SourceFileDigest:
    base: dict[str, object] = {
        "relative_path": relative_path,
        "size": 12,
        "content_digest": "sha256:abc",
    }
    return SourceFileDigest(**(base | overrides))  # type: ignore[arg-type]


def _plain(**overrides: object) -> SourceManifest:
    base: dict[str, object] = {
        "source_scope": "选定模块目录",
        "source_form": SourceForm.PLAIN,
        "manifest_digest": "sha256:manifest",
        "files": (_file(),),
    }
    return SourceManifest(**(base | overrides))  # type: ignore[arg-type]


def _git(**overrides: object) -> SourceManifest:
    base: dict[str, object] = {
        "source_scope": "选定模块目录",
        "source_form": SourceForm.GIT,
        "git_base_commit": "abc123",
        "git_diff_digest": "sha256:diff",
        "files": (_file(),),
    }
    return SourceManifest(**(base | overrides))  # type: ignore[arg-type]


# ------------------------------------------------------------------ 形式互斥


def test_source_form_values_match_binding_form() -> None:
    """源码快照形态与绑定形态取值一致（第 11.1 节"同一模式"）。"""
    assert {item.value for item in SourceForm} == {item.value for item in BindingForm}


def test_git_form_requires_base_commit_and_diff_digest() -> None:
    with pytest.raises(ValueError, match="git_base_commit"):
        _git(git_base_commit=None)
    with pytest.raises(ValueError, match="git_diff_digest"):
        _git(git_diff_digest=None)


def test_git_form_must_omit_the_manifest_digest() -> None:
    """另一形态的字段必须**省略**，不是给空值。"""
    with pytest.raises(ValueError, match="must not carry a manifest digest"):
        _git(manifest_digest="sha256:manifest")


def test_plain_form_requires_the_manifest_digest() -> None:
    with pytest.raises(ValueError, match="requires manifest_digest"):
        _plain(manifest_digest=None)


def test_plain_form_must_omit_git_fields() -> None:
    with pytest.raises(ValueError, match="must omit git_base_commit"):
        _plain(git_base_commit="abc123")
    with pytest.raises(ValueError, match="must omit git_diff_digest"):
        _plain(git_diff_digest="sha256:diff")


def test_the_default_form_is_plain() -> None:
    """默认 `plain`：既有调用方不传形态时的行为不变。"""
    manifest = SourceManifest(source_scope="s", manifest_digest="sha256:m")
    assert manifest.source_form is SourceForm.PLAIN


# ------------------------------------------------------------------ 身份计算


def test_the_same_input_gives_the_same_identity() -> None:
    """可复现：同一输入必须得到同一身份（夹具据此逐字节断言）。"""
    assert source_content_identity(_plain()) == source_content_identity(_plain())
    assert source_content_identity(_git()) == source_content_identity(_git())


def test_the_identity_is_path_order_independent() -> None:
    """目录遍历顺序不参与身份：文件清单的顺序不同不改变身份。"""
    forward = _plain(files=(_file("a.py"), _file("b.py"), _file("c.py")))
    shuffled = _plain(files=(_file("c.py"), _file("a.py"), _file("b.py")))
    assert source_content_identity(forward) == source_content_identity(shuffled)


def test_the_identity_changes_when_file_content_changes() -> None:
    """任一条目的内容摘要变化必须改变身份（这是"依据变了"的判据）。"""
    before = _plain()
    after = _plain(files=(_file(content_digest="sha256:changed"),))
    assert source_content_identity(before) != source_content_identity(after)


def test_the_identity_changes_when_file_size_changes() -> None:
    before = _plain()
    after = _plain(files=(_file(size=13),))
    assert source_content_identity(before) != source_content_identity(after)


def test_the_identity_changes_when_a_file_is_added_or_removed() -> None:
    """增删文件改变身份——A-15 的"整目录沿用单文件范围"正是靠这条判出来。"""
    one = _plain(files=(_file("a.py"),))
    two = _plain(files=(_file("a.py"), _file("b.py")))
    assert source_content_identity(one) != source_content_identity(two)


def test_the_two_forms_never_share_an_identity() -> None:
    """形态身份参与计算：`git` 与 `plain` 的内容身份不会因巧合相同。"""
    assert source_content_identity(_plain()) != source_content_identity(_git())


def test_an_empty_manifest_still_has_an_identity() -> None:
    """空清单也是合法输入：身份由形态身份决定，不能崩。"""
    identity = source_content_identity(_plain(files=()))
    assert identity.startswith("sha256:")
    assert identity == source_content_identity(_plain(files=()))


# ------------------------------------------------------------------ 不参与计算的字段


def test_mtime_is_never_part_of_the_identity() -> None:
    """`mtime` **只作变化提示，永不参与身份计算**（第 11.3 节第 3 条）。"""
    without = _plain(files=(_file(),))
    with_hint = _plain(files=(_file(mtime_hint="2026-10-03T00:00:00Z"),))
    assert source_content_identity(without) == source_content_identity(with_hint)


def test_scope_and_exclusions_are_not_part_of_the_identity() -> None:
    """范围声明不进身份：它的影响已经体现在"哪些文件在清单里"。

    若把 `source_scope` 算进身份，同一批文件只因范围描述不同就会得到两个身份，
    与"内容身份"的语义不符。
    """
    base = _plain()
    renamed_scope = _plain(source_scope="另一个描述")
    with_exclusions = _plain(exclusion_rules=("**/*.md",))
    assert source_content_identity(base) == source_content_identity(renamed_scope)
    assert source_content_identity(base) == source_content_identity(with_exclusions)


def test_refetch_metadata_is_not_part_of_the_identity() -> None:
    """复取依赖与复取范围描述"能不能再取到"，不是"内容是什么"。"""
    base = _plain()
    with_refetch = _plain(
        refetch_dependencies=("git-lfs:models/*.bin",),
        refetch_scope="大文件依赖远端可用性",
    )
    assert source_content_identity(base) == source_content_identity(with_refetch)


def test_the_identity_changes_when_the_form_identity_changes() -> None:
    """形态身份（基准提交／差异摘要／清单摘要）参与计算。"""
    assert source_content_identity(_git()) != source_content_identity(
        _git(git_base_commit="def456")
    )
    assert source_content_identity(_plain()) != source_content_identity(
        _plain(manifest_digest="sha256:other")
    )


# ------------------------------------------------------------------ 规范化


def test_normalized_files_sorts_by_relative_path() -> None:
    manifest = _plain(files=(_file("b.py"), _file("a.py")))
    assert [item.relative_path for item in manifest.normalized_files()] == ["a.py", "b.py"]


def test_form_identity_is_explicit_about_the_form() -> None:
    assert _plain().form_identity() == "plain:sha256:manifest"
    assert _git().form_identity() == "git:abc123:sha256:diff"


def test_the_identity_is_a_real_digest() -> None:
    identity = source_content_identity(_plain())
    assert identity.startswith("sha256:")
    assert len(identity) == len("sha256:") + 64
