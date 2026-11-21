"""Worker 子进程环境：编码与项目 .env 的处理。"""

from __future__ import annotations

from pathlib import Path

from aitest.worker import _run_env


def test_run_env_pins_utf8_for_windows_projects(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.setenv("AITEST_TEST_MARKER", "kept")
    env = _run_env(tmp_path, None, "run_env_test")
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    # 其他非敏感变量仍按原样继承，避免改变被测程序行为。
    assert env.get("AITEST_TEST_MARKER") == "kept"


def test_run_env_loads_project_dotenv_with_expansion(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "SECRET_KEY=project-secret",
                "DB_PASSWORD=db-pass",
                'DATABASE_URL="postgresql://postgres:${DB_PASSWORD}@localhost/app"',
                "AITEST_MODEL_KEY=must-not-leak",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SECRET_KEY", "host-secret")
    env = _run_env(tmp_path, None, "run_dotenv")
    assert env["SECRET_KEY"] == "project-secret"
    assert env["DATABASE_URL"] == "postgresql://postgres:db-pass@localhost/app"
    assert "AITEST_MODEL_KEY" not in env

