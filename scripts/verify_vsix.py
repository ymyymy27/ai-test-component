"""校验 Trae VSIX 扩展包的结构完整性、版本一致性与字节级 SHA-256 哈希对拍（E-15）。

校验项目：
1. VSIX 文件存在且为合法 ZIP；
2. extension/package.json 与仓库 pyproject.toml 版本一致；
3. 必含核心产物存在：extension/dist/extension.js、extension/media/panel/index.html、
   extension/media/testing.svg、以及与当前版本匹配的 wheel；
4. 字节级 SHA-256 对拍：将 VSIX 内 extension.js、media/panel/* 与 media/runtime/*.whl
   与磁盘上的 build 输出目录逐一核对 SHA-256；
5. 计算并打印整包 SHA-256；传 ``--write-manifest`` 时写出 ``<vsix>.sha256`` 摘要清单。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXTENSION_ROOT = ROOT / "integrations" / "trae"
DEFAULT_VSIX = DEFAULT_EXTENSION_ROOT / "dist" / "aitest-trae.vsix"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def expected_version(pyproject_path: Path = ROOT / "pyproject.toml") -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def verify_vsix(
    vsix_path: Path = DEFAULT_VSIX,
    *,
    extension_root: Path = DEFAULT_EXTENSION_ROOT,
    version: str | None = None,
) -> dict[str, object]:
    """校验 VSIX 完整性和磁盘产物 SHA-256 一致性，返回明细报告或抛 SystemExit(1)。"""
    expected_ver = version or expected_version()
    if not vsix_path.is_file():
        raise SystemExit(f"VSIX 文件不存在：{vsix_path}")
    if not zipfile.is_zipfile(vsix_path):
        raise SystemExit(f"VSIX 不是有效的 ZIP 包：{vsix_path}")

    errors: list[str] = []
    verified_entries: dict[str, str] = {}

    with zipfile.ZipFile(vsix_path, "r") as zf:
        bad_member = zf.testzip()
        if bad_member is not None:
            errors.append(f"ZIP 成员损坏：{bad_member}")

        names = set(zf.namelist())
        if "extension/package.json" not in names:
            errors.append("缺少 extension/package.json")
        else:
            pkg = json.loads(zf.read("extension/package.json").decode("utf-8"))
            if str(pkg.get("version") or "") != expected_ver:
                errors.append(
                    f"extension/package.json 版本为 {pkg.get('version')!r}，"
                    f"与期望 {expected_ver!r} 不一致"
                )

        required_members = (
            "extension/dist/extension.js",
            "extension/media/panel/index.html",
            "extension/media/testing.svg",
        )
        for member in required_members:
            if member not in names:
                errors.append(f"缺少必含扩展资源：{member}")

        expected_whl_prefix = f"extension/media/runtime/ai_test_component-{expected_ver}-"
        own_wheels = [
            name
            for name in sorted(names)
            if name.startswith(expected_whl_prefix) and name.endswith(".whl")
        ]
        if len(own_wheels) != 1:
            errors.append(
                f"期望包含 1 个匹配版本 {expected_ver} 的核心 wheel"
                f"（{expected_whl_prefix}*.whl），实际找到 {len(own_wheels)} 个"
            )

        # 对拍磁盘产物
        # （extension/dist/extension.js, extension/media/panel/*, extension/media/runtime/*）
        disk_checks: list[tuple[str, Path]] = [
            ("extension/dist/extension.js", extension_root / "dist" / "extension.js"),
        ]
        panel_dir = extension_root / "media" / "panel"
        if panel_dir.is_dir():
            for disk_file in sorted(p for p in panel_dir.rglob("*") if p.is_file()):
                rel = disk_file.relative_to(extension_root).as_posix()
                disk_checks.append((f"extension/{rel}", disk_file))
        runtime_dir = extension_root / "media" / "runtime"
        if runtime_dir.is_dir():
            for disk_file in sorted(p for p in runtime_dir.rglob("*") if p.is_file()):
                rel = disk_file.relative_to(extension_root).as_posix()
                disk_checks.append((f"extension/{rel}", disk_file))

        for zip_member, disk_path in disk_checks:
            if zip_member not in names:
                errors.append(f"VSIX 内缺失与磁盘产物对应的文件：{zip_member}")
                continue
            if not disk_path.is_file():
                errors.append(f"磁盘预期构建产物不存在：{disk_path}")
                continue
            zip_digest = sha256_bytes(zf.read(zip_member))
            disk_digest = sha256_file(disk_path)
            if zip_digest != disk_digest:
                errors.append(
                    f"SHA-256 不一致：{zip_member}"
                    f"（VSIX={zip_digest[:12]} vs 磁盘={disk_digest[:12]}）"
                )
            else:
                verified_entries[zip_member] = zip_digest

    if errors:
        raise SystemExit("VSIX 校验失败：\n- " + "\n- ".join(errors))

    vsix_sha256 = sha256_file(vsix_path)
    return {
        "vsix": str(vsix_path),
        "version": expected_ver,
        "size_bytes": vsix_path.stat().st_size,
        "sha256": vsix_sha256,
        "verified_files_count": len(verified_entries),
        "verified_files": verified_entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 Trae VSIX 包完整性与 SHA-256 哈希")
    parser.add_argument("--vsix", type=Path, default=DEFAULT_VSIX, help="VSIX 文件路径")
    parser.add_argument(
        "--extension-root",
        type=Path,
        default=DEFAULT_EXTENSION_ROOT,
        help="Trae 扩展源码及产物根目录",
    )
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="写入同目录 <vsix>.sha256 校验清单",
    )
    args = parser.parse_args(argv)
    report = verify_vsix(args.vsix.resolve(), extension_root=args.extension_root.resolve())
    if args.write_manifest:
        manifest_path = Path(str(args.vsix.resolve()) + ".sha256")
        lines = [f"{report['sha256']}  {args.vsix.name}"]
        verified_files = report["verified_files"]
        if isinstance(verified_files, dict):
            for member, digest in sorted(verified_files.items()):
                lines.append(f"{digest}  {member}")
        manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"manifest={manifest_path}")
    print(
        json.dumps(
            {
                "ok": True,
                "version": report["version"],
                "size_bytes": report["size_bytes"],
                "sha256": report["sha256"],
                "verified_files_count": report["verified_files_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
