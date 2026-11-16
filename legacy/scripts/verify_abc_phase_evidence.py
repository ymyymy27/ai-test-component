"""Verify frozen source/log evidence, optionally against the current checkout."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def relative_file(root: Path, name: str) -> Path:
    path = root / name
    if (
        Path(name).is_absolute()
        or ".." in Path(name).parts
        or not path.resolve().is_relative_to(root)
    ):
        raise ValueError("evidence reference escapes the repository")
    return path


def symbol_lines(content: bytes) -> dict[str, int]:
    lines: dict[str, int] = {}

    def visit(nodes: list[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbol = f"{prefix}.{node.name}" if prefix else node.name
                lines[symbol] = node.lineno
                visit(node.body, symbol)

    visit(ast.parse(content.decode("utf-8-sig")).body)
    return lines


def verify(root: Path, manifest_path: Path, *, against_checkout: bool) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_path = relative_file(root, manifest["source_archive"]["path"])
    if digest(archive_path.read_bytes()) != manifest["source_archive"]["sha256"]:
        raise ValueError("frozen source archive digest mismatch")
    with ZipFile(archive_path) as archive:
        if set(archive.namelist()) != {item["path"] for item in manifest["source_files"]}:
            raise ValueError("frozen source member set mismatch")
        for item in manifest["source_files"]:
            content = archive.read(item["path"])
            if digest(content) != item["sha256"]:
                raise ValueError(f"frozen source member digest mismatch: {item['path']}")
            if against_checkout:
                current = relative_file(root, item["path"]).read_bytes()
                if digest(current.replace(b"\r\n", b"\n")) != item["normalized_sha256"]:
                    raise ValueError(f"checkout differs from this validation phase: {item['path']}")
        for ref in manifest["references"]:
            content = archive.read(ref["path"])
            if symbol_lines(content).get(ref["symbol"]) != ref["line"]:
                raise ValueError(f"source symbol/line mismatch: {ref['path']}:{ref['symbol']}")
    for log in manifest["logs"]:
        if digest(relative_file(root, log["path"]).read_bytes()) != log["sha256"]:
            raise ValueError(f"validation log digest mismatch: {log['path']}")
    return {
        "status": "passed",
        "phase": manifest["phase"],
        "source_files": len(manifest["source_files"]),
        "references": len(manifest["references"]),
        "logs": len(manifest["logs"]),
        "against_checkout": against_checkout,
        "real_ac_verified": manifest["real_ac_verified"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--against-checkout", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = verify(root, args.manifest.resolve(), against_checkout=args.against_checkout)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
