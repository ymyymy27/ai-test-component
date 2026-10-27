"""`aitest ensure-core`：宿主入口的发现/启动语义与"不造工作空间"守卫。

架构 05 第 1 节把"先连既有核心，连不上再启动一个"交给编辑器宿主；本动词是它的
命令行形态。这里锁定两件事：

1. 路径缺失或不是目录时**结构化失败**，且**不创建**数据工作空间——与 `doctor`
   的既有口径一致（写错的路径不得变成一个新的数据工作空间）；
2. 本动作不派发业务动作，只回报已核对的端点身份。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aitest.interfaces.tools import cli


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(sys, "argv", ["aitest", *argv])
    code = cli.main()
    captured = capsys.readouterr()
    return code, captured


def test_ensure_core_requires_a_workspace_argument(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["aitest", "ensure-core"])
    with pytest.raises(SystemExit):
        cli.main()


def test_ensure_core_refuses_a_missing_workspace_without_creating_it(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    target = tmp_path / "typo-workspace"

    code, captured = _run(
        ["ensure-core", "--workspace", str(target)], monkeypatch, capsys
    )

    assert code == 2
    assert not target.exists(), "写错的路径不得被入口创建成数据工作空间"
    payload = json.loads(captured.err.strip())
    assert payload["code"] == "WORKSPACE_NOT_FOUND"


def test_ensure_core_refuses_a_file_path(tmp_path: Path, monkeypatch, capsys) -> None:
    target = tmp_path / "not-a-directory"
    target.write_text("x", encoding="utf-8")

    code, captured = _run(
        ["ensure-core", "--workspace", str(target)], monkeypatch, capsys
    )

    assert code == 2
    assert json.loads(captured.err.strip())["code"] == "WORKSPACE_NOT_FOUND"


def test_rebuild_index_refuses_a_missing_workspace_without_creating_it(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    target = tmp_path / "typo-workspace"

    code, captured = _run(
        ["rebuild-index", "--workspace", str(target)], monkeypatch, capsys
    )

    assert code == 2
    assert not target.exists(), "维护入口同样不得把写错的路径创建成工作空间"
    assert json.loads(captured.err.strip())["code"] == "WORKSPACE_NOT_FOUND"


def test_rebuild_index_does_not_turn_a_plain_directory_into_a_workspace(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    code, captured = _run(
        ["rebuild-index", "--workspace", str(plain)], monkeypatch, capsys
    )

    assert code == 2
    assert list(plain.iterdir()) == [], "维护命令不得在普通目录写入锁或身份文件"
    assert json.loads(captured.err.strip())["code"] == "INDEX_REBUILD_FAILED"


def test_rebuild_index_reports_new_generation_for_a_real_workspace(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from tests.unit.test_sharded_record_authority import _legacy, _migrate

    _legacy(tmp_path, count=2)
    _migrate(tmp_path)

    code, captured = _run(
        ["rebuild-index", "--workspace", str(tmp_path)], monkeypatch, capsys
    )

    assert code == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["status"] == "REBUILT" and payload["generation"] >= 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named-pipe host lifecycle")
def test_one_shot_startup_keeps_same_core_for_owning_host(tmp_path: Path) -> None:
    from aitest.bootstrap import shutdown_endpoint

    env = dict(os.environ, PYTHONUTF8="1")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    cmd = [sys.executable, "-m", "aitest.interfaces.tools.cli"]
    try:
        startup = subprocess.run(
            [*cmd, "ensure-core", "--workspace", str(tmp_path)],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=90,
        )
        assert startup.returncode == 0, startup.stderr
        identity = json.loads(startup.stdout)
        # More than two watchdog intervals after the startup process has exited.
        time.sleep(2.5)
        read = subprocess.run(
            [*cmd, "doctor", "--workspace", str(tmp_path)],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=90,
        )
        assert read.returncode == 0, read.stderr
        response = json.loads(read.stdout)
        assert response["instance_id"] == identity["instance_id"]
        assert response["workspace_id"] == identity["workspace_id"]
        assert response["error"] is None
    finally:
        shutdown_endpoint(tmp_path)
