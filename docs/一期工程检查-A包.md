# 一期工程检查-A包

复核日期：2026-10-12；源码基线：`develop@845e697`；文档提交目标：`fix`。

本表已按实际代码重新复核，保留历史编号；“部分实现”不等于整项关闭，“待验收”不等于未写代码。项目规模、门禁结果、合同版本与验收数字统一查[项目现状](现状.md)；本轮源码清单、命令与反例见[复核证据](validation/p1-code-review-20261012/README.md)。

## 1. 主责与依据

协议与基础设施，主责 P1-FR16；牵头 P1-AC28。跨包协作与验收归属仍以[机器台账](../tests/acceptance/p1/status.json)及[接口总台账](接口对接/README.md)为准。

先读[阅读索引](项目文档/阅读索引.md)、[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[功能唯一判定口径](项目文档/一期/功能文档/01-功能文档.md#3-判定与统计唯一口径)和一期架构分册。此表不增删 FR/AC，也不覆盖字段、枚举和恢复合同。

## 2. 当前开放条目

| 编号／复核状态 | 已实现并核对的边界 | 剩余工作与整项关闭条件 | 源码触点／涉及验收 |
| --- | --- | --- | --- |
| A-02 · 部分实现／待宿主验收 | 生命周期锁、发现/连接、活动保活、CLI 派发及 MCP 七类普通保存已接。 | 受控人工宿主会话、未知旧启动处置、跨入口恢复和真实 Trae 排空/重连；核对实际实例与 writer_epoch，未知执行不得自动重放。 | `bootstrap.py；interfaces/local/core_worker.py；interfaces/tools/agent_relay.py`；AC19/26/27/31/34 |
| A-05 · 部分实现／待完整恢复验收 | CommitManifest v2、七字段 current、对象/幂等引用、业务索引、旧根与有界查询已有实现。 | 不确定发布的人工恢复产品入口；完整历史对象归属/大小、报告历史排序对拍、目标设备耐久与物理掉电。不能以启动自愈替代发布一致性。 | `infrastructure/file_store/commit_manifest.py；canonical_manifest.py；ordered_index.py；publication_backend.py`；AC27/28/34 |
| A-06 · 待真实环境验收 | 句柄与永久材料进入完整性/备份闭包，恢复先检查摘要、归属与路径链接。 | 完整 C/D 保存→换核心→备份/恢复，损坏备份和目标目录拒绝，有符号链接权限的实测；跳过项不能计通过。 | `infrastructure/file_store/backup.py；integrity.py；references.py`；AC16/28/35 |
| A-07 · 部分实现／待产品恢复 | spool 租约、活动守卫、抢救与控制恢复均已有组件。 | 实际活动/未知句柄核实与安全人工处置；确认抢救及全部 JSON/JSONL 引用后才能迁移或回收，不可读引用继续阻塞。 | `infrastructure/file_store/recovery.py；spool.py；maintenance.py`；AC28 |
| A-08 · 组件已注入／默认来源阻断 | 固定源码物化、路径映射和入口/解释器/来源组件存在；不能把注入当实际链路通过。 | 先修 C-15 快照/字段/目录错接及 C-16 语法/模块失败仍 verified，再验 editable/PYTHONPATH/绝对入口、构建/最小启动和 Windows 耐久/掉电；物化不证明环境/远端一致。 | `bootstrap.py；application/execution/action_resolver.py；infrastructure/adapters/execution/python_checks.py`；AC12/13/19/25/30 |
| A-09 · 部分实现／待全制品验证 | 模型结构化响应、spool、对象、诊断等共用写前过滤，JSON 编码变体已有回归。 | 附件、报告、portable 导出/诊断/备份的完整产品安全链；不安全材料登记缺口，凭据正文不得进入任何制品。 | `infrastructure/security.py；projections.py；application/evidence/external_imports.py`；AC07/16/32/35 |
| A-10 · 部分实现／待按动作对拍 | 能力门和连接状态持久化已实现；执行族接通不能等同全部动作已可用。 | 按每个动作真实端口依赖核对故障与恢复；verify_pending 默认缺业务解析/核验端口，D 发布族缺 handler。模型关闭仍须可用人工路径。 | `infrastructure/capabilities.py；bootstrap.py；infrastructure/connections.py`；AC26/32 |
| A-19 · 新增：可复现工程阻断 | uv.lock requires-python 为 ==3.13.*，pyproject.toml 为 >=3.11,<3.14。uv 0.11.8 的 --locked 安装退出 1。 | 按声明的 Python 范围重新解析并生成锁文件，干净环境重跑 uv sync --extra dev --locked，再验证 3.11/3.12/3.13；不手改生成锁文件。本轮只登记。 | `uv.lock（仓库根）；pyproject.toml；.github/workflows/ci.yml`；工程门禁／AC26/28 前置 |

## 3. 已修条目与验证边界

历史关闭问题保留原编号，索引见[历史修复证据](一期工程整改/03-修复证据/ABC包问题修复证据清单-2026-10-03.md)。

本轮没有修改产品源码或代填 `verified`。规则/合同/真实文件与进程组件通过，只证明各自测试边界；实际宿主、供应方/业务对象、物理掉电和完整 AC 需单独记录输入、软件/制品版本、预期、实际及证据位置。执行后可靠保存材料再发布结果，未知副作用不重放。

续接顺序见[整改计划](一期工程整改/02-整改计划/ABC剩余问题整改计划-2026-10-04.md)和[交接](一期工程整改/04-交接/交接说明-2026-10-12.md)。清理前原文及完成条件可用 `git show 845e697:docs/一期工程检查-A包.md` 恢复，完整 SHA-256 见[原文清单](validation/p1-code-review-20261012/documents-before.json)。
