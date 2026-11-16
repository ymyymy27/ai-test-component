import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import aitest
from aitest.infrastructure.file_store import atomic
from aitest.infrastructure.file_store.locking import writer_lock


def _child_env() -> dict[str, str]:
    """构造子进程环境，显式把 aitest 所在的 src 目录注入 ``PYTHONPATH``。

    pytest 的 ``pythonpath`` 配置只对当前解释器生效；subprocess 派生的
    独立解释器只继承环境变量，未 pip 安装本包时必须显式传入 src 路径，
    子进程才能 ``import aitest``。
    """
    src_dir = str(Path(aitest.__file__).resolve().parent.parent)
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        os.pathsep.join([src_dir, existing]) if existing else src_dir
    )
    return env


def test_failed_publish_preserves_old_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "current.json"
    atomic.write_json(path, {"commit": "old"})

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(atomic.os, "replace", fail)
    with pytest.raises(OSError):
        atomic.write_json(path, {"commit": "new"})
    assert json.loads(path.read_text()) == {"commit": "old"}
    assert list(tmp_path.iterdir()) == [path]


def test_transient_replace_retries_but_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fail(*args: object) -> None:
        calls.append(args)
        raise PermissionError(errno.EACCES, "busy")

    monkeypatch.setattr(atomic.os, "replace", fail)
    monkeypatch.setattr(atomic.time, "sleep", lambda seconds: None)
    with pytest.raises(PermissionError):
        atomic.replace_with_retry("a", "b", attempts=3)
    assert len(calls) == 3


@pytest.mark.skipif(os.name != "nt", reason="Windows long-path diagnostics")
def test_long_target_is_attempted_instead_of_pre_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 父目录已存在、只有目标名过长：不再按长度提前拒绝，而是真实尝试创建。
    target = tmp_path / ("x" * 260 + ".json")
    assert len(str(target.absolute())) > atomic._MAX_TARGET_PATH
    attempted: list[int] = []

    def refuse_creation(*args: object, **kwargs: object) -> int:
        attempted.append(1)
        raise FileNotFoundError(errno.ENOENT, "cannot create")

    monkeypatch.setattr(atomic.tempfile, "mkstemp", refuse_creation)
    monkeypatch.setattr(atomic.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(atomic, "_long_paths_enabled", lambda: True)
    with pytest.raises(OSError) as raised:
        atomic.write_json(target, {"value": 1})
    assert attempted, "长路径写入必须先真实尝试，而不是在写之前拒绝"
    assert "已启用长路径支持" in str(raised.value)


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [(True, "已启用长路径支持"), (False, "未启用长路径支持"), (None, "如需保留长路径")],
)
def test_long_path_message_follows_the_actual_host_setting(
    monkeypatch: pytest.MonkeyPatch, enabled: bool | None, expected: str
) -> None:
    monkeypatch.setattr(atomic, "_long_paths_enabled", lambda: enabled)
    assert expected in atomic._long_path_message(300)


def test_real_second_process_cannot_acquire_writer_lock(tmp_path: Path) -> None:
    path = tmp_path / "writer.lock"
    code = (
        "from pathlib import Path; "
        "from aitest.infrastructure.file_store.locking import writer_lock; "
        "import sys\n"
        "with writer_lock(Path(sys.argv[1])): pass\n"
    )
    with writer_lock(path):
        blocked = subprocess.run(
            [sys.executable, "-c", code, str(path)],
            capture_output=True,
            env=_child_env(),
        )
        assert blocked.returncode != 0
        assert b"WorkspaceInUse" in blocked.stderr
    recovered = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        capture_output=True,
        env=_child_env(),
    )
    assert recovered.returncode == 0
