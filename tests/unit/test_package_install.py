"""Install fallback tests for pip-less virtual environments."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aitest.runners.package_install import install_packages

PYTHON = Path("C:/project/.venv/Scripts/python.exe")
PACKAGES = ["factory-boy>=3.2"]


def _completed(code: int, stdout: bytes = b"", stderr: bytes = b"") -> SimpleNamespace:
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def test_install_uses_pip_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        return _completed(0, b"Successfully installed factory-boy-3.3.3\n")

    monkeypatch.setattr("aitest.runners.package_install.subprocess.run", fake_run)
    result = install_packages(PYTHON, PACKAGES, tmp_path)

    assert result.ok is True
    assert result.installer == "pip"
    assert result.attempts == ["pip"]
    assert calls[0][:4] == [str(PYTHON), "-m", "pip", "install"]


def test_install_falls_back_to_uv_for_uv_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        if command[1:3] == ["-m", "pip"]:
            return _completed(1, stderr=b"No module named pip\n")
        return _completed(0, b"Installed 1 package\n")

    monkeypatch.setattr("aitest.runners.package_install.subprocess.run", fake_run)
    result = install_packages(
        PYTHON,
        PACKAGES,
        tmp_path,
        which=lambda name: r"C:\tools\uv.exe" if name == "uv" else None,
    )

    assert result.ok is True
    assert result.installer == "uv"
    assert result.attempts == ["pip", "uv"]
    assert calls[1][:4] == [
        r"C:\tools\uv.exe",
        "pip",
        "install",
        "--python",
    ]
    assert calls[1][4] == str(PYTHON)
    assert PACKAGES[0] in calls[1]


def test_install_bootstraps_pip_when_uv_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        if command[1:3] == ["-m", "pip"] and len(calls) == 1:
            return _completed(1, stderr=b"No module named pip\n")
        if command[1:3] == ["-m", "ensurepip"]:
            return _completed(0, b"Successfully installed pip\n")
        return _completed(0, b"Successfully installed factory-boy\n")

    monkeypatch.setattr("aitest.runners.package_install.subprocess.run", fake_run)
    result = install_packages(PYTHON, PACKAGES, tmp_path, which=lambda _name: None)

    assert result.ok is True
    assert result.installer == "ensurepip+pip"
    assert result.attempts == ["pip", "ensurepip", "pip-retry"]
    assert calls[1][1:4] == ["-m", "ensurepip", "--upgrade"]


def test_install_does_not_hide_a_real_pip_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        return _completed(1, stderr=b"ERROR: Could not find a version that satisfies\n")

    monkeypatch.setattr("aitest.runners.package_install.subprocess.run", fake_run)
    result = install_packages(
        PYTHON,
        PACKAGES,
        tmp_path,
        which=lambda name: r"C:\tools\uv.exe" if name == "uv" else None,
    )

    assert result.ok is False
    assert result.installer == "pip"
    assert result.attempts == ["pip"]
    assert len(calls) == 1


def test_install_reports_all_failed_bootstraps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command, **_kwargs):
        if command[1:3] == ["-m", "pip"]:
            return _completed(1, stderr=b"No module named pip\n")
        return _completed(1, stderr=b"ensurepip is not available\n")

    monkeypatch.setattr("aitest.runners.package_install.subprocess.run", fake_run)
    result = install_packages(PYTHON, PACKAGES, tmp_path, which=lambda _name: None)

    assert result.ok is False
    assert result.installer == "none"
    assert result.attempts == ["pip", "ensurepip"]
    assert b"ensurepip" in result.output
    assert b"No module named pip" in result.output


def test_install_without_packages_is_a_noop(tmp_path: Path) -> None:
    result = install_packages(PYTHON, [], tmp_path)
    assert result.ok is True
    assert result.installer == "none"
    assert result.attempts == []
