# `request_regression`（安排回归）与 `return_stage`（退回阶段）落地（2026-10-13，第二十批）

## 0. 为什么本批把它们从"待裁定"改成"落地"

上一轮把这两项登记为"合同未冻结"。按用户指示**回到项目文档逐条核对**后，发现分册其实已经冻结了语义（此前只查了动作名与我的初读，漏了功能 §15 与架构 03 的缺陷章节）：

| 项 | 冻结位置与原文 | 结论 |
| --- | --- | --- |
| `return_stage` | 功能 §4/§221「退回阶段：形成新修订，将依赖下游标为需重跑，旧记录可查」；**功能 §15「退回阶段与普通上游重试使用同一失效机制」**；架构 02 §157「退回阶段生成修订与下游需重跑清单，不把沿用证据记为重新执行」；设计 §372「退回阶段先展示下游需重跑」 | 机制＝上游重试；入口只需**如实回执影响面** |
| `request_regression` | 架构 05 §81 动作表（问题/报告组）；架构 03 §66「处理中 → 待复测：登记修复版本与回归方式」；§117「回归关联：原证据、事实/假设、修复版本、**新Attempt/证据**、原关闭条件」；§149「修复关闭读取指定**新回归 Attempt**及可读证据」；功能 §542「登记修复、**安排回归**及核对关闭依据」 | ＝**安排回归**：登记回归关联，不创建 Attempt、不执行、不授权、不关闭 |

## 1. 交付内容

| 项 | 说明 | 触点 |
| --- | --- | --- |
| `return_stage` | 与 `retry_step` 同一条执行/失效路径 + 影响面回执：执行前按**已提交事实的依赖边**算传递下游，执行后只回执**实际被登记为需重跑**的子集；无下游/未登记失效时如实给出 `downstream_note`；旧尝试保留、`inherited_evidence_not_reexecuted=true` | `application/execution/commands.py`（`return_stage`、`_downstream_steps`、`_facts`）、`bootstrap.py` |
| `request_regression` | 参数仅 `{issue_id, note?}`；目标与标准全部取自问题记录自身的修复三元组与 links；写入 `links.regressions`（回归关联）与域字段 `regression_request_id`；**不改状态、不建尝试、不关闭**；同意图幂等 | `domain/review/defects.py`（`request_regression` + 字段）、`application/review/defect_management.py`、`application/review/saved_defects.py`、`interfaces/local/d_registration.py` |
| 回归关联读回严格化 | `issue_links` 读回 `regressions` 并核对字段集合与状态；写侧不再把安排**静默丢弃**（此前 `issue_to_payload` 只序列化 5 个 link 键，安排会消失） | `application/review/saved_defects.py` |
| 证据引用集合化（顺带修真实缺陷） | 调用方重复声明同一证据时，写侧按引用集合合并；此前会写出 `["evidence-1","evidence-1"]`，而读侧严格拒绝重复值 ⇒ **该问题的后续任何修订都会整体读失败**（记录被写坏） | 同上（`issue_to_payload`）+ 回归用例 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 新增 `tests/unit/test_request_regression.py` | **6 passed**：安排回归写关联但不改状态/不加尝试；同意图重放幂等；域守卫与用例层各证"关闭仍须新尝试"；未登记修复/非 active 处置/缺原复现入口逐项拒绝；未知参数与修订冲突显式失败 |
| 新增 `tests/unit/test_default_return_stage.py` | **6 passed**：委派与传递下游清单、只回执实际登记需重跑的子集、无下游/未登记失效如实说明、首发尝试与非法参数拒绝、**读不到已提交事实即拒绝（先算影响面后执行）**、真装配广告 |
| 新增回归用例（`test_saved_defect_management.py`） | 重复声明证据不再写出不可读记录；记录随后仍可继续修订 |
| 受影响路径回归（FR15 + 报告/导出 + C 侧入口 + 维护/备份/迁移 + 面板白名单 + 注册表 + 本地协议 + 架构 + 验收台账） | **142 passed / 0 failed / 0 error / 0 skipped**（144.14 秒，[JUnit](../../validation/p1-defect-fix-20261012/2026-10-13-request-regression-return-stage.xml)） |
| `ruff check src tests scripts` / `mypy`（strict，245 文件） / `check_docs_facts --full` | 通过，0 差异 |

## 3. 文档同步

- [项目现状](../../现状.md)：231 模块 / 70,016 行、tests 338 文件 / 77,875 行、节点 5181、**默认可派发动作 57 → 59**；缺 handler 清单 **3 → 1**（只剩 `test_connection` 这一条件能力）。
- [待裁定清单](../05-待裁定/2026-10-13-剩余合同缺口与所需决定.md)：第 3、4 项标注"**已裁定并落地**"并写明冻结原文与实现方式。

## 4. 仍未闭合（如实登记）

- **test_connection**：仅在配置连接器时作为 API 内建分支广告，未配置时不可宣称可用（无需新代码）。
- 详情读取环、C-03 默认核验解析规则、D-02 断言期望值来源、FR02 投影写入点、迁移前备份位置/跨进程维护态、R 保存侧动作名：仍在待裁定清单（其余项本轮未动）。
- 真实 Trae、物理掉电、完整 AC 未验收；`tests/acceptance/p1/status.json` 未改动。全量 5181 节点未跑完。
