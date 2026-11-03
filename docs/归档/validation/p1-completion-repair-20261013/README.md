# 一期继续补齐与验证（2026-10-13）

起点 `fix@76ab7d24c68a590de44d12fd6f749f5f98fcc6f4`。用户要求继续修复可修部分、检查可检查部分，并补当前欠缺/仍未证明的边界。规范仍取一期现行分册；没有修改主责需求或 AC 登记，真实验收不能用组件结果代替。

## 已补的代码边界

| 边界与依据 | 修复后的行为 | 当前证明范围 |
| --- | --- | --- |
| C-17/D-14：FR14、功能 §15 / 报告冻结输入；AC21/24/27/35 | 合格 R 消费保留准确源执行快照与选择引用；报告冻结源证据；导出只读冻结源，不随当前源运行漂移。旧无源指针明确缺口，坏归属/重复冲突拒绝；缺附件不生成假空文件。 | 类型组件资格接缝 + 真实文件对象/ZIP。默认动态环境/目标依赖仍未知，未证明真实 R 资格。 |
| A-05/D-05：存储 §13 / 面板快照与事件；AC27/28/34 | SubstrateRecordReader 从同一次 query_page 得到行与边界，避免两次读取拼接；read_view 在整个 DTO 读取前后核对外层 commit/event/generation/index_version 未漂移。 | 原查询回归、漂移拒绝与默认已保存运行的真实核心装配读取；外层整体边界和内层执行事实快照字段分开保留。 |
| B-13/D-05：FR02 / 准确提测引用 | 交付视图核对准确记录身份，附带的提测必须绑定同一 delivery_id / delivery_record_revision，不能混入同项目其他交付。 | 已有交付 DTO/读取回归。verified_in_scope 权威生产投影仍欠，未从自述推导。 |
| D-06/A-02：FR17 / 宿主薄适配 | 协商式 read_view 已装配，显式项目/运行配置送 projectContext；面板按准确请求标签归位，切换清空旧数据/拒绝迟到回执，能力取 doctor；只读主屏展示核心 DTO，不重算结论。 | 13 项真 Chromium 浏览器组件 + 4 项 mock vscode 上下文接缝；未证明实际 Trae 兼容/完整用户流。 |
| A-02/A-09：入口安全与可靠结果 | 双输出各限 8 MiB UTF-8 字节；异常退出/数组/身份不符不能成为成功，超限/超时可识别，原始 stderr 不回显。 | 16 项真实 Python CLI 进程夹具，覆盖 stdout/stderr Unicode 超限、错误退出/身份、超时；是假 CLI，不是真核心/宿主验收。 |
| A-07：FR16 / 存储 §6/§9 | 持久 prepared 未成功登记、rollback_pending 或坏检查点在新核心仍阻塞普通写；只读诊断/受控迁移保留，不自动重放、不把重启当完成。 | 真实文件与两次核心装配，含未知状态；已有迁移/回滚回归。未知活动、完整 generation 切换/独立位置与物理掉电未证明。 |
| C-18/A-02：人工边界与只读 MCP | select_case_reuse 入统一 HUMAN_ACTIONS；aitest_read_view 仅在核心广告 read_view 时提供，有限选择器不允许覆盖原项目/绑定身份，旧核心降级。 | MCP 假传输与既有真实 stdio 检查；7 个原普通材料写白名单不扩充，relay 不代签人工选择。 |

## 本轮检查

- [202 项受影响路径整合回归](targeted.xml)：202 passed，失败/错误/跳过为 0；其源版本先于新增 2 个维护状态参数，不能叠加成全量。
- [最终持久维护分支](maintenance-verified.xml)：3 passed，15 deselected；[生成 Schema](schema.xml)：1 passed。
- [面板构建](panel-build.log)与[浏览器](panel-verified.log)：13 passed；[桥接/宿主组件](host-final.log)：20 passed。
- [ruff](ruff-verified.log)、[mypy](mypy-verified.log)、[版本](versions.log)、[锁文件](lock.log)已通过；249 个 strict 文件。
- [sdist/wheel 构建](build.log)、[隔离 wheel 冒烟](wheel-smoke.log)、[VSIX](vsix-final.log)与[制品内容/共享面板同字节](artifacts.log)通过。wheel 冒烟是新环境非 editable 安装；没有实际 Trae 安装/运行证明。
- 本轮全量中断在约 25%，没有完成回执/JUnit，未登记通过：[中断登记](full-interrupted.json)、[命令](full-command.json)与[原输出](full.log)保留。后续修复、兼容检查及独立干净重跑见[2026-10-14 续检](../p1-completion-repair-20261014/README.md)。
- [facts.json](facts.json)记录实际规模/环境，完整测试源码指纹为 [tested-source-inventory.json](tested-source-inventory.json)。不把全文件哈希清点称作逐行安全审计。

## 失败与修复记录

[second.log](second.log) 是新增测试导入错用 `_Stack`，收集失败；[third.log](third.log) 错取导出记录引用字段，1 failed/12 passed；[fifth.log](fifth.log) 错写维护错误码大小写，1 failed/36 passed；[maintenance-final.log](maintenance-final.log) 错写异常基类，1 failed/2 passed。这些是新增测试夹具/断言问题，修正为既有 `_Harness`、保存导出记录、已发布 `maintenance_required` 与 MigrationError；相应复测保留，不修改生产语义迁就断言。

[panel.log](panel.log) 1 failed/12 passed：原事件测试发送未请求的回执，按新请求归位保护应拒绝。修正模拟宿主为每次真实请求回显标签，保留拒绝保持原游标/事件的既有断言，新增迟到/外项目/上下文切换回归后 13 passed。早期 lint/type 诊断保留为 `*-preliminary.log`/`*-second.log`；临时错误覆盖原 contracts/views.py 被 mypy 检出并恢复原字节，新选择器放入独立 read_views.py，原 Response/CoverageDTO 无变更。

## 仍欠的输入与证明

逐项主责、准确缺口、下一次输入及关闭检查见[剩余边界](../../一期工程整改/05-待裁定/2026-10-13-剩余合同缺口与所需决定.md#本次仍欠的可执行关闭条件)。默认核验结构化来源/配置、真实动态 R、交付验证权威写入、适用构建/最小启动与完整加载来源保存、受控宿主业务流/主动作、独立备份/完整恢复仍欠。真实 Trae、供应方/业务对象、物理掉电与全部 AC 没有新增 verified 登记。旧签署/last_verified_commit 保持。

## 文档与恢复

[原文哈希](documents-before.json)，本机可恢复备份 `.runtime/p1-completion-before-76ab7d2.zip`。全部整改资料保留原阶段输入/结果并加本次入口；当前四包/SOT/接口与剩余清单精确修订，删除失效现行说法，未删除历史失败。没有找到需要删除的独立过时文件；不能为满足清理而删除真实证据。最终逐项处置/文档哈希会随本轮结果登记。
