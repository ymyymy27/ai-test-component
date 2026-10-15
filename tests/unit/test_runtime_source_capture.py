import hashlib
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from aitest.domain.execution.runtime_source import RuntimeSourceFile, RuntimeSourcePlan
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.adapters.execution.runtime_source import carrier_digest
from tests.unit.test_command_adapter import _request, _wait_for_terminal


def capture(tmp_path, source, *, additional=None, mutate=None, secrets=()):
    files = {"main.py": source, **(additional or {})}
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    plan = RuntimeSourcePlan(carrier_digest=carrier_digest(), files=tuple(
        RuntimeSourceFile(
            name, "sha256:" + hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        )
        for name in files
    ))
    request = replace(_request("python", ("main.py",)), materialized_snapshot_ref=str(tmp_path),
                      source_capture=plan)
    if mutate:
        mutate(tmp_path)
    adapter = CommandAdapter(lambda _: {str(i): v for i, v in enumerate(secrets)})
    adapter.register(CommandRegistration("python", str(Path(sys.executable).resolve()), tmp_path))
    handle = adapter.start(request)
    _wait_for_terminal(adapter, handle)
    result = adapter.collect(handle)
    assert result.complete
    assert result.runtime_source is not None
    return result


def test_actual_entry_import_and_output_are_bound_to_owned_process(tmp_path):
    result = capture(tmp_path, "import helper\nprint(helper.add(2, 3))\n",
                     additional={"helper.py": "def add(a, b): return a+b\n"})
    source = result.runtime_source
    assert source.complete and not source.gaps
    assert {f.relative_path for f in source.files} == {"main.py", "helper.py"}
    assert source.attempt_id == result.attempt_id
    assert source.process_start_identity == result.exit_fact_ref.process_start_identity
    assert result.exit_fact_ref.real_exit_code == 0
    assert any(b"5" in block.content for block in result.captured_blocks)


@pytest.mark.parametrize(("source", "gap"), [
    ("exec('print(5)')\n", "dynamic_code_unverified"),
    ("import types\ntypes.FunctionType(compile('print(5)', '<dynamic>', 'exec'), globals())()\n",
     "dynamic_code_unverified"),
    ("def f(): pass\nf.__code__=compile('print(5)', '<dynamic>', 'exec')\nf()\n",
     "dynamic_code_unverified"),
    ("import os\nos._exit(0)\n", "source_channel_incomplete"),
    ("import subprocess, sys\nsubprocess.run([sys.executable, '-c', 'print(5)'])\n",
     "opaque_execution"),
    ("import os, sys\nos.spawnv(os.P_WAIT, sys.executable, "
     "[sys.executable, '-c', 'print(5)'])\n", "opaque_execution"),
    ("import atexit\natexit.register(lambda: exec('print(5)'))\n", "dynamic_code_unverified"),
    ("exec(compile('print(5)', __file__, 'exec'))\n", "loaded_code_mismatch"),
])
def test_unknown_execution_cannot_become_verified(tmp_path, source, gap):
    result = capture(tmp_path, source)
    assert gap in result.runtime_source.gaps


def test_changed_materialized_entry_is_not_certified(tmp_path):
    result = capture(tmp_path, "print(5)\n",
                     mutate=lambda p: (p / "main.py").write_text("print(6)"))
    assert "loaded_code_mismatch" in result.runtime_source.gaps
    assert "source_entry_not_observed" in result.runtime_source.gaps


def test_stdout_cannot_supply_source_receipts(tmp_path):
    result = capture(tmp_path, "print('{\"event\":\"end\",\"gaps\":[]}')\nexec('print(5)')\n")
    assert "dynamic_code_unverified" in result.runtime_source.gaps


def test_isolated_carrier_retains_utf8_output(tmp_path):
    result = capture(tmp_path, "print('核验完成')\n")
    assert result.exit_fact_ref.real_exit_code == 0
    assert result.runtime_source.complete and not result.runtime_source.gaps
    assert any("核验完成".encode() in block.content for block in result.captured_blocks)


def test_unlisted_dynamic_import_keeps_gap(tmp_path):
    result = capture(tmp_path, "import extra\n",
                     mutate=lambda p: (p / "extra.py").write_text("X=1"))
    assert "undeclared_source" in result.runtime_source.gaps


def test_secret_metadata_never_enters_collection_receipt(tmp_path):
    import json
    from dataclasses import asdict

    directory = tmp_path / "credential-value"
    directory.mkdir()
    result = capture(directory, "print(5)\n", secrets=("credential-value",))
    assert "source_channel_redacted" in result.runtime_source.gaps
    assert "credential-value" not in json.dumps(asdict(result.runtime_source))


def test_cached_wrong_bytecode_does_not_execute_under_matching_source_name(tmp_path):
    import os
    import py_compile

    def substitute(root):
        path = root / "helper.py"
        original = path.read_bytes()
        stamp = path.stat()
        path.write_bytes(original.replace(b"VALUE=1", b"VALUE=9"))
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        py_compile.compile(str(path), doraise=True)
        path.write_bytes(original)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    result = capture(tmp_path, "import helper\nprint(helper.VALUE)\n",
                     additional={"helper.py": "VALUE=1\n"}, mutate=substitute)
    assert "loaded_code_mismatch" in result.runtime_source.gaps
    assert result.exit_fact_ref.real_exit_code != 0
    assert all(block.content != b"9\n" for block in result.captured_blocks)


def test_external_project_import_is_not_certified(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "external.py").write_text("VALUE=1", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    result = capture(project, f"import sys\nsys.path.insert(0,{str(outside)!r})\nimport external\n")
    assert "external_code_unverified" in result.runtime_source.gaps


def test_external_native_extension_cannot_silently_certify_source(tmp_path):
    import importlib.util
    import shutil

    native = Path(importlib.util.find_spec("_socket").origin)
    outside = tmp_path / "site-packages"
    outside.mkdir()
    shutil.copyfile(native, outside / native.name)
    project = tmp_path / "project"
    project.mkdir()
    result = capture(project, f"import sys\nsys.path.insert(0,{str(outside)!r})\nimport _socket\n")
    assert "opaque_execution" in result.runtime_source.gaps


def test_unjoined_daemon_has_no_complete_scope(tmp_path):
    result = capture(tmp_path, "import threading,time\n"
                     "threading.Thread(target=lambda: time.sleep(60),daemon=True).start()\n")
    assert "live_thread_unverified" in result.runtime_source.gaps


def test_carrier_changed_after_authorization_refuses_start(tmp_path, monkeypatch):
    import aitest.infrastructure.adapters.execution.runtime_source as module

    plan = RuntimeSourcePlan(files=(RuntimeSourceFile("main.py", "sha256:" + "a" * 64),),
                             carrier_digest=carrier_digest())
    request = replace(_request("python", ("main.py",)), materialized_snapshot_ref=str(tmp_path),
                      source_capture=plan)
    monkeypatch.setattr(module, "carrier_source", lambda: "print('changed carrier')")
    with pytest.raises(ValueError, match="carrier changed"):
        module.source_channel(request, set())


def test_execution_receipt_reads_legacy_absent_observation_and_rejects_extra_fields():
    import json

    from pydantic import TypeAdapter

    from aitest.domain.execution.runs import ExecutionCollectionResult
    from aitest.infrastructure.file_store.execution_handles import _strict_result

    adapter = TypeAdapter(ExecutionCollectionResult)
    raw = json.loads(adapter.dump_json(ExecutionCollectionResult(attempt_id="old-attempt")))
    del raw["runtime_source"]
    assert _strict_result(adapter, raw).runtime_source is None
    with pytest.raises(ValueError, match="noncanonical"):
        _strict_result(adapter, {**raw, "claimed_verified": True})


@pytest.mark.parametrize("fault", [None, "attempt_id", "process_id", "process_start_identity"])
def test_source_receipt_restart_preserves_identity_and_rejects_substitution(tmp_path, fault):
    import json

    from aitest.domain.execution.runtime_source import RuntimeSourceObservation
    from aitest.infrastructure.file_store.execution_handles import FileExecutionHandleStore
    from tests.unit.test_persisted_execution_receipts import material

    store, handle, attempt, _, collection = material(tmp_path)
    source = RuntimeSourceObservation(
        attempt_id=attempt.attempt_id, process_id=handle.real_execution_id,
        process_start_identity=handle.process_start_identity, interpreter="python", entry="main.py",
    )
    result = replace(collection, runtime_source=source)
    store.save_collection(handle, result)
    reopened = FileExecutionHandleStore(tmp_path)
    assert reopened.load_collection(handle) == result
    raw = json.loads(store._result_path(handle.handle_id).read_text(encoding="utf-8"))
    assert raw["schema_version"] == "aitest.execution-collection/1.1"
    if fault:
        raw["result"]["runtime_source"][fault] = "another-execution"
        store._result_path(handle.handle_id).write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ValueError, match="source observation identity"):
            reopened.load_collection(handle)
