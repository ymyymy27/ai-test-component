"""Check audit links, table shape, FR/AC scope, probe references, and source hashes."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "CHANGELOG.md",
    ROOT / "docs/一期工程分包检查.md",
    ROOT / "docs/当前代码分析与一期工程对比.md",
    ROOT / "docs/修改日志/袁/2026-10-01-一期工程分包与整体源码检查.md",
    OUTPUT / "README.md",
)


def main() -> None:
    problems: list[str] = []
    checked_links = 0
    for path in DOCUMENTS:
        source = path.read_text(encoding="utf-8-sig")
        for raw in re.findall(r"\[[^\]]+\]\(([^)]+)\)", source):
            if raw.startswith(("https://", "http://", "#", "codex://")):
                continue
            target = unquote(raw.split("#", 1)[0]).strip("<>")
            checked_links += 1
            if not (path.parent / target).exists():
                problems.append(f"missing link: {path.name}: {target}")
        columns: int | None = None
        for number, line in enumerate(source.splitlines(), 1):
            if line.startswith("|"):
                current = line.count("|") - 1
                if columns is not None and columns != current:
                    problems.append(f"table shape: {path.name}:{number}")
                columns = current
            else:
                columns = None
    overall = (ROOT / "docs/当前代码分析与一期工程对比.md").read_text(encoding="utf-8")
    for kind, total in (("FR", 17), ("AC", 35)):
        found = re.findall(rf"^\| P1-{kind}(\d{{2}}) \|", overall, re.MULTILINE)
        expected = [f"{number:02d}" for number in range(1, total + 1)]
        if found != expected:
            problems.append(f"{kind} rows differ: {found}")
    inventory = json.loads((OUTPUT / "source-inventory.json").read_text(encoding="utf-8"))
    for item in inventory["sources"] + inventory["tests"] + inventory["normative_documents"]:
        if sha256((ROOT / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            problems.append(f"source/contract changed since inventory: {item['path']}")
    package = (ROOT / "docs/一期工程分包检查.md").read_text(encoding="utf-8")
    probes = json.loads((OUTPUT / "probe-results.json").read_text(encoding="utf-8"))
    if len(probes) != 20:
        problems.append(f"probe count: {len(probes)}")
    for key in probes:
        if key not in package:
            problems.append(f"probe absent from report: {key}")
    if (ROOT / "docs/一期工程未实现部分分包检查.md").exists():
        problems.append("old report still exists")
    result = {
        "checked_documents": len(DOCUMENTS),
        "checked_local_links": checked_links,
        "fr_rows": 17,
        "ac_rows": 35,
        "probes": len(probes),
        "scope_hashes_unchanged": not any("inventory" in problem for problem in problems),
        "problems": problems,
        "does_not_claim": "runtime correctness, real acceptance, or external link availability",
    }
    (OUTPUT / "document-check.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
