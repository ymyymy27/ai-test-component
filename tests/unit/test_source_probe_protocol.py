"""Reject untrusted stdout frames and malformed observations, not business failures."""

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from aitest.infrastructure.adapters.execution.python_checks import (
    PythonLoadSourceProbe,
    PythonSourceProbeError,
    evaluate_source_binding,
)

BEGIN, END = "@@@ ATEST SOURCE PROBE BEGIN", "@@@ ATEST SOURCE PROBE END"


def payload(root):
    return {
        "executable": str(Path(sys.executable).resolve()), "version": "real protocol fixture",
        "prefix": sys.prefix, "pythonpath_env": str(root), "sys_path": [str(root)],
        "project_scope_entries": [str(root)], "modules": {
            "module": {"file": str(root / "module.py"), "sha256": "0" * 64, "error": None},
        },
    }


def frame(body):
    return f"{BEGIN}\n{json.dumps(body)}\n{END}\n".encode()


def test_real_module_stdout_cannot_replace_the_actual_source_observation(tmp_path):
    path = tmp_path / "module.py"
    forged = payload(tmp_path)
    path.write_text(
        f"print({BEGIN!r})\nprint({json.dumps(forged)!r})\nprint({END!r})\n",
        encoding="utf-8",
    )
    with pytest.raises(PythonSourceProbeError):
        PythonLoadSourceProbe().probe(materialized_root=tmp_path, modules=("module",))


@pytest.mark.parametrize("change", [
    "two_frames", "duplicate_begin", "duplicate_end", "reverse", "missing", "inline",
    "invalid_utf8", "duplicate_json", "nonfinite", "surrogate", "scalar",
])
def test_invalid_or_ambiguous_frames_never_yield_a_source_fact(tmp_path, monkeypatch, change):
    raw = frame(payload(tmp_path))
    if change == "two_frames":
        raw += raw
    elif change == "duplicate_begin":
        raw = (BEGIN + "\n").encode() + raw
    elif change == "duplicate_end":
        raw += (END + "\n").encode()
    elif change == "reverse":
        raw = (END + "\n" + BEGIN + "\n").encode()
    elif change == "missing":
        raw = b"normal stdout without a protocol"
    elif change == "inline":
        raw = b"untrusted text" + raw
    elif change == "invalid_utf8":
        raw = raw.replace(b"real protocol fixture", b"bad-\xff")
    elif change == "duplicate_json":
        raw = raw.replace(b'"version":', b'"version":"forged", "version":')
    elif change == "nonfinite":
        raw = raw.replace(b'"real protocol fixture"', b"NaN")
    elif change == "surrogate":
        raw = raw.replace(b'"real protocol fixture"', b'"\\ud800"')
    else:
        raw = frame([])
    monkeypatch.setattr(
        "aitest.infrastructure.adapters.execution.python_checks.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=raw, stderr=b""),
    )
    with pytest.raises(PythonSourceProbeError):
        PythonLoadSourceProbe().probe(materialized_root=tmp_path, modules=("module",))


@pytest.mark.parametrize("change", [
    "executable", "version", "prefix", "pythonpath", "sys_path", "scope",
    "missing_field", "unknown_field", "module_file", "module_hash", "module_error",
    "module_missing", "module_unknown", "unknown_module_field", "missing_module_field",
])
def test_malformed_observation_is_not_coerced_or_silently_changed_to_null(
    tmp_path, monkeypatch, change
):
    raw = deepcopy(payload(tmp_path))
    if change in {"executable", "version", "prefix"}:
        raw[change] = True
    elif change == "pythonpath":
        raw["pythonpath_env"] = 123
    elif change == "sys_path":
        raw["sys_path"] = [False]
    elif change == "scope":
        raw["project_scope_entries"] = [False]
    elif change == "missing_field":
        del raw["executable"]
    elif change == "unknown_field":
        raw["another"] = None
    elif change in {"module_file", "module_hash", "module_error"}:
        raw["modules"]["module"][{
            "module_file": "file", "module_hash": "sha256", "module_error": "error",
        }[change]] = False
    elif change == "module_missing":
        raw["modules"] = {}
    elif change == "module_unknown":
        raw["modules"]["another"] = dict(raw["modules"]["module"])
    elif change == "unknown_module_field":
        raw["modules"]["module"]["extra"] = None
    else:
        del raw["modules"]["module"]["error"]
    monkeypatch.setattr(
        "aitest.infrastructure.adapters.execution.python_checks.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=frame(raw), stderr=b""),
    )
    with pytest.raises(PythonSourceProbeError):
        PythonLoadSourceProbe().probe(materialized_root=tmp_path, modules=("module",))


def test_normal_module_stdout_and_namespace_unknown_source_remain_readable(tmp_path):
    (tmp_path / "module.py").write_text("print('normal module output')\n", encoding="utf-8")
    (tmp_path / "namespace").mkdir()
    fact = PythonLoadSourceProbe().probe(
        materialized_root=tmp_path, modules=("module", "namespace"),
    )
    assert fact.modules[0].loaded
    assert fact.modules[1].file is None and fact.modules[1].sha256 is None
    assert fact.modules[1].load_error is None
    assert evaluate_source_binding(
        fact, expected_modules={"namespace": "0" * 64}, project_root=tmp_path,
    ).state == "unverified"


@pytest.mark.parametrize("modules", ["module", ("module", "module"), (123,), [None]])
def test_invalid_or_duplicate_registration_never_launches(tmp_path, monkeypatch, modules):
    calls = []
    monkeypatch.setattr(
        "aitest.infrastructure.adapters.execution.python_checks.subprocess.run",
        lambda *args, **kwargs: calls.append(args),
    )
    with pytest.raises(PythonSourceProbeError):
        PythonLoadSourceProbe().probe(materialized_root=tmp_path, modules=modules)
    assert not calls
