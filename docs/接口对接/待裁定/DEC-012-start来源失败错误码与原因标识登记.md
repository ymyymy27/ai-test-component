# DEC-012：start 前来源失败的对外错误码与原因标识登记

状态：**待裁定**（对外标识未登记，当前不得暴露）

提出日期：2026-10-09

提出方：A 包（实现方）

受影响方：A（协议错误码维护）、C（start 侧回执实现）、D（展示与查询）

责任方：A（登记到 `AB-001`/`CD-001` 的错误码表）＋ C（消费）

下一动作：确认对外错误码名、`next_step` 文案与缺口名；A 侧登记后，C 侧才可把回执接入 LocalAPI/MCP/CLI 响应

阻塞影响：**start 准入的对外回执**（实现已给出 `code`/`retryable`/`gap`/`next_step`，但未接任何入口，因此当前不影响协议响应）

影响合同：`AB-001`（错误码/回执）＋ `CD-001`（对外展示）

## 1. 待裁定问题

按 `AB-001` 1.35 的 4A，start 前四类失败要给出"不可重试阻塞"回执。实现中使用了两类**新标识**，均**未在合同登记**：

1. 错误码 `SOURCE_BINDING_UNVERIFIED`（现为 `ValueError` 子类的类属性，**不是**协议错误码）；
2. 四类原因字符串：`entry_missing`、`source_mismatch`、`environment_unregistered`、`materialization_failed`。

缺口名 `source_unverified` 取自领域既有枚举 `EvidenceGapKind.SOURCE_UNVERIFIED`，**无需新登记**。

## 2. 候选方案

- **方案 A（C 推荐）**：在 `AB-001` 错误码表登记 `SOURCE_BINDING_UNVERIFIED`（不可重试）+ 四类 `reason` 作为**结构化字段**（不是独立错误码），`next_step` 文案由合同固定；`CD-001` 只引用不另起定义。
- **方案 B**：四类失败各自登记为独立错误码（更细，但错误码表膨胀，且与"按具体动作降级"的既有做法重复）。

## 3. 边界

- 登记前，实现的回执**不在任何入口暴露**（`src/` 内该模块零引用）；
- 两种方案都不改变既有错误码与既有响应语义；
- 登记完成后，`AB-001` 版本提升，并在接口台账与修改日志同步。
