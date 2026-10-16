"""Isolated stdlib-only Python file carrier for the frozen python-script/1 policy.

Executed from the installed artifact with -I -B -c. No application output is
parsed as provenance. The private inherited pipe carries metadata only.
"""

from __future__ import annotations

import _imp
import atexit
import hashlib
import io
import json
import os
import sys
import sysconfig
import threading
import types
from pathlib import Path
from typing import Any


def main() -> None:
    # -I ignores PYTHONIOENCODING; retain the adapter's UTF-8 output contract.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    config = json.loads(sys.stdin.buffer.readline(2 * 1024 * 1024))
    fd = config["pipe"]
    if os.name == "nt":
        import msvcrt

        fd = msvcrt.open_osfhandle(fd, os.O_WRONLY)
    os.set_inheritable(fd, False)
    root = Path.cwd().resolve()
    entry = (root / config["arguments"][0]).resolve()
    expected = {item["relative_path"]: item["content_digest"] for item in config["files"]}
    trusted = tuple(Path(sysconfig.get_path(key)).resolve() for key in ("stdlib", "platstdlib"))
    observed: dict[str, str] = {}
    gaps: set[str] = set()
    active = threading.local()
    lock = threading.RLock()
    for relative in expected:
        name = relative.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
        if name in sys.modules:
            gaps.add("preloaded_project_module")

    def send(value: dict[str, Any]) -> None:
        data = (json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        while data:
            data = data[os.write(fd, data):]

    send({"event": "start", "pid": os.getpid(), "interpreter": str(Path(sys.executable).resolve()),
          "entry": str(entry)})
    if sys.stdin.buffer.readline(16) != b"owned\n":
        raise RuntimeError("source channel process ownership was not confirmed")
    # Application input retains the previous DEVNULL semantics.
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 - process lifetime

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if getattr(active, "busy", False):
            return
        with lock:
            active.busy = True
            try:
                observe(event, args)
            finally:
                active.busy = False

    def observe(event: str, args: tuple[Any, ...]) -> None:
        if event in {"function.__new__", "code.__new__"} or (
            event == "object.__setattr__" and len(args) > 1 and args[1] == "__code__"
        ):
            gaps.add("dynamic_code_unverified")
        if event in {"subprocess.Popen", "os.system", "os.exec", "os.fork", "os.spawn",
                     "os.posix_spawn", "os.startfile", "os.startfile/2", "_winapi.CreateProcess",
                     "ctypes.dlopen",
                     "sys._getframe", "sys.settrace", "sys.setprofile"}:
            gaps.add("opaque_execution")
        if event == "import" and len(args) > 1 and isinstance(args[1], str):
            imported = Path(args[1]).resolve()
            if imported.suffix != ".py":
                gaps.add("opaque_execution")
        if event != "exec":
            return
        try:
            code = args[0]
            if not isinstance(code, types.CodeType) or code.co_filename.startswith("<"):
                # Frozen standard-library modules are supplied by the registered interpreter.
                if not (isinstance(code, types.CodeType)
                        and code.co_filename.startswith("<frozen ")
                        and code.co_filename.endswith(">")
                        and _imp.is_frozen(code.co_filename[8:-1])
                        and _imp.get_frozen_object(code.co_filename[8:-1]) == code):
                    gaps.add("dynamic_code_unverified")
                return
            path = Path(code.co_filename).resolve()
            if not path.is_relative_to(root):
                if (not any(path.is_relative_to(base) for base in trusted)
                        or any(part.lower() in {"site-packages", "dist-packages"}
                               for part in path.parts)
                        or path.suffix != ".py" or compile(
                    path.read_bytes(), code.co_filename, "exec", dont_inherit=True,
                    optimize=sys.flags.optimize,
                ) != code):
                    gaps.add("external_code_unverified")
                return
            relative = path.relative_to(root).as_posix()
            if relative not in expected or path.suffix != ".py":
                gaps.add("undeclared_source")
                return
            data = path.read_bytes()
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
            if digest != expected[relative] or compile(
                data, code.co_filename, "exec", dont_inherit=True, optimize=sys.flags.optimize
            ) != code:
                gaps.add("loaded_code_mismatch")
                raise RuntimeError("loaded project code differs from frozen source")
            if relative not in observed:
                observed[relative] = digest
                send({"event": "file", "relative_path": relative, "digest": digest})
        except (OSError, ValueError, SyntaxError):
            gaps.add("source_read_unverified")

    # Remove the carrier's __main__ so the application sees its own module.
    module = types.ModuleType("__main__")
    module.__file__ = str(entry)
    module.__package__ = None
    module.__spec__ = None
    sys.modules["__main__"] = module
    sys.argv = list(config["arguments"])
    sys.path.insert(0, str(entry.parent))
    sys.addaudithook(audit)
    def finish() -> None:
        with lock:
            if any(t.is_alive() and t is not threading.current_thread()
                   for t in threading.enumerate()):
                gaps.add("live_thread_unverified")
            send({"event": "end", "gaps": sorted(gaps)})
            os.close(fd)

    atexit.register(finish)
    code = compile(entry.read_bytes(), str(entry), "exec", dont_inherit=True)
    exec(code, module.__dict__)


if __name__ == "__main__":
    main()
