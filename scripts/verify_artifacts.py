"""Verify deliverables contain shared generated resources, not old services."""

import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    wheel = next((ROOT / "dist").glob("*.whl"))
    vsix = ROOT / "integrations/trae/dist/aitest-trae.vsix"
    with zipfile.ZipFile(wheel) as package, zipfile.ZipFile(vsix) as extension:
        names = package.namelist()
        assert len([p for p in names if "/templates/" in p and p.endswith(".json")]) == 6
        assert "aitest/contracts/schemas/Command.json" in names
        assert not any("node_modules" in p or p.startswith("ai_test/") for p in names)
        panel = package.read("aitest/resources/panel/dist/index.js")
        assert panel == extension.read("extension/dist/panel.js")
        assert panel == (ROOT / "src/aitest/resources/panel/dist/index.js").read_bytes()
    evidence = {
        "version": "0.4.0",
        "result": "passed",
        "scope": "artifact_contents_and_shared_panel_identity_only",
        "artifacts": [
            {"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in (wheel, vsix)
        ],
    }
    output = ROOT / "docs/validation/artifacts.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
