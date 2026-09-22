"""Smoke-test the built wheel outside the checkout and editable environment."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    wheels = list((ROOT / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Expected exactly one built wheel in dist")
    with tempfile.TemporaryDirectory(prefix="aitest-wheel-") as directory:
        root = Path(directory)
        environment = root / "venv"
        subprocess.run(["uv", "venv", str(environment), "--python", "3.13"], check=True)
        binary = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binary / ("python.exe" if os.name == "nt" else "python")
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(wheels[0])], check=True
        )
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
