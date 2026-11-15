"""Collapse single-decorator parametrize matrices into one in-body loop.

Usage: python .tmp_deparam.py <file> [<file> ...]
"""

from __future__ import annotations

import ast
import pathlib
import sys


def _eligible(node: ast.FunctionDef, source: str) -> tuple[ast.Call, list[str], str] | None:
    if not node.name.startswith("test_") or len(node.decorator_list) != 1:
        return None
    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(
        node.body[0].value, ast.Constant
    ) and isinstance(node.body[0].value.value, str):
        return None
    dec = node.decorator_list[0]
    if not (
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr == "parametrize"
        and isinstance(dec.func.value, ast.Attribute)
        and dec.func.value.attr == "mark"
    ):
        return None
    if len(dec.args) < 2 or not isinstance(dec.args[0], ast.Constant):
        return None
    names_text = dec.args[0].value
    if not isinstance(names_text, str):
        return None
    names = [part.strip() for part in names_text.split(",") if part.strip()]
    if not names or len(names) > 2:
        return None
    values_src = ast.get_source_segment(source, dec.args[1])
    if values_src is None or "pytest.param" in values_src or "indirect" in values_src:
        return None
    if any(kw.arg not in {"argnames", "argvalues"} for kw in dec.keywords):
        return None
    if any(arg.arg in {"request", "recwarn"} for arg in node.args.args):
        return None
    extra = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args) if arg.arg not in set(names)]
    if extra or node.args.vararg is not None or node.args.kwarg is not None or node.args.kwonlyargs:
        # Fixture-based cases keep per-case isolation (fresh tmp_path/monkeypatch).
        return None
    return dec, names, values_src


def transform(path: pathlib.Path) -> int:
    changed = 0
    while True:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = None
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            eligible = _eligible(node, source)
            if eligible is not None:
                found = (node, *eligible)
                break
        if found is None:
            break
        node, dec, names, values_src = found
        lines = source.splitlines(keepends=True)
        param_names = set(names)
        remaining_args = ast.arguments(
            posonlyargs=[a for a in node.args.posonlyargs if a.arg not in param_names],
            args=[a for a in node.args.args if a.arg not in param_names],
            vararg=node.args.vararg,
            kwonlyargs=[a for a in node.args.kwonlyargs if a.arg not in param_names],
            kw_defaults=[
                default
                for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
                if arg.arg not in param_names
            ],
            kwarg=node.args.kwarg,
            defaults=node.args.defaults,
        )
        returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
        signature = f"def {node.name}({ast.unparse(remaining_args)}){returns}:\n"
        loop = "    for {target} in {values}:\n".format(
            target=", ".join(names), values=values_src
        )
        body = lines[node.body[0].lineno - 1 : node.end_lineno]
        indented = [("    " + line) if line.strip() else line for line in body]
        rebuilt = (
            lines[: dec.lineno - 1]
            + [signature, loop]
            + indented
            + lines[node.end_lineno :]
        )
        lines = rebuilt
        path.write_text("".join(lines), encoding="utf-8")
        changed += 1
    return changed


def main() -> None:
    for raw in sys.argv[1:]:
        path = pathlib.Path(raw)
        try:
            print(f"{path}: collapsed {transform(path)} functions")
        except Exception as error:  # noqa: BLE001 - batch continue
            print(f"{path}: FAILED {error!r}")


if __name__ == "__main__":
    main()
