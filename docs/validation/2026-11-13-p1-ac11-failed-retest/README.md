# P1-AC11 修复后复测仍失败

## 范围

真实产品核心（Windows 11 x64，0.4.10，提交 `3424753`）上的“修复后复测仍失败”场景。执行入口为 CLI `dispatch`，人工确认走核心原生窗口。

- 项目：`guided-935db490-d7d6-476f-ba25-60a260428424`
- 回归运行：`run-30ac84968498bb8bf7baa286dd69f47f03cef0d7ed771f11580c3e5c4b2963d0`
- 回归 Attempt：`execution-attempt-dfe2770eabb38a64c8ddb6b8e430c03a497b0d5d8aad06807c761fa60df763aa`
- 问题：`issue:pytest:split:ac11-retest`（源问题 `issue:pytest:f434ca836e25232fa22ab75a`）

## 输入与步骤

1. 将 `pkgA/order.py` 的 `total()` 改回 `1`，重新分析得到快照 `source-f0ae17e0…`，内容摘要 `sha256:a6aa2223…`。
2. 用同一计划登记并执行回归运行；第 1 步核验为 `mismatched`（`business_fact_mismatch:expected_outcome`），来源核验 `verified`，源摘要 `sha256:a6aa2223…`。
3. 从原问题冻结簇拆出子问题 `issue:pytest:split:ac11-retest`（节点 `pkgA.tests.test_order::test_create`）。
4. 子问题走 draft → awaiting_confirmation → confirmed → in_progress。
5. `record_fix`：`fix_version=sha256:a6aa2223…`，回归方式为同一计划/用例的真实运行。
6. `close_issue` 引用回归运行的新 Attempt `dfe277…`，断言结果 `failed`。

## 实际结果

- `close_issue` 返回问题修订 6，`status=in_progress`，未进入 `closed`。
- `fix_version` 保留为 `sha256:a6aa22233ee03ec92dbc6d8c4d63f9c7d14d74c7cacde0e105b06b26c57e3836`。
- `attempt_ids` 含失败回归 Attempt `execution-attempt-dfe277…`。
- `split_from` 指向源问题 `issue:pytest:f434ca836e25232fa22ab75a`；源问题保持不变。
- 回归 Attempt 的源核验为 `verified`，无缺口；业务核验 `mismatched`，因此关闭被拒。

## 判定

AC11 预期成立：修复版本与复测记录保留，复测失败使问题重新进入处理流程，而不是关闭或丢弃原失败。

## 边界

- 由 CLI `dispatch` + 核心原生确认驱动，不是 Trae 面板点击链路。
- 本次只升级 AC11；不据此升级其他问题/回归场景。
