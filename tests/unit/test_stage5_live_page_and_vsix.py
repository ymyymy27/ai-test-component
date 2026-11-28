"""步骤 5.4（verify_vsix.py）单元测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_verify_vsix_checks_integrity_and_detects_tampering(tmp_path: Path) -> None:
    ext_root = tmp_path / "trae"
    (ext_root / "dist").mkdir(parents=True)
    (ext_root / "media" / "panel").mkdir(parents=True)
    (ext_root / "media" / "runtime").mkdir(parents=True)

    ext_js = ext_root / "dist" / "extension.js"
    panel_html = ext_root / "media" / "panel" / "index.html"
    icon_svg = ext_root / "media" / "testing.svg"
    wheel = ext_root / "media" / "runtime" / "ai_test_component-0.5.0-py3-none-any.whl"
    pkg_json = ext_root / "package.json"

    ext_js.write_bytes(b"console.log('aitest');")
    panel_html.write_text("<html><body>ok</body></html>", encoding="utf-8")
    icon_svg.write_text("<svg></svg>", encoding="utf-8")
    wheel.write_bytes(b"PK\x03\x04wheel-bytes")
    pkg_json.write_text(json.dumps({"name": "aitest-trae", "version": "0.5.0"}), encoding="utf-8")

    vsix_path = ext_root / "dist" / "aitest-trae.vsix"
    with zipfile.ZipFile(vsix_path, "w") as zf:
        zf.write(pkg_json, "extension/package.json")
        zf.write(ext_js, "extension/dist/extension.js")
        zf.write(panel_html, "extension/media/panel/index.html")
        zf.write(icon_svg, "extension/media/testing.svg")
        zf.write(wheel, f"extension/media/runtime/{wheel.name}")

    script = ROOT / "scripts" / "verify_vsix.py"
    ok_proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--vsix",
            str(vsix_path),
            "--extension-root",
            str(ext_root),
            "--write-manifest",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert '"ok": true' in ok_proc.stdout
    manifest = Path(str(vsix_path) + ".sha256")
    assert manifest.is_file()
    assert wheel.name in manifest.read_text(encoding="utf-8")

    # 篡改磁盘上的一字节，必须校验失败拦截
    ext_js.write_bytes(b"console.log('TAMPERED');")
    bad_proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--vsix",
            str(vsix_path),
            "--extension-root",
            str(ext_root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert bad_proc.returncode != 0
    assert "SHA-256 不一致" in (bad_proc.stderr + bad_proc.stdout)
