# `request_regression` 与 `return_stage` 落地（2026-10-13 第二十批）

基线起点：`fix@9c0f65a` + 本会话此前各批。本批按用户指示**回查项目文档**，把此前登记为"合同未冻结"的两项按分册冻结语义落地。

## 1. 为什么改判：文档其实冻结了语义

| 项 | 冻结位置 | 原文要点 |
| --- | --- | --- |
| `return_stage` | 功能 §4/§221、**功能 §15**、架构 02 §157、设计 §372 | 「退回阶段：形成新修订，将依赖下游标为需重跑，旧记录可查」；**「退回阶段与普通上游重试使用同一失效机制」**；「生成修订与下游需重跑清单，**不把沿用证据记为重新执行**」；「先展示下游需重跑」 |
| `request_regression` | 架构 05 §81；架构 03 §66/§117/§149；功能 §542 | 动作在问题/报告组；「处理中 → 待复测：登记修复版本与回归方式」；「回归关联：原证据、事实/假设、修复版本、**新Attempt/证据**、原关闭条件」；「修复关闭读取指定**新回归 Attempt**及可读证据」；「登记修复、**安排回归**及核对关闭依据」 |

## 2. 修了什么

| 项 | 触点 |
| --- | --- |
| `return_stage`：同一失效机制 + 影响面如实回执（依赖边来自已提交事实） | `src/aitest/application/execution/commands.py`、`bootstrap.py` |
| `request_regression`：只登记回归关联（不改状态/不建尝试/不关闭），参数仅 `issue_id`+`note` | `domain/review/defects.py`、`application/review/defect_management.py`、`application/review/saved_defects.py`、`interfaces/local/d_registration.py` |
| 回归关联不被静默丢弃（写侧序列化 + 读侧严格核对） | `application/review/saved_defects.py` |
| **真实缺陷**：重复声明同一证据曾写出不可读记录（后续修订整体失败），现按引用集合合并 | 同上 |

## 3. 验证

| 检查 | 结果 |
| --- | --- |
| 新增 `tests/unit/test_request_regression.py` | 6 passed |
| 新增 `tests/unit/test_default_return_stage.py` | 6 passed |
| 新增回归用例（重复证据不写坏记录） | 通过 |
| 受影响路径回归 | **142 passed / 0 failed / 0 error / 0 skipped**（144.14 秒） |
| `ruff` / `mypy`（strict，245 文件） / `check_docs_facts --full` | 通过，0 差异 |

## 4. 文档同步

- [项目现状](../../现状.md)：231 模块 / 70,016 行、tests 338 文件 / 77,875 行、节点 5181、**动作 57 → 59**、缺 handler 3 → 1。
- [待裁定清单](../05-待裁定/2026-10-13-剩余合同缺口与所需决定.md)：第 3、4 项改判为"已裁定并落地"并写明冻结原文。
- [整改索引](../README.md) 新增 21 号证据；新增[证据文档](../03-修复证据/2026-10-13-request-regression与return-stage.md)；CHANGELOG 与 validation README 新增条目。

## 5. 未做

- 其余待裁定项（详情读取环、C-03 解析规则、D-02 断言期望值、FR02 投影写入点、迁移前备份位置/跨进程维护态、R 保存侧动作名）本轮未动。
- 真实 Trae/掉电/完整 AC 未验收；`tests/acceptance/p1/status.json` 未改动；全量 5181 节点未跑完。
