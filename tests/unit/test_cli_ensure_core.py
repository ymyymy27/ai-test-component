"""`aitest ensure-core`：宿主入口的发现/启动语义与"不造工作空间"守卫。

架构 05 第 1 节把"先连既有核心，连不上再启动一个"交给编辑器宿主；本动词是它的
命令行形态。这里锁定两件事：

1. 路径缺失或不是目录时**结构化失败**，且**不创建**数据工作空间——与 `doctor`
   的既有口径一致（写错的路径不得变成一个新的数据工作空间）；
2. 本动作不派发业务动作，只回报已核对的端点身份。
"""

from __future__ import annotations

import json
import sys
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
