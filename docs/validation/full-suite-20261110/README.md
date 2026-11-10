# 全量 Python 测试记录（2026-11-10）

## 命令

```bash
.venv/Scripts/python.exe -m pytest -q --import-mode=importlib -n 8 --dist loadfile
```

`--import-mode=importlib` 用于规避 `tests/contracts` 与 `tests/unit` 同名测试模块的收集冲突。

## 结果

- 收集：**6104** 个节点。
- 最终：**6101 passed / 3 skipped / 0 failed**，退出码 **0**。
- 首轮曾出现 41 项失败；其中 35 项是过期夹具/契约，已更新；6 项核心问题已修复。

## 本轮修复

### 源码固定字节闭包（4 项）

- `source_record_files` 现在会从 `project_understanding.source_snapshot_id` 追溯已保存的 `source_snapshot`，校验其 pinned 元数据与实际 blob，并把 `snapshots/<id>.json` 与 `snapshots/blobs/<sha256>` 纳入当前提交的文件闭包。
- `FileCommitStore.verify_material` 始终按 `require_verified=True` 校验变更记录的 pinned source；声明了 `source_material_files` 时，必须与实际变更记录引用的文件集合完全一致。
- 删除 blob 后 `read_current(verify_material=True)` 会拒绝；新清单不允许通过空 `source_material_files` 省略固定 blob；旧清单删除 blob 后同样拒绝。
- 没有本地 `source_snapshot` 的合成/旧理解记录不增加闭包文件，但不会伪造来源。

### 授权消费者（2 项）

- 两个测试改为先创建 `on_demand` 运行，再针对该运行的步骤准备授权动作与挑战，避免用 full 运行的 action 去比对 on_demand 运行。
- `initial_basis` 的准备/登记使用唯一 request/intent，避免固定 `prepare_request_id` 冲突；断言语义不变。
- `full` 运行的运行时修订仍按合同拒绝（`full_runtime_revision_not_allowed`）。

### 其它对齐

- 普通发布机制测试改用 `on_demand`；`full` 必须有覆盖契约与受控发布证明。
- 单次完成尝试的步骤按 `pending_verification` 断言。
- 面板 `PLAIN_WRITE_ACTIONS` 与扩展白名单逐项一致。

## 边界

- 这是组件/集成层全量，不是真实 Trae、真实 Tk 原生确认、真实模型供应商、掉电恢复、其他编辑器或跨机导出验收。
- 全量通过不等于 AC 通过，也不等于一期完成；验收登记未改。
- 面板 Playwright 31/31、扩展测试 35/35 通过。
