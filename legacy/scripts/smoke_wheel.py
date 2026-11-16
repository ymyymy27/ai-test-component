"""Smoke-test the built wheel outside the checkout and editable environment."""

import json
import os
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    wheel = ROOT / "dist" / f"ai_test_component-{version}-py3-none-any.whl"
    if not wheel.is_file():
        raise RuntimeError("The current project version has not been built")
    with tempfile.TemporaryDirectory(prefix="aitest-wheel-") as directory:
        root = Path(directory)
        environment = root / "venv"
        subprocess.run(["uv", "venv", str(environment), "--python", "3.13"], check=True)
        binary = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binary / ("python.exe" if os.name == "nt" else "python")
        subprocess.run(["uv", "pip", "install", "--python", str(python), str(wheel)], check=True)
        for args, expected in [
            (["templates"], 0),
            (["doctor"], 2),
            (["mcp-relay", "--binding", "smoke"], 2),
        ]:
            result = subprocess.run(
                [str(python), "-I", "-m", "aitest.interfaces.tools.cli", *args],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.returncode != expected:
                raise RuntimeError(f"Unexpected exit for {args}: {result.stderr}")
            if args[0] == "templates":
                assert len(json.loads(result.stdout)) == 6
            elif args[0] == "doctor":
                assert json.loads(result.stdout)["result"]["status"] == "NOT_READY"
            else:
                assert not result.stdout
                assert json.loads(result.stderr)["code"] == "CAPABILITY_UNAVAILABLE"
    print("Isolated wheel smoke checks passed")


if __name__ == "__main__":
    main()
