"""A-08：固定字节 → 冻结身份 → 实际加载来源 的真实解释器核对。

使用当前真实 Python 解释器起受控子进程，覆盖一期执行来源合同 §12：
- 物化目录内项目模块的实际加载文件与摘要核对（verified）；
- PYTHONPATH 显式映射与 sys.path/解释器身份事实；
- 物化输入被改写后摘要不符 → mismatch 且阻塞；
- 导入失败/动态未知 → unverified 缺口，不冒充通过；
- 项目模块被范围外同名包覆盖 → mismatch 且阻塞；
- 模块名白名单与探针协议破坏检测；
- pin → materialize → probe 完整链路。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.infrastructure.adapters.execution.python_checks import (
    PythonLoadSourceProbe,
    PythonSourceProbeError,
    evaluate_source_binding,
    file_sha256,
)
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore


@pytest.fixture
def materialized_tree(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """返回（物化目录, 模块文件, 模块名, 文件摘要）。"""
    root = tmp_path / "materialized"
    pkg = root / "demo_pkg_a08"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("VALUE = 'pinned'\n", encoding="utf-8")
    module = pkg / "mod.py"
    module.write_text("ANSWER = 42\n", encoding="utf-8")
    digest = file_sha256(module)
    return root, module, "demo_pkg_a08.mod", digest


def test_verified_chain_pinned_bytes_match_actual_loaded_source(
    materialized_tree: tuple[Path, Path, str, str],
) -> None:
    root, _module, name, digest = materialized_tree
    probe = PythonLoadSourceProbe(executable=sys.executable)
    fact = probe.probe(materialized_root=root, modules=(name,))

    assert fact.probe_error is None
    assert Path(fact.executable).name.lower().startswith("python")
    assert fact.version
    assert str(root) in fact.project_scope_entries
    loaded = {item.module: item for item in fact.modules}[name]
    assert loaded.load_error is None
    assert loaded.file is not None
    assert Path(loaded.file).is_relative_to(root)
    assert loaded.sha256 == digest

    verdict = evaluate_source_binding(
        fact, expected_modules={name: digest}, project_root=root
    )
    assert verdict.state == "verified"
    assert verdict.blocked is False
    assert verdict.reasons == ()


def test_pythonpath_mapping_recorded_and_applied(
    tmp_path: Path, materialized_tree: tuple[Path, Path, str, str]
) -> None:
    root, _module, name, digest = materialized_tree
    extra = tmp_path / "deps"
    dep_pkg = extra / "dep_a08"
    dep_pkg.mkdir(parents=True)
    (dep_pkg / "__init__.py").write_text("DEP = 1\n", encoding="utf-8")

    probe = PythonLoadSourceProbe()
    fact = probe.probe(
        materialized_root=root,
        modules=(name, "dep_a08"),
        extra_pythonpath=(str(extra),),
    )
    assert fact.probe_error is None
    assert str(extra) in fact.pythonpath_env
    assert str(root) in fact.pythonpath_env
    loaded = {item.module: item for item in fact.modules}
    assert loaded["dep_a08"].file is not None
    assert str(extra) in loaded["dep_a08"].file
    # 依赖包不属冻结项目范围：只核对项目模块，依赖事实单独存在。
    verdict = evaluate_source_binding(
        fact, expected_modules={name: digest}, project_root=root
    )
    assert verdict.state == "verified"


def test_rewritten_materialized_input_blocks_startup(
    materialized_tree: tuple[Path, Path, str, str],
) -> None:
    root, module, name, digest = materialized_tree
    # 物化后字节被改写（原仓库修改/缓存污染），冻结摘要仍是旧值。
    module.write_text("ANSWER = 999  # tampered\n", encoding="utf-8")
    fact = PythonLoadSourceProbe().probe(materialized_root=root, modules=(name,))
    verdict = evaluate_source_binding(
        fact, expected_modules={name: digest}, project_root=root
    )
    assert verdict.state == "mismatch"
    assert verdict.blocked is True
    assert any("摘要" in reason for reason in verdict.reasons)


def test_external_same_name_module_cannot_pass(
    tmp_path: Path, materialized_tree: tuple[Path, Path, str, str]
) -> None:
    root, module, name, digest = materialized_tree
    # 外部目录放同名模块；外部路径排在物化根之前时，项目模块被覆盖。
    external = tmp_path / "external"
    shadow = external / "demo_pkg_a08"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("", encoding="utf-8")
    shadow_module = shadow / "mod.py"
    shadow_module.write_text("ANSWER = 'outside'\n", encoding="utf-8")

    # 直接把外部路径置于 sys.path 首位（模拟 editable 指回原仓库）。
    fact = PythonLoadSourceProbe().probe(
        materialized_root=root,
        modules=(name,),
        extra_pythonpath=(str(external),),
    )
    # 探针保证物化根优先，故默认应仍加载物化字节；再构造"外部抢先"事实：
    # 通过手工 sys.path 顺序验证 verdict 对范围外文件零容忍。
    loaded = {item.module: item for item in fact.modules}[name]
    assert Path(loaded.file).is_relative_to(root)  # 默认映射下项目范围获胜

    # 显式构造外部抢先子进程：把外部路径作为物化根以外的第一映射。
    fact_external = PythonLoadSourceProbe().probe(
        materialized_root=external,
        modules=(name,),
    )
    verdict = evaluate_source_binding(
        fact_external, expected_modules={name: digest}, project_root=root
    )
    assert verdict.state == "mismatch"
    assert verdict.blocked is True
    assert any("范围外" in reason for reason in verdict.reasons)


def test_unimportable_module_is_unverified_gap(
    materialized_tree: tuple[Path, Path, str, str],
) -> None:
    root, _module, name, digest = materialized_tree
    fact = PythonLoadSourceProbe().probe(
        materialized_root=root, modules=(name, "no_such_module_a08")
    )
    verdict = evaluate_source_binding(
        fact,
        expected_modules={name: digest, "no_such_module_a08": "0" * 64},
        project_root=root,
    )
    assert verdict.state == "unverified"
    assert verdict.blocked is False
    assert any("no_such_module_a08" in reason for reason in verdict.reasons)


def test_invalid_module_name_rejected_without_execution(
    materialized_tree: tuple[Path, Path, str, str],
) -> None:
    root, _module, _name, _digest = materialized_tree
    with pytest.raises(PythonSourceProbeError):
        PythonLoadSourceProbe().probe(
            materialized_root=root, modules=("demo_pkg_a08.mod; import os",)
        )


def test_probe_protocol_break_raises(tmp_path: Path) -> None:
    # 以一个脚本冒充解释器：输出不含协议标记，必须报错而非猜测。
    fake = tmp_path / "fake_python.py"
    fake.write_text("print('nothing useful')\n", encoding="utf-8")
    probe = PythonLoadSourceProbe(executable=str(fake))
    with pytest.raises(PythonSourceProbeError):
        probe.probe(materialized_root=tmp_path, modules=("json",))


def test_full_chain_pin_materialize_probe(tmp_path: Path) -> None:
    source = tmp_path / "src"
    pkg = source / "chain_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "logic.py").write_text("OK = True\n", encoding="utf-8")

    workspace = tmp_path / "ws"
    store = FileSourceSnapshotStore(workspace)
    pinned = store.pin(canonical_path=str(source), purpose="attempt-1")
    snapshot_id = str(pinned["snapshot_id"])
    expected_digest = next(
        str(item["sha256"])
        for item in pinned["files"]
        if str(item["relative_path"]) == "chain_pkg/logic.py"
    )
    destination = tmp_path / "run-src"
    materialized = store.materialize(snapshot_id, str(destination))
    assert materialized["verified"] is True

    fact = PythonLoadSourceProbe().probe(
        materialized_root=destination, modules=("chain_pkg.logic",)
    )
    verdict = evaluate_source_binding(
        fact,
        expected_modules={"chain_pkg.logic": expected_digest},
        project_root=destination,
    )
    assert verdict.state == "verified"
    assert verdict.blocked is False


# ----- 复核补强：范围事实准确性、空期望与 Windows 大小写归属 --------------


def test_scope_entries_exclude_prefix_sibling_directory(tmp_path: Path) -> None:
    """兄弟目录与物化根共享字符串前缀时，不得被记为项目范围内路径。"""
    root = tmp_path / "materialized"
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    sibling = tmp_path / "materialized-evil"
    (sibling / "pkg").mkdir(parents=True)
    (sibling / "pkg" / "mod.py").write_text("EVIL = 1\n", encoding="utf-8")

    fact = PythonLoadSourceProbe().probe(
        materialized_root=root,
        modules=("pkg",),
        extra_pythonpath=(str(sibling),),
    )
    assert fact.probe_error is None
    # 旧实现按 startswith(root) 比较，会把 sibling 误报为范围内。
    normalized = {os.path.normcase(entry) for entry in fact.project_scope_entries}
    assert os.path.normcase(str(sibling)) not in normalized
    root_str = os.path.normcase(str(root))
    assert any(
        entry == root_str or entry.startswith(root_str + os.sep)
        for entry in normalized
    )


def test_empty_expected_bindings_never_verify(tmp_path: Path) -> None:
    """期望绑定为空说明没有发生核对，不得返回 verified 冒充通过。"""
    root = tmp_path / "materialized"
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    fact = PythonLoadSourceProbe().probe(materialized_root=root, modules=("pkg",))
    verdict = evaluate_source_binding(fact, expected_modules={}, project_root=root)
    assert verdict.state == "unverified"
    assert verdict.blocked is False
    assert verdict.reasons


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows 文件系统大小写不敏感语义"
)
def test_windows_case_insensitive_scope_match(tmp_path: Path) -> None:
    """project_root 与子进程报告路径仅大小写不同时，不得误判为范围外。"""
    root = tmp_path / "Materialized"
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    init_file = pkg / "__init__.py"
    init_file.write_text("VALUE = 1\n", encoding="utf-8")
    fact = PythonLoadSourceProbe().probe(materialized_root=root, modules=("pkg",))

    verdict = evaluate_source_binding(
        fact,
        expected_modules={"pkg": file_sha256(init_file)},
        project_root=str(root).upper(),
    )
    assert verdict.state == "verified"
    assert verdict.blocked is False
