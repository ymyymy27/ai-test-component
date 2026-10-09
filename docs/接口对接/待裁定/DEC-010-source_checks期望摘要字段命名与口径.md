# DEC-010：`SourceCheckRequest.expected_source_binding_digest` 的命名与口径

状态：**待裁定**（候选方案未生效）

提出日期：2026-10-09

提出方：C 包（应用用例侧复核人）

受影响方：A（存储与端口）、B（`PreparedRun` 契约术语提出方）、C（来源核验消费方）

责任方：B（术语提出方）＋ C（实现方）

下一动作：B 侧确认采取方案 A 或 B；C 侧在接线前完成改名或探针改造

阻塞影响：**C 在 start 前的来源核验接线**（`application/execution/source_checks.py` 目前只有测试构造、无生产装配；未接线前不产生线上误判，改动成本最低）

影响合同：`BC-001`（第 6 节已定义 `source_binding_digest`）＋ `AB-001`（第 3.4 节 1.34 已定义 `materialize.content_digest`）

## 1. 待裁定问题

`SourceCheckRequest.expected_source_binding_digest`（代码内类型，**无合同条目**）在 `_derive_state` 中与探针的**单个** `observed_source_digest` 比较：

```python
if observation.observed_source_digest != request.expected_source_binding_digest:
    return SourceVerificationState.MISMATCH, ("source_digest_mismatch",)
```

而 `BC-001` 第 6 节把 `source_binding_digest` 定义为 **C 在 start 计算的"期望→实际路径映射"摘要**。字段名复用了合同术语、实际语义是**来源内容摘要**——两者不可能相等。全仓文档与接口合同检索 `SourceCheckRequest`、`expected_source_binding_digest`、`observed_source_digest` **零命中**，确认这是代码内命名与合同术语的口径不一致，而非文档遗漏。

## 2. 候选方案

- **方案 A（C 推荐）**：把该内部字段**改名**（如 `expected_source_content_digest`）并注明它**不等于** `BC-001` 的 `source_binding_digest`；映射级比较继续由 start 准入（物化映射摘要重放核对）承担。合同只补一句"内部类型不得复用合同术语"的约定，不改字段语义。
- **方案 B**：保留字段名与映射摘要含义，探针改为**逐项返回**期望→实际映射，`_derive_state` 改为映射级比较。表达力强，但要改 `SourceProbeObservation`、探针适配器与全部夹具。

## 3. 影响与边界

- 方案 A 只动 C 内部类型与夹具，**不改变任何已发布字段语义**；
- 方案 B 会改变 `SourceProbeObservation` 的形状，须同步 `AB-001`/`CORE-001` 并升版本；
- 两种方案都**不**影响 `SourceCheckRequest.failure_class`/状态派生逻辑本身。
