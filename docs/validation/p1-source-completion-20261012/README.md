# 全源码与一期完成性复核证据

2026-10-12，文档起点 `fix@708439bf0152008ea981cfb89c68d64538b2e57d`。产品源码/测试代码与 `develop@845e6978d522def651d5fec5a0b046126ffaaadd` 相同；本轮只修改审查/整改/接口实现状态文档并新增取证脚本，不修产品、生成 Schema 或验收台账。

结论：**一期没有全部完成**。逐项义务、源码支撑、生产入口/保存/界面缺口及关闭条件见[完整报告](../../一期工程整改/01-现状检查/当前代码分析与一期工程对比.md)，当前数字/任务/接口版本/验收状态见[项目现状](../../现状.md)。

## 1. 全源码范围

[source-inventory.json](source-inventory.json) 清点 Git 跟踪的 Python/TypeScript/JavaScript/脚本文件，区分产品、工程、示例、验证和历史探针；逐文件保存路径/哈希/行数。产品/工程 Python 读取全文并进行 AST 定义/导入/调用检查，逐模块职责/装配索引见[module-map.md](module-map.md)，默认链路和关键风险函数深入核对见报告。TypeScript 面板/宿主/桥、构建及测试配置全文核对。

测试/历史探针仅清点、AST 解析和相关证据检索，未逐行人工审计所有测试/历史脚本。第三方依赖、dist、.runtime 等不计作产品新实现。清点/结构检查不等于逐行安全审计，也不证明排除了所有潜在缺陷。模板与生成 Schema 的验证沿用实际组件测试，不手工修改生成物。

## 2. C-15：默认真实文件链阻断

[source-provider-counterexamples.py](source-provider-counterexamples.py) 使用已有 `_authoritative` 保存材料夹具：真实工作空间核心/UOW、源码分析/固定、计划准备和初始 Run 登记；环境/受控确认来源是明确的组件 fixture，未声称真实 Trae 人工验收。默认 `SavedActionResolver` 和端口没有替换。

结果：prepare_run 为 prepared；正式 `SnapshotRef.source_snapshot_id` 为业务 source-ID，技术 pin 为 snap-ID。prepare_execution 在技术快照读取处抛 `SnapshotError: pinned source snapshot identity is invalid`，业务未启动。provider 单独核对正式 DTO 字段时又报 `snapshot.snapshot_id must be nonempty text`；使用旧 fixture 字段并接真实技术清单时报 `pinned.content_identity must be nonempty text`。

同族静态触点：`action_resolver.py` 把 snapshot_id 写入 request.materialized_snapshot_ref，默认 registered_entry 把该值当 cwd；当前受前置阻断遮挡，未声称真实业务已经执行到这一错误。关闭需要准确业务→技术引用/内容身份和真实固定工作目录贯通，不能关闭来源核验绕过。

## 3. C-16：下游 verified 的三个反例

为隔离 C-15，脚本在下游明确使用 `CompatibleSnapshotFixture` 添加期望字段，**这不是产品修复**。固定 blob/物化及 Python 导入使用真实实现。平铺入口语法错误、正常包加导入错误包、正常包加有语法错误子文件的命名空间包，三组均 `verified` 且无 gap。实际语法错误/导入错误与使用的替身标记都在 JSON 中保存。

根因：fallback 不实际编译；有模块只取一个非空 file，未消费其余模块失败/无文件/真实摘要、probe_error 和实际解释器。现有 Python 探针组件与合法状态 Schema 不能保证 provider 合法消费。

成功复现输出：[source-provider-counterexamples.json](source-provider-counterexamples.json)。首轮 stdout 中文解码有误，原字节呈现保留在[source-provider-first-stdout.txt](source-provider-first-stdout.txt)；后轮由脚本 UTF-8 直接写 JSON，并以英文/转义打印摘要。调试阶段的接口调用签名错误发生在取证脚本，修正后才取得上述成功复现结果，不是产品缺陷证据。

复现命令（工作目录仓库根）：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe docs/validation/p1-source-completion-20261012/source-provider-counterexamples.py
```

脚本退出 0 表示当前缺陷行为被复现，不表示修复通过；再次执行会生成新的本机 fixture 路径并重写同名输出，应另存本次原证据后再重跑。

## 4. 既有回归和代码基线

[checks.json](checks.json) 保存精确命令/退出码/时长/日志哈希。来源 provider、动作解析、纯判定、事实适配、Schema、模板与架构的既有测试通过；实际 JUnit 见[scope-regression.xml](scope-regression.xml)，原输出见[scope-regression.log](scope-regression.log)。它们没有断言本轮全部新增拒绝场景，不能关闭 C-15/C-16。

初次 `git diff --exit-code` 含 tests 整目录，因前轮修改的验收 README 文档而退出 1，原[输出](product-source-diff.log)保留。排除该唯一文档后，产品/配置/测试代码 diff 退出 0，原[输出](product-code-diff.log)保留。这不是产品代码回归失败；初次与修正后的检查分别登记。

后续接口状态/文档收尾检查单列结果，不能把批次数相加伪装完整全套。前轮完整套件中止、历史成功与本机组件检查都保持原归属，详见[前轮复核](../p1-code-review-20261012/README.md)和[接口复核](../p1-interface-review-20261012/README.md)。真实 Trae 全业务、真实供应方故障、实际业务对象、物理掉电及完整可携带导出未验收。

收尾原命令及结果见[closeout-checks.json](closeout-checks.json)：接口/架构/验收台账/真实文件往返专项原[Junit](interface-and-ledger-regression.xml)与[日志](interface-and-ledger-regression.log)分别保存；文档事实核对、产品/测试/主责文档/验收不变检查通过。本地链接/锚点、整改表格、FR/AC 数与六合同版本/状态对照见[document-checks.json](document-checks.json)。

格式检查中临时强制 `core.autocrlf=false` 与仓库 Windows 换行设置不一致，将 CRLF 的 CR 判为尾空白，非零原[输出](diff-check.log)保留；按仓库正常 Git 设置重查结果另存，不能把原非零删除或覆盖。

## 5. 文档更新与恢复

[fr-ac-review.json](fr-ac-review.json) 保存每 FR 的已有实现/缺口、每 AC 的登记结果/支撑/欠缺，并保存未改的验收台账哈希；完整场景/预期仍取需求和机器台账，不另定定义。

[documents-before.json](documents-before.json) 保存修改前 SHA-256/字节数及 `git show 708439b:<path>` 恢复版本；本机备份 `.runtime/p1-completion-docs-before-708439b.zip`。受影响当前文档见[documents-changed.json](documents-changed.json)。更正前轮默认链乐观表述，新增 C-15/C-16；接口 CORE/AB/BC/AC 仅修实现状态/阻塞/下一动作，reviewing/partial/not_run 和确认历史不改，协议/Schema/保存数据无需迁移。未删除历史验证原输出或代填真实验收。
