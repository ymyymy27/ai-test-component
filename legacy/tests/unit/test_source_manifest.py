import pytest

from aitest.domain.project.context import SourceFileDigest, SourceManifest


def _file(relative_path: str = "src/app.py", **overrides: object) -> SourceFileDigest:
    base: dict[str, object] = {
        "relative_path": relative_path,
        "size": 12,
        "content_digest": "sha256:abc",
    }
    return SourceFileDigest(**(base | overrides))  # type: ignore[arg-type]


def _manifest(**overrides: object) -> SourceManifest:
    base: dict[str, object] = {
        "source_scope": "选定模块目录",
        "manifest_digest": "sha256:manifest",
        "files": (_file(),),
    }
    return SourceManifest(**(base | overrides))  # type: ignore[arg-type]


def test_content_digest_is_required() -> None:
    with pytest.raises(ValueError, match="content_digest"):
        _file(content_digest="  ")


def test_manifest_requires_scope_and_digest() -> None:
    with pytest.raises(ValueError, match="source_scope"):
        _manifest(source_scope="")
    with pytest.raises(ValueError, match="manifest_digest"):
        _manifest(manifest_digest=" ")


def test_relative_paths_must_stay_inside_the_scope() -> None:
    with pytest.raises(ValueError, match="inside the source scope"):
        _file(relative_path=r"C:\outside\app.py")
    with pytest.raises(ValueError, match="inside the source scope"):
        _file(relative_path="../outside.py")


def test_manifest_rejects_duplicate_paths() -> None:
    with pytest.raises(ValueError, match="relative_path"):
        _manifest(files=(_file(), _file()))


def test_size_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="size"):
        _file(size=-1)


def test_mtime_is_only_a_hint_and_may_be_absent() -> None:
    """元数据只是变化提示；内容身份来自实际字节摘要。"""
    digest = _file()
    assert digest.mtime_hint is None
    assert _file(mtime_hint="2026-09-25T00:00:00Z").mtime_hint is not None


def test_refetch_dependencies_and_scope_are_recorded_explicitly() -> None:
    manifest = _manifest(
        refetch_dependencies=("git-lfs:models/*.bin", "submodule:vendor/lib"),
        refetch_scope="vendor/lib 可复取；大文件依赖远端可用性",
    )
    assert manifest.refetch_dependencies
    assert manifest.refetch_scope is not None


def test_missing_refetch_scope_stays_a_visible_gap() -> None:
    """缺失即缺口，不留空冒充完整。"""
    assert _manifest().refetch_scope is None


def test_manifest_rejects_empty_exclusion_rule() -> None:
    with pytest.raises(ValueError, match="exclusion_rules"):
        _manifest(exclusion_rules=("",))
