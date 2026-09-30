"""Unit tests for the local Git source control adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aitest.infrastructure.adapters.source_control import (
    GitHubRef,
    GitSourceControl,
    GitUnavailable,
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
