"""步骤 5.2（LivePageProbe）与步骤 5.4（verify_vsix.py）单元测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import types
import zipfile
from pathlib import Path
from typing import Any

import pytest

from aitest.core.live_page_probe import LivePageProbe, detect_local_browser
from aitest.core.plugins import DEFAULT_REGISTRY
from aitest.core.probes import run_deterministic_probes

ROOT = Path(__file__).resolve().parents[2]


def test_live_page_probe_registered_and_skips_when_disabled(tmp_path: Path) -> None:
    probe_ids = [p.probe_id for p in DEFAULT_REGISTRY.probes()]
    assert "live_page_inspection" in probe_ids

    probe = LivePageProbe()
    res_disabled = probe.run(base_url="http://127.0.0.1:8000", service_ready=True, config={})
    assert res_disabled["status"] == "skipped"
    assert res_disabled["engine"] == "disabled"

    # 默认 run_deterministic_probes 未配置 enabled=True 时保持原 3 个内置探针向后兼容
    default_results = run_deterministic_probes(tmp_path, {}, context={"api_routes": []})
    assert len(default_results) == 3
    assert all(r["probe_id"] != "live_page_inspection" for r in default_results)


def test_live_page_probe_blocks_disallowed_url_and_handles_missing_browsers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = LivePageProbe()
    blocked = probe.run(
        enable_live_page=True,
        service_ready=True,
        base_url="http://169.254.169.254/latest/meta-data",
    )
    assert blocked["status"] == "failed"
    assert blocked["findings"][0]["kind"] == "url_blocked"

    monkeypatch.setattr("aitest.core.live_page_probe.importlib.util.find_spec", lambda _name: None)
    monkeypatch.setattr("aitest.core.live_page_probe.detect_local_browser", lambda: "")
    skipped = probe.run(
        enable_live_page=True,
        service_ready=True,
        base_url="http://127.0.0.1:9000",
    )
    assert skipped["status"] == "skipped"
    assert skipped["engine"] == "unavailable"


def test_live_page_probe_local_browser_fallback_catches_blank_and_overlay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_exe = tmp_path / "msedge.exe"
    fake_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr("aitest.core.live_page_probe.importlib.util.find_spec", lambda _name: None)
    monkeypatch.setattr("aitest.core.live_page_probe.detect_local_browser", lambda: str(fake_exe))

    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        html = (
            '<html><head><title>App</title></head>'
            '<body><div id="root"></div><div>vite-error-overlay</div></body></html>'
        )
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=html,
            stderr="",
        )

    monkeypatch.setattr("aitest.core.live_page_probe.subprocess.run", fake_run)

    probe = LivePageProbe()
    res = probe.run(
        enable_live_page=True,
        service_ready=True,
        base_url="http://127.0.0.1:5173",
    )
    assert res["status"] == "failed"
    assert res["engine"] == "local_browser"
    kinds = {f["kind"] for f in res["findings"]}
    assert "live_dom_error_overlay" in kinds or "live_dom_blank" in kinds


def test_live_page_probe_playwright_captures_console_and_500_and_honors_convention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handlers: dict[str, Any] = {}

    class FakePage:
        def on(self, event: str, cb: Any) -> None:
            handlers[event] = cb

        def goto(self, _url: str, **_kwargs: Any) -> None:
            if "console" in handlers:
                handlers["console"](
                    types.SimpleNamespace(type="error", text="Uncaught ReferenceError: foo")
                )
            if "response" in handlers:
                handlers["response"](
                    types.SimpleNamespace(status=500, url="http://127.0.0.1:8000/api/crash")
                )

        def content(self) -> str:
            return "<html><body><h1>Hello World App Dashboard</h1></body></html>"

    class FakeBrowser:
        def new_page(self) -> FakePage:
            return FakePage()

        def close(self) -> None:
            return None

    class FakeCtx:
        def __enter__(self) -> Any:
            return types.SimpleNamespace(
                chromium=types.SimpleNamespace(launch=lambda **_kw: FakeBrowser())
            )

        def __exit__(self, *_exc: Any) -> None:
            return None

    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = lambda: FakeCtx()  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "aitest.core.live_page_probe.importlib.util.find_spec",
        lambda name: object() if name == "playwright" else None,
    )
    monkeypatch.setattr(
        "aitest.core.live_page_probe.importlib.import_module",
        lambda name: fake_sync_api if name == "playwright.sync_api" else None,
    )

    # 通过 run_deterministic_probes 运行并验证错题本豁免生效
    cfg_no_conv = {
        "probes": {"live_page": {"enabled": True}},
        "conventions": [],
    }
    res1 = run_deterministic_probes(
        tmp_path,
        cfg_no_conv,
        context={"api_routes": [], "frontend_api_calls": [], "frontend_assets": []},
        base_url="http://127.0.0.1:8000",
        service_ready=True,
    )
    live1 = next(item for item in res1 if item["probe_id"] == "live_page_inspection")
    assert live1["status"] == "failed"
    assert len(live1["findings"]) == 2

    cfg_with_conv = {
        "probes": {"live_page": {"enabled": True}},
        "conventions": [
            {"id": "c1", "pattern": "/api/crash", "reason": "压测接口允许 500", "scope": "probe"},
            {"id": "c2", "pattern": "ReferenceError", "reason": "忽略噪声", "scope": "probe"},
        ],
    }
    res2 = run_deterministic_probes(
        tmp_path,
        cfg_with_conv,
        context={"api_routes": [], "frontend_api_calls": [], "frontend_assets": []},
        base_url="http://127.0.0.1:8000",
        service_ready=True,
    )
    live2 = next(item for item in res2 if item["probe_id"] == "live_page_inspection")
    assert live2["status"] == "passed"
    assert len(live2["suppressed_findings"]) == 2
    assert isinstance(detect_local_browser(), str)


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
