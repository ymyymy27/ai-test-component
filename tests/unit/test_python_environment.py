"""Actual local Python probe and content drift, independent of host acceptance."""

import hashlib
import os
import sys
import venv
from dataclasses import replace
from pathlib import Path

import pytest

from aitest.application.ports import EnvironmentResolutionRequest
from aitest.infrastructure.python_environment import (
    PythonEnvironmentCarrier,
    RegisteredPythonEnvironmentResolver,
)


def sha(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def carrier(tmp_path):
    root = tmp_path / "tested-venv"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(root)
    executable = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    dependencies = root / (
        "Lib/site-packages" if os.name == "nt" else "lib/python3.13/site-packages"
    )
    base = Path(sys._base_executable).resolve()
    return PythonEnvironmentCarrier(
        "project",
        "environment",
        "registered-local-test-carrier",
        executable,
        sha(executable),
        base,
        sha(base),
        (dependencies,),
        venv_root=root,
    )


def request(**changes):
    return replace(
        EnvironmentResolutionRequest("project", "environment", "venv", "Python 3.13"), **changes
    )


def test_actual_registered_python_proves_exact_version_executable_and_stable_dependencies(carrier):
    resolver = RegisteredPythonEnvironmentResolver((carrier,))
    fact = resolver.resolve(request())
    assert fact.resolution.executable_path == str(carrier.executable.resolve())
    assert fact.resolution.executable_digest == sha(carrier.executable)
    assert fact.resolution.base_executable_digest == sha(carrier.base_executable)
    assert fact.resolution.interpreter_version == ".".join(map(str, sys.version_info[:3]))
    assert fact.resolution.probe_policy == "python-isolated-no-site/1"
    assert fact.interpreter_identity == fact.resolution.interpreter_identity
    assert fact.dependency_set_digest == fact.resolution.dependency_set_digest
    assert resolver.resolve(request()) == fact


def test_dependency_bytes_change_identity_even_with_restored_mtime_and_same_size(carrier):
    file = carrier.dependency_roots[0] / "business.py"
    file.write_text("VALUE = 1\n")
    resolver = RegisteredPythonEnvironmentResolver((carrier,))
    first = resolver.resolve(request())
    timestamp = file.stat().st_mtime_ns
    file.write_text("VALUE = 2\n")
    os.utime(file, ns=(timestamp, timestamp))
    second = resolver.resolve(request())
    assert first.dependency_set_digest != second.dependency_set_digest
    assert first.resolution.content_identity != second.resolution.content_identity


@pytest.mark.parametrize(
    "changes",
    [
        {"project_id": "other"},
        {"environment_id": "other"},
        {"isolation_mode": "none"},
        {"interpreter_requirement": "python.exe"},
        {"interpreter_requirement": "3.12"},
        {"interpreter_requirement": "Python 3.13.999"},
    ],
)
def test_unregistered_or_mismatched_request_is_refused(carrier, changes):
    with pytest.raises(ValueError):
        RegisteredPythonEnvironmentResolver((carrier,)).resolve(request(**changes))


def test_default_has_no_implicit_core_interpreter():
    with pytest.raises(ValueError, match="not registered"):
        RegisteredPythonEnvironmentResolver().resolve(request())


@pytest.mark.parametrize("base", [False, True])
def test_changed_registered_binary_is_rejected_before_probe(carrier, monkeypatch, base):
    field = "expected_base_executable_digest" if base else "expected_executable_digest"
    bad = replace(carrier, **{field: "sha256:" + "0" * 64})
    resolver = RegisteredPythonEnvironmentResolver((bad,))
    monkeypatch.setattr(resolver, "_probe", lambda _: pytest.fail("must not start process"))
    with pytest.raises(ValueError, match="content changed"):
        resolver.resolve(request())


@pytest.mark.parametrize("text", ["import malicious\n", "../external-source\n"])
def test_pth_is_not_executed_or_claimed_as_verified(carrier, monkeypatch, text):
    (carrier.dependency_roots[0] / "editable.pth").write_text(text)
    resolver = RegisteredPythonEnvironmentResolver((carrier,))
    monkeypatch.setattr(resolver, "_probe", lambda _: pytest.fail("must not start process"))
    with pytest.raises(ValueError, match="explicit loading mapping"):
        resolver.resolve(request())


def test_system_site_packages_mixed_into_venv_is_refused(carrier):
    config = carrier.venv_root / "pyvenv.cfg"
    config.write_text(
        config.read_text().replace(
            "include-system-site-packages = false", "include-system-site-packages = true"
        )
    )
    with pytest.raises(ValueError, match="unregistered system"):
        RegisteredPythonEnvironmentResolver((carrier,)).resolve(request())


def test_missing_root_and_byte_limits_do_not_produce_incomplete_digest(carrier):
    resolver = RegisteredPythonEnvironmentResolver((carrier,), max_bytes=1)
    with pytest.raises(ValueError, match="byte limit"):
        resolver.resolve(request())
    missing = replace(carrier, dependency_roots=(carrier.venv_root / "missing",))
    with pytest.raises(ValueError, match="do not match the venv"):
        RegisteredPythonEnvironmentResolver((missing,)).resolve(request())


def test_probe_does_not_import_project_or_ambient_sitecustomize(carrier, tmp_path, monkeypatch):
    malicious = tmp_path / "ambient"
    malicious.mkdir()
    marker = tmp_path / "executed"
    (malicious / "sitecustomize.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(malicious))
    monkeypatch.setenv("PYTHONHOME", str(malicious))
    RegisteredPythonEnvironmentResolver((carrier,)).resolve(request())
    assert not marker.exists()


def test_environment_mutation_during_probe_is_rejected(carrier, monkeypatch):
    resolver = RegisteredPythonEnvironmentResolver((carrier,))
    original = resolver._probe

    def mutate(executable):
        facts = original(executable)
        (carrier.dependency_roots[0] / "new.py").write_text("changed = True\n")
        return facts

    monkeypatch.setattr(resolver, "_probe", mutate)
    with pytest.raises(ValueError, match="changed while resolving"):
        resolver.resolve(request())


def test_file_count_limit_refuses_an_incomplete_dependency_manifest(carrier):
    for name in ("one.py", "two.py"):
        (carrier.dependency_roots[0] / name).write_text("VALUE = 1\n")
    with pytest.raises(ValueError, match="scan exceeds"):
        RegisteredPythonEnvironmentResolver((carrier,), max_files=1).resolve(request())


@pytest.mark.parametrize(
    "script,message",
    [
        ("print('x' * 20000)", "exceeded output limit"),
        ("print('invalid-json')", "invalid safe facts"),
    ],
)
def test_probe_output_is_bounded_and_never_published_raw(carrier, monkeypatch, script, message):
    import aitest.infrastructure.python_environment as module

    monkeypatch.setattr(module, "_PROBE", script)
    with pytest.raises(ValueError, match=message):
        RegisteredPythonEnvironmentResolver((carrier,)).resolve(request())


def test_probe_timeout_stops_the_probe_without_publishing_a_fact(carrier, monkeypatch):
    import aitest.infrastructure.python_environment as module

    monkeypatch.setattr(module, "_PROBE", "import time; time.sleep(3)")
    with pytest.raises(ValueError, match="timed out"):
        RegisteredPythonEnvironmentResolver((carrier,), timeout_seconds=0.1).resolve(request())


def test_base_path_mismatch_is_refused_before_reading_unregistered_binary(carrier, monkeypatch):
    resolver = RegisteredPythonEnvironmentResolver((carrier,))
    original = resolver._probe

    def unknown_base(executable):
        facts = original(executable)
        facts["base_executable"] = str(carrier.venv_root / "unregistered.exe")
        return facts

    monkeypatch.setattr(resolver, "_probe", unknown_base)
    with pytest.raises(ValueError, match="differs from the registered carrier"):
        resolver.resolve(request())
