# DEC-012：start 前来源失败的对外错误码与原因标识登记

状态：**已裁定并归档（2026-10-09）；对外标识已登记**

裁定方：项目负责人（本会话获授权代决，按项目文档择优选型）

裁定日期：2026-10-09

**裁定结论：采用方案 A** —— 登记**一个**不可重试错误码 `SOURCE_BINDING_UNVERIFIED`，四类原因作为**结构化字段** `reason ∈ {entry_missing, source_mismatch, environment_unregistered, materialization_failed}`（不各立错误码）；缺口名沿用领域既有 `EvidenceGapKind.SOURCE_UNVERIFIED`；`next_step` 文案固定为"核对冻结来源与实际物化材料；如需更换入口或参数，请重新 prepare"。依据 `AGENTS.md` 第 4 节"能力降级按**具体动作**的真实依赖判断"与接口合同"相同字段不得重复另起定义"的既有做法（错误码表不膨胀，细节放结构化字段）。已写入 `AB-001` 1.36。

**实现状态：已实现、未接线** —— 回执由 `application/execution/start_source_binding.py` 给出（`code`/`retryable=False`/`gap`/`reason`/`next_step`，31 项回归覆盖四类原因与未知原因拒绝）；因 start 准入尚未接入任何入口，**当前不影响任何协议响应**；接线时按 1.36 的定义直接使用，无需再改标识。

受影响方：A（协议错误码维护）、C（start 侧回执实现）、D（展示与查询）

责任方：A（合同登记，已完成）＋ C（接线时消费）

下一动作：随 start 准入接线一并暴露；D 侧按 `CD-001` 引用本错误码，不另起定义

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
