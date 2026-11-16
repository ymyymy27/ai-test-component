# 测试报告 rep_37b1d493ed0f

- 结论：failed
- 运行：run_cb3591f3263c
- 范围：4/4
- 源码提交：未记录
- 规则版本：未记录
- 环境 Python：3.11.9
- 服务：未配置

AI 摘要未采用，以下为事实摘要
摘要：本次范围 4/4，通过 1，失败 1，阻塞或未完成 2，未覆盖验收项 3。

## 范围与覆盖
- 已选验收项：4
- 适用验收项：4
- 覆盖验收项：4
- 通过：1
- 失败：1
- 阻塞或未完成：2

## 未覆盖清单
- ai_d8662392a7ca
- ai_84d057be16e7
- ai_5eb0a64acb7c

## 分层计数
- L1：4 个用例
- L2：1 个用例
- L3：0 个用例

## 用例明细
| 用例 | 验收项 | 分层 | 结果 | 证据 |
| --- | --- | --- | --- | --- |
| case_52de62bf4433 | ai_d8662392a7ca | L1 | failed | - |
| case_f0d2b89133ac | ai_84d057be16e7 | L2 | blocked | - |
| case_b2775e9f9c4d | ai_0b38561dcde3 | L1 | passed | evidence/case_b2775e9f9c4d-0.log |
| case_0247672deb5a | ai_0b38561dcde3 | L1 | passed | evidence/case_0247672deb5a-0.log |
| case_210df3db980a | ai_5eb0a64acb7c | L1 | not_run | - |

## 失败证据
- case_52de62bf4433：未采集证据

## AI 分析
### 分析状态：generated
**观察**：运行 run_cb3591f3263c 状态为 cancelled；L1 构建失败（evidence/l1-build.log，exit_code=1，ok=false）；case_52de62bf4433 失败且无证据引用、消息为空；case_f0d2b89133ac 被阻塞，消息为“L1 检查未通过”；case_b2775e9f9c4d 与 case_0247672deb5a 通过并各有日志证据；case_210df3db980a 未运行；service_started=false；l1.entries 为空。
**证据引用**：evidence/l1-build.log、evidence/case_b2775e9f9c4d-0.log、evidence/case_0247672deb5a-0.log
**候选原因**：
- L1 构建失败（exit_code=1）导致依赖 L1 检查的用例被阻塞，并使运行被取消（把握程度：high）
- case_52de62bf4433 失败原因未记录（message 为空、无 evidence_refs），无法归因（把握程度：unknown）
- 运行在 18:57:00 心跳后即被取消，可能由外部取消或前置失败触发中止（把握程度：medium）
**尚未验证**：
- L1 构建日志中的具体编译/构建错误内容尚未读取
- case_52de62bf4433 的实际失败原因与执行环境状态未知
- 运行取消是人工触发还是失败后自动中止尚未确认
- case_210df3db980a 未运行是否因取消而跳过尚未确认
**下一步**：
- 读取 evidence/l1-build.log 定位构建失败的具体错误
- 补充 case_52de62bf4433 的失败日志与消息采集
- 确认运行取消的触发来源（人工/自动）
- 修复 L1 构建后重跑受影响的用例

## 问题
- issue_8b998626a8d0 [P2/pending] 用例失败：pytest 断言失败（发生 1 次）
- issue_b98e26ef7c31 [P2/pending] 用例失败：失败（发生 1 次）
- issue_c765a530faad [P2/pending] 用例失败：失败（发生 1 次）
- issue_f33452cb59f1 [P2/pending] 断言失败（发生 2 次）

## Mock 判定
- 未判定

## 验收意见
未填写验收意见

## 证据索引
| 引用 | 存在 | 字节 |
| --- | --- | --- |
| evidence/l1-build.log | 是 | 0 |
| evidence/case_b2775e9f9c4d-0.log | 是 | 8 |
| evidence/case_0247672deb5a-0.log | 是 | 6 |
