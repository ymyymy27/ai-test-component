"""Build the wheel, install it into a throwaway environment and run ``doctor``.

This is the lightweight replacement for the old ``verify_artifacts.py`` /
``smoke_wheel.py`` pair that moved to ``legacy/scripts/``. It only proves three
things: the wheel builds, the package imports from the installed location (not
from ``src/``), and ``python -m aitest.cli doctor`` returns a successful
envelope. It does not inspect release metadata or compare artifact hashes.

Usage::

    uv run python scripts/smoke_wheel.py
    python scripts/smoke_wheel.py          # falls back to ``python -m build``

Exit code 0 means every assertion passed; 1 means a mismatch was reported.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUEST_ID = "smoke-wheel-doctor"


def _version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(project["project"]["version"])


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True, env=env)


def _venv_python(venv: Path) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    name = "python.exe" if os.name == "nt" else "python"
    return venv / directory / name


def _build_wheel(dist: Path, version: str) -> Path:
    uv = shutil.which("uv")
    if uv is not None:
        _run([uv, "build", "--wheel", "--out-dir", str(dist)], cwd=ROOT)
    else:
        _run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
            cwd=ROOT,
        )
    expected_prefix = f"ai_test_component-{version}-"
    wheels = sorted(path for path in dist.glob("*.whl") if path.name.startswith(expected_prefix))
    if len(wheels) != 1:
        raise SystemExit(
            f"expected exactly one {expected_prefix}*.whl in {dist}, found {len(wheels)}"
        )
    return wheels[0]


def _create_venv(venv: Path) -> Path:
    uv = shutil.which("uv")
    if uv is not None:
        _run([uv, "venv", "--python", sys.executable, str(venv)], cwd=ROOT)
    else:
        _run([sys.executable, "-m", "venv", str(venv)], cwd=ROOT)
    python = _venv_python(venv)
    if not python.is_file():
        raise SystemExit(f"venv interpreter missing: {python}")
    return python


def _install(python: Path, wheel: Path) -> None:
    uv = shutil.which("uv")
    if uv is not None:
        _run([uv, "pip", "install", "--python", str(python), str(wheel)], cwd=ROOT)
    else:
        _run([str(python), "-m", "pip", "install", str(wheel)], cwd=ROOT)


def _clean_env() -> dict[str, str]:
    """Drop variables that would make the temp interpreter import repository code."""

    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV"):
        env.pop(name, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def main() -> int:
    version = _version()
    with tempfile.TemporaryDirectory(prefix="aitest-smoke-wheel-") as raw:
        temp = Path(raw)
        wheel = _build_wheel(ROOT / "dist", version)
        venv = temp / "venv"
        python = _create_venv(venv)
        _install(python, wheel)

        env = _clean_env()
        installed = subprocess.run(
            [
                str(python),
                "-c",
                "import aitest, pathlib; print(pathlib.Path(aitest.__file__).resolve())",
            ],
            cwd=temp,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        module_path = Path(installed.stdout.strip())
        if venv.resolve() not in module_path.parents:
            raise SystemExit(f"aitest was imported from outside the venv: {module_path}")

        workspace = temp / "workspace"
        request = temp / "request.json"
        request.write_text(
            json.dumps(
                {"command": "doctor", "request_id": REQUEST_ID, "payload": {}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                str(python),
                "-m",
                "aitest.cli",
                "doctor",
                "--workspace",
                str(workspace),
                "--input",
                str(request),
            ],
            cwd=temp,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        print(completed.stdout.strip())
        if completed.returncode != 0:
            print(completed.stderr.strip(), file=sys.stderr)
            raise SystemExit(f"doctor exited with {completed.returncode}")
        try:
            response = json.loads(completed.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise SystemExit(f"doctor did not print one JSON envelope: {exc}") from exc
        if response.get("ok") is not True:
            raise SystemExit(f"doctor envelope is not ok: {response}")
        if response.get("request_id") != REQUEST_ID:
            echoed = response.get("request_id")
            raise SystemExit(f"doctor echoed a different request_id: {echoed!r}")
        package = response.get("data", {}).get("package", {})
        if package.get("version") != version:
            raise SystemExit(f"installed package version {package.get('version')!r} != {version}")
        print(f"smoke ok: {wheel.name} installed in {venv} and doctor responded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
