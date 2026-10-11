"""Unit tests for the local Git source control adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aitest.infrastructure.adapters import source_control as source_control_module
from aitest.infrastructure.adapters.source_control import (
    GitCommandFailed,
    GitHubRef,
    GitSourceControl,
    GitUnavailable,
    _is_not_a_repository,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

    git("init", "-b", "main")
    git(
        "-c", "user.email=test@example.com",
        "-c", "user.name=Test",
        "commit", "--allow-empty", "-m", "init",
    )
    return tmp_path


@pytest.fixture
def control() -> GitSourceControl:
    return GitSourceControl()


def _require_plain_directory_outside_a_repository(
    control: GitSourceControl, plain: Path
) -> None:
    """非仓库分支断言的前提：该目录不在任何 Git 工作树内。

    受限环境里 TEMP 可能不可写，``tempfile`` 会回退到当前目录，于是 pytest 的
    临时目录被放进被测仓库，普通目录事实上位于工作树内。此时如实跳过，不把
    环境前提不成立记成产品失败；环境正常时断言照旧执行。
    """
    if control.is_repository(plain):
        pytest.skip(
            "pytest 临时目录位于某个 Git 工作树内（本机 TEMP 不可写，tempfile 回退到"
            "检出目录），无法构造仓库外的普通目录"
        )


def test_is_available(control: GitSourceControl) -> None:
    assert control.is_available() is True


def test_missing_executable() -> None:
    control = GitSourceControl(executable="git-does-not-exist-xyz")
    assert control.is_available() is False
    with pytest.raises(GitUnavailable):
        control.is_repository(Path.cwd())


def test_is_repository(
    control: GitSourceControl, repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    assert control.is_repository(repo) is True
    plain = tmp_path_factory.mktemp("plain")
    _require_plain_directory_outside_a_repository(control, plain)
    assert control.is_repository(plain) is False


def test_describe(control: GitSourceControl, repo: Path) -> None:
    facts = control.describe(repo)
    assert facts["is_repository"] is True
    assert facts["branch"] == "main"
    assert isinstance(facts["head_commit"], str)
    assert len(facts["head_commit"]) == 40


def test_describe_plain(control: GitSourceControl, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    _require_plain_directory_outside_a_repository(control, plain)
    assert control.describe(plain) == {"is_repository": False}


def test_changes_detects_untracked(control: GitSourceControl, repo: Path) -> None:
    (repo / "new.txt").write_text("hello", encoding="utf-8")
    changes = control.changes(repo)
    assert changes["untracked"] == ["new.txt"]
    assert changes["modified"] == []


def test_changes_detects_modified(control: GitSourceControl, repo: Path) -> None:
    (repo / "tracked.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit", "-m", "add"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    (repo / "tracked.txt").write_text("b", encoding="utf-8")
    changes = control.changes(repo)
    assert changes["modified"] == ["tracked.txt"]


def test_upstream_counts_without_upstream(control: GitSourceControl, repo: Path) -> None:
    result = control.upstream_counts(repo)
    assert result["is_repository"] is True
    assert result["upstream"] is None


def test_github_ref_parse() -> None:
    https = GitHubRef.parse("https://github.com/owner/repo.git")
    assert https is not None
    assert https.owner == "owner" and https.repo == "repo"
    ssh = GitHubRef.parse("git@github.com:owner/repo")
    assert ssh is not None and ssh.repo == "repo"
    assert GitHubRef.parse("https://gitlab.com/owner/repo") is None


def test_changes_parses_rename_nul_double_path(
    control: GitSourceControl, repo: Path
) -> None:
    """git status -z 的 rename 记录是 old\\0new\\0 双路径，必须成对消费。"""
    (repo / "old_name.txt").write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "old_name.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit", "-m", "add"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(["git", "mv", "old_name.txt", "new_name.txt"], cwd=repo, check=True)

    changes = control.changes(repo)

    assert changes["deleted"] == ["old_name.txt"]
    assert changes["added"] == ["new_name.txt"]


def test_not_a_repository_marker_classification() -> None:
    assert _is_not_a_repository(
        GitCommandFailed(
            "fatal: not a git repository (or any of the parent directories): .git",
            returncode=128,
        )
    )
    assert not _is_not_a_repository(
        GitCommandFailed("fatal: detected dubious ownership in repository", returncode=128)
    )
    assert not _is_not_a_repository(GitUnavailable("git: command not found"))


def test_is_repository_propagates_non_repo_failures(
    control: GitSourceControl, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非“不是仓库”类 git 故障不得退化成 is_repository=False。"""
    plain = tmp_path / "plain"
    plain.mkdir()

    def fake_run_git(*args: str, cwd: Path, executable: str, timeout: float = 10.0) -> str:
        if args[:2] == ("rev-parse", "--is-inside-work-tree"):
            raise GitCommandFailed(
                "fatal: detected dubious ownership in repository", returncode=128
            )
        return "git version 2.0\n"

    monkeypatch.setattr(source_control_module, "_run_git", fake_run_git)

    with pytest.raises(GitCommandFailed):
        control.is_repository(plain)
