import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import errno  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

from aitest.infrastructure.file_store import atomic  # noqa: E402
from aitest.infrastructure.file_store.locking import writer_lock  # noqa: E402


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


def test_real_second_process_cannot_acquire_writer_lock(tmp_path: Path) -> None:
    path = tmp_path / "writer.lock"
    code = (
        "from pathlib import Path; "
        "from aitest.infrastructure.file_store.locking import writer_lock; "
        "import sys\n"
        "with writer_lock(Path(sys.argv[1])): pass\n"
    )
    with writer_lock(path):
        blocked = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True)
        # 调试：观察 WorkspaceInUse 异常类名实际输出位置（stderr traceback 或 stdout）
        print(f"[debug-blocked] returncode={blocked.returncode}")
        print(f"[debug-blocked] stdout={blocked.stdout!r}")
        print(f"[debug-blocked] stderr={blocked.stderr!r}")
        assert blocked.returncode != 0
        # 子进程未捕获的异常默认走 stderr traceback；部分环境/重定向场景可能落到 stdout，
        # 因此同时检查两个流以提升跨环境稳定性。
        assert (
            b"WorkspaceInUse" in blocked.stderr
            or b"WorkspaceInUse" in blocked.stdout
        )
    recovered = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True)
    # 调试：恢复后子进程输出，便于核对锁释放路径
    print(f"[debug-recovered] returncode={recovered.returncode}")
    print(f"[debug-recovered] stdout={recovered.stdout!r}")
    print(f"[debug-recovered] stderr={recovered.stderr!r}")
    assert recovered.returncode == 0
