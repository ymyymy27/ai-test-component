# 一期续修最终检查（2026-10-14）

续修起点仍为 `fix@76ab7d2`，本批承接 [2026-10-13 修复与取证](../p1-completion-repair-20261013/README.md)。原批所有失败、局部结果及源指纹保留。主责分册/验收台账未改，不代填真实 AC。

## 新增补齐

- **桥接拒绝安全**：`ensureCore` 不从原 stderr JSON 复制 `code`。已用虚构凭据反例证明旧实现会带回该字段（[修复前](stderr-counterexample.log)），修复后使用受控常量；[21 项桥接/宿主组件](host.log)通过。是假 CLI/mock vscode 接缝，不能证明真实 Trae。
- **接口参数合同**：ReadViewQuery 生成 Schema 的 oneOf 现明确 run/panel 必带 run_id、禁止交付字段；delivery 必带 delivery_id、禁止 run_id。非空白/控制字符约束与核心一致。标准 JSON Schema 验证器曾接受尾换行而模型拒绝（[反例](schema-validator-before.log)）；补显式控制字符否定后 [14 个正反向向量](schema-validator-verified.log)与模型一致，[17 项 Schema/MCP](schema.xml)通过。生成物由源码生成。
- **声明版本边界**：分别安装 uv 管理的 3.11.15/3.12.13，不覆盖已有用户的 python3.11/python3.12 可执行入口；三个独立环境均 `uv sync --extra dev --locked` 通过。3.11/3.12 的 Schema、有限 MCP、查询、验收登记、判定、迁移、交付与 stdio 组件各 [107 passed](compat311.xml) / [107 passed](compat312.xml)，不是这两个版本的全量，也不改变一期目标 Python 3.13。
- **制品再核对**：TS check/21 项组件、[VSIX](vsix-verified.log)、[sdist/wheel](build.log)、[隔离非 editable 安装冒烟](wheel-smoke.log)及[共享面板/制品内容](artifacts.log)通过。首次 VSIX 打包因新增 README 相对链接缺发布根失败（vsix.log）；改为已核对 origin 仓库的绝对 fix 分支链接后再打包通过，保留失败。

前一批已补的 R 源快照/证据冻结、同次 query_page、整个只读视图外层配对、准确交付提测关联、显式宿主上下文/迟到回执及持久迁移检查点闸门仍见原批取证。

## 进一步复核：ZIP 路径守卫已修

运行全量期间只做纯内存路径探针，不启动其他重测试或改动冻结产品源码。实际 `_bundle`、已验证形状的 EvidenceFact、合成报告上下文与内存对象读取共同证明：`../../escape`、反斜线穿越、`CON` 和大小写重名均被接受，见[生成反例](zip-path-counterexample.log)；同样材料的永久对象摘要正确时，公共旧制品回读守卫也未拒绝，见[旧包反例](zip-legacy-counterexample.log)。没有解压这些路径，不作为真实 AC 证据。

按[报告架构第 5 节](../../项目文档/一期/架构文档/03-报告与缺陷.md#5-范围环境和可携带导出)的规范相对路径/拒绝覆盖要求，隔离副本先验证[生成预览](zip-path-preview.log)/[旧包预览](zip-legacy-preview.log)均拒绝四组输入；原全量结束后已实际应用到产品源码。保留名、控制字符和大小写规则参照 [Microsoft 文件命名规范](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)。

生成与原意图/相同冻结键回读共同检查成员路径、大小写/Unicode 重名、普通文件属性和清单路径/目录对应；清单预算 8 MiB，仅支持 stored/deflate，不解压附件。当前实际投影仅为 1.0，未知请求策略拒绝，避免记录声称另一策略但产物仍用 1.0。真实 UOW 拒绝前不提交、Unicode/安全下级路径/COM10 正向、保留名/控制/重名/符号链接/不完整清单/预算与压缩反向均已覆盖。

[五文件最终回归](repair-verified.xml) 98 passed / 0 failed，[导出最后整文件](zip-budget-final.xml) 42 passed / 0 failed，均覆盖受改动路径的既有断言，不拼接为全量。首次新增策略测试误将 request_id 和 intent_id 写成同值，被 Command 校验器拒绝（[原失败](repair-targeted.log)），修正测试输入后整组重跑。ruff/strict mypy 249 通过；旧失败与所有辅助脚本错误保留。

[旧生成器实际字节向量](zip-legacy-vectors.json)以 Base64 归档供回读回归，含 SHA-256 和准确来源提交；[生成探针](probe_zip_attachment_paths.py)与[回读探针](probe_zip_legacy_recall.py)可复现。首次生成探针曾传错辅助参数顺序，见[辅助脚本错误](zip-path-probe-helper-error.json)，已修正；该异常不是产品反例。

## 全量检查与中断

2026-10-13 全量停在约 25%，没有完成回执/JUnit，原进程已不存在：[原中断登记](../p1-completion-repair-20261013/full-interrupted.json)与 [原日志](../p1-completion-repair-20261013/full.log)保留，不能登记通过或与重跑相加。

本批在独立干净锁定 3.13.0 环境执行全部 **5297 个节点**：[准确命令](full-command.json)、[完成状态](full-state.json)、[原输出](full.log)、[JUnit](full.xml)、[完整分析](full-analysis.json)。结果 **5290 passed / 1 failed / 6 skipped / 1 warning**，退出 1，23217.62 秒（6 小时 26 分 57 秒）。这是 ZIP/测试助手修复前的完整失败批次，不能登记为修后全量通过。

唯一失败为 `test_command_adapter_reaps_child_process_group`：子进程 PID 已打印，但辅助 tasklist 文本采集在 PYTHONUTF8 下解码本机 CP936 原生输出失败，阅读线程异常后 stdout 为 None，继而 TypeError。已改为二进制采集、仅按 CSV ASCII PID 精确列核对，非零退出不当“已终止”；新增本机编码/精确 PID/探针失败反例。原 150ms 超时及实际子进程终止断言未放宽，修后该实际进程用例通过且无线程警告。

6 个跳过分别为 3 个符号链接权限不足和 3 个临时目录位于 Git 工作树内；后者已准备仓库外可写目录实测。修后实际收集 **5331** 节点，[新全量取证](../p1-final-closure-20261014/README.md)单独记录，不覆盖本轮原回执或把局部数字相加。

[源码指纹](tested-source-inventory.json)覆盖全部核心/测试/脚本与受改 TS；[facts.json](facts.json)记录规模/动作与版本。全文件指纹不等于逐行安全审计。

## 仍未证明

准确欠缺、事实来源、下一次输入及关闭条件见[剩余清单](../../一期工程整改/05-待裁定/2026-10-13-剩余合同缺口与所需决定.md#本次仍欠的可执行关闭条件)。默认核验结构化来源/配置、可信动态 R、交付验证权威写入、适用构建/显式最小启动与全部加载来源保存、受控宿主用户流、独立备份位置/完整 generation 迁移/物理掉电仍欠。真实 Trae、供应方/业务对象与全部 AC 没有新增 verified。

## 文档与恢复

CORE 1.85、AB 1.45、BC 0.49、CD 1.24、AC 1.9、BD 0.7 与台账 2.87 保持；新增参数 Schema 约束仍属这批未提交的同一合同版本。签署/last_verified_commit 不动，reviewing/partial/not_run 不升为真实对拍。

原基线哈希和备份仍见上一批；[逐文件处置/哈希](document-audit.json)、[台账未变](acceptance-unchanged.json)及[本地链接核对](document-links.json)已登记，最终结果回写后再刷新文件指纹。过时现行判断已替换，历史失败/阶段证据保留，没有为清理而删除有效追溯文件。改动原因见[本轮修改日志](../../修改日志/2026-10-14-一期欠缺边界续修与检查.md)。
