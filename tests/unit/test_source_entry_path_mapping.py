"""Module-mode pytest entry scopes are directories, not only files."""

from __future__ import annotations

from pathlib import Path

from aitest.bootstrap import _entry_actual_path


def test_frozen_directory_entry_maps_to_the_materialized_scope(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    scope = workdir / "pkgA" / "tests"
    scope.mkdir(parents=True)
    (scope / "test_order.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")

    prepared = {
        "execution_source": {
            "entry_arguments": ["-m", "pytest", "-q", "pkgA/tests"],
        }
    }
    # The materialization mapping lists files; the scope itself is not a file
    # entry, so the frozen argument is the authority for a directory entry.
    paths = [
        {
            "relative_path": "pkgA/tests/test_order.py",
            "actual_path": str(scope / "test_order.py"),
        }
    ]
    assert _entry_actual_path(paths, str(workdir), prepared) == str(scope.resolve())


def test_frozen_file_entry_still_wins_over_a_directory_scope(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    file = workdir / "tests" / "test_order.py"
    file.parent.mkdir(parents=True)
    file.write_text("def test_a():\n    assert True\n", encoding="utf-8")

    prepared = {"execution_source": {"entry_arguments": ["tests/test_order.py"]}}
    paths = [{"relative_path": "tests/test_order.py", "actual_path": str(file)}]
    assert _entry_actual_path(paths, str(workdir), prepared) == str(file.resolve())
