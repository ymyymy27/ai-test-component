# 一期续修最终检查（2026-10-14）

续修起点仍为 `fix@76ab7d2`，本批承接 [2026-10-13 修复与取证](../p1-completion-repair-20261013/README.md)。原批所有失败、局部结果及源指纹保留。主责分册/验收台账未改，不代填真实 AC。

## 新增补齐

- **桥接拒绝安全**：`ensureCore` 不从原 stderr JSON 复制 `code`。已用虚构凭据反例证明旧实现会带回该字段（[修复前](stderr-counterexample.log)），修复后使用受控常量；[21 项桥接/宿主组件](host.log)通过。是假 CLI/mock vscode 接缝，不能证明真实 Trae。
- **接口参数合同**：ReadViewQuery 生成 Schema 的 oneOf 现明确 run/panel 必带 run_id、禁止交付字段；delivery 必带 delivery_id、禁止 run_id。非空白/控制字符约束与核心一致。标准 JSON Schema 验证器曾接受尾换行而模型拒绝（[反例](schema-validator-before.log)）；补显式控制字符否定后 [14 个正反向向量](schema-validator-verified.log)与模型一致，[17 项 Schema/MCP](schema.xml)通过。生成物由源码生成。
- **声明版本边界**：分别安装 uv 管理的 3.11.15/3.12.13，不覆盖已有用户的 python3.11/python3.12 可执行入口；三个独立环境均 `uv sync --extra dev --locked` 通过。3.11/3.12 的 Schema、有限 MCP、查询、验收登记、判定、迁移、交付与 stdio 组件各 [107 passed](compat311.xml) / [107 passed](compat312.xml)，不是这两个版本的全量，也不改变一期目标 Python 3.13。
- **制品再核对**：TS check/21 项组件、[VSIX](vsix-verified.log)、[sdist/wheel](build.log)、[隔离非 editable 安装冒烟](wheel-smoke.log)及[共享面板/制品内容](artifacts.log)通过。首次 VSIX 打包因新增 README 相对链接缺发布根失败（vsix.log）；改为已核对 origin 仓库的绝对 fix 分支链接后再打包通过，保留失败。

前一批已补的 R 源快照/证据冻结、同次 query_page、整个只读视图外层配对、准确交付提测关联、显式宿主上下文/迟到回执及持久迁移检查点闸门仍见原批取证。

## 全量检查与中断

2026-10-13 全量停在约 25%，没有完成回执/JUnit，原进程已不存在：[原中断登记](../p1-completion-repair-20261013/full-interrupted.json)与 [原日志](../p1-completion-repair-20261013/full.log)保留，不能登记通过或与重跑相加。

本批在独立干净锁定 3.13.0 环境重新执行全部 **5297 个节点**：[准确命令](full-command.json)、[运行状态](full-state.json)、[原输出](full.log)。当前执行中，结束前不写通过。单次后台进程隐藏启动，可跨会话核对 PID、命令与原状态；不并行其他重测试，不自动重放失败用例。

运行中已出现一个失败；按收集顺序定位为 `test_command_adapter_reaps_child_process_group`，见[定位记录](first-failure-locator.json)。完整错误回执尚未生成，不能提前认定是产品清理缺陷或夹具时限；保留整轮输出，结束后按实际原因修复并覆盖既有用例。

[源码指纹](tested-source-inventory.json)覆盖全部核心/测试/脚本与受改 TS；[facts.json](facts.json)记录规模/动作与版本。全文件指纹不等于逐行安全审计。

## 仍未证明

准确欠缺、事实来源、下一次输入及关闭条件见[剩余清单](../../一期工程整改/05-待裁定/2026-10-13-剩余合同缺口与所需决定.md#本次仍欠的可执行关闭条件)。默认核验结构化来源/配置、可信动态 R、交付验证权威写入、适用构建/显式最小启动与全部加载来源保存、受控宿主用户流、独立备份位置/完整 generation 迁移/物理掉电仍欠。真实 Trae、供应方/业务对象与全部 AC 没有新增 verified。

## 文档与恢复

CORE 1.85、AB 1.45、BC 0.49、CD 1.24、AC 1.9、BD 0.7 与台账 2.87 保持；新增参数 Schema 约束仍属这批未提交的同一合同版本。签署/last_verified_commit 不动，reviewing/partial/not_run 不升为真实对拍。

原基线哈希和备份仍见上一批；[逐文件处置/哈希](document-audit.json)、[台账未变](acceptance-unchanged.json)及[本地链接核对](document-links.json)已登记，最终结果回写后再刷新文件指纹。过时现行判断已替换，历史失败/阶段证据保留，没有为清理而删除有效追溯文件。改动原因见[本轮修改日志](../../修改日志/2026-10-14-一期欠缺边界续修与检查.md)。
