# 合同先行：materialize 返回实际路径映射与内容摘要（AB-001 1.34）

基线 `876d888`，`fix`。按 `docs/接口对接/AGENTS.md` 第 5.3 条"先改合同、再改代码"完成**合同侧澄清**；A 侧实现与夹具随后对齐。

## 1. 差异

[AB-001 第 3.4 节](../../接口对接/进行中/AB-001-端口与保存/contract.md) 一直要求
`SourceSnapshotPort.materialize(snapshot_id, destination)`"返回**实际路径映射与内容摘要**"，
但未固定键名；现行 [FileSourceSnapshotStore.materialize](../../../src/aitest/infrastructure/adapters/source_snapshot.py)
成功时只返回 `snapshot_id`/`destination`/`materialized`（相对路径列表）/`verified`/`state`，
拒绝时返回 `refused`，没有显式的期望→实际映射条目，也没有内容摘要字段（逐文件摘要只在 pinned 记录里）。

## 2. 合同澄清（AB-001 1.33 → 1.34）

- front-matter `contract_version: "1.34"`；
- 新增版本条目 1.34（置于版本记录首条）；
- 第 3.4 节签名文档补全成功/拒绝返回形状：
  - 成功：`snapshot_id`、`destination`、`verified`、`state`（既有键不变）＋ `paths`（按固定清单顺序，每项 `relative_path` 期望来源路径、`actual_path` workdir 内解析出的实际路径、`sha256`、`size`）＋ `content_digest`（对 `paths` 规范 JSON 字节取 `sha256:` 摘要，证明本次物化出的实际来源字节集合）；
  - 拒绝：保持 `refused`，不返回 `content_digest`。
- 明确用途与边界：该摘要供 C 在 start 生成 `source_binding_digest`（[BC-001 第 6 节](../../接口对接/进行中/BC-001-PreparedRun/contract.md)：固定 workdir 的"期望→实际路径映射"摘要），**不替代** B 在 prepare 冻结的 `resolved_input_digest`；
- 台账同步：接口总台账 AB-001 行版本改 `1.34`，并按目录约定追加 2026-10-09 修改日志；
- 状态不变：`reviewing` / `partial` / `not_run`，真实 AC 未改变；B 侧忽略未知键即可接入，属**兼容新增**。

## 3. 未做与下一步

- 本轮**未改产品代码**：A 侧 `materialize` 的 `paths`/`content_digest` 实现与夹具对齐、以及 C 侧 start 物化→实际路径解析→`source_binding_digest` 属下一批；
- 键名为本次澄清内容，B/C 双方确认按目录流程回写合同；
- ABC 仍 21 整项，真实 AC 仍 0 verified。
