# CD-001 D侧评审确认

日期：2026-10-02  
评审人：D包 郭  
结论：确认冻结 `aitest.execution-facts/1.0`，无字段或展示阻塞  

## 1 六项确认

| 项目 | D侧结论 | 消费约束 |
| --- | --- | --- |
| Schema 版本 | 接受 | 只消费 `aitest.execution-facts/1.0`，未知主版本走兼容错误 |
| 多流游标 | 接受 | stdout／stderr 使用独立 `output_cursors`，不共享 offset |
| timeout | 接受 | 保留 `pending_verification`，不自动重放未知副作用 |
| 非 UTF-8 | 接受 | 按摘要和字节读取，不强制文本解码 |
| spool 边界 | 接受 | 只读提交快照和对象引用，不读内部 spool 路径 |
| 变更流程 | 接受 | 字段、枚举或语义变化重新走契约 PR |

## 2 evidence_level 边界

`EvidenceFact.evidence_level` 是 C 对单条证据可追溯程度的发布事实。D 可以展示、引用和按未知值降级，但最终 Run 级证据等级 A／B／C／D 由 D 的 `DecisionFacts` 和 `evaluate_review()` 派生。

D 的等级输入至少包括：

- S／M 与 E／R／V／P／F／U／H；
- 当前 Attempt、依赖失效和依据确认；
- 源码身份与执行来源核对；
- 关键链路、必需证据、独立核验、Mock、未知和缺口；
- 环境、规则、验收范围和政策修订。

## 3 后续接入

- D 实现 ExecutionFacts 到 `DecisionFacts` 的唯一适配。
- D 校验 F⊆H、H 引用当前 Attempt 和有效证据。
- 七类夹具全部用于消费者合同测试。
- 接入 A 的一致快照和对象读取后再登记真实联调证据。

本次确认不代表 D 消费代码已完成，也不代表 P1-AC 已通过。
