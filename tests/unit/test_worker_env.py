"""Worker 子进程环境：扩展为 CLI IO 设置的编码变量不应传给被测程序。"""

from __future__ import annotations

from pathlib import Path

from aitest.worker import _run_env


def test_run_env_drops_extension_encoding_variables(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.setenv("AITEST_TEST_MARKER", "kept")
    env = _run_env(tmp_path, None, "run_env_test")
    assert "PYTHONUTF8" not in env
    assert "PYTHONIOENCODING" not in env
    # 其他非敏感变量仍按原样继承，避免改变被测程序行为。
    assert env.get("AITEST_TEST_MARKER") == "kept"
