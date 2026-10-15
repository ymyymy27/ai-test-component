# 计划执行核验报告续修（2026-10-15）

基线 `fix@8d06c5ff47beb3a38c17be710221fd05fab2e37b`，产品元数据 0.4.3；锁定 CPython 3.13.0 回归环境。主责分册和三个期次验收状态不改。本目录记录自动化组件证据，不是真实 Trae/人类操作/物理故障验收。

## 已实现

- prepare_run 的 from_saved 形式：准确读取项目、绑定、计划、源码、环境、范围、完整用例、模板/规则，复用原发布/来源/环境/确认门禁；不让调用方伪造冻结用例。执行输入摘要由核心计算，准确确认回执仍核对受控证明。
- 默认 HTTP 核验：宿主 AITEST_BUSINESS_QUERIES 冻结登记，准确 StepRevisionRef/Case 依据解析为 VerificationRequest。配置、目标和项目不符拒绝；执行输出不作为核验结果；查询材料沿既有保存/幂等/回读路径。
- 分步表单覆盖依据确认、发布、准备、登记、解析、授权、执行、核验、报告、导出；同执行意图三步复用，其他动作清空意图，未知不自动重跑。修复旧回执盖住新输入提示。

## 检查回执

| 检查 | 结果与证据 |
| --- | --- |
| 首轮新文件 | [configured.xml](configured.xml)：18 项中 17 通过、1 失败；真实进程退出 2，夹具仍使用不存在的 tests/acceptance 参数；改为明确 main.py |
| 首轮完整链 | [chain.xml](chain.xml)：1 项失败；执行/查询成功，报告拒绝缺失模板；补发布表单模板输入和真实模板引用，未放松报告门禁 |
| 修后主链 | [chain-fixed.xml](chain-fixed.xml)：1 项通过；真实 Python 执行、独立 HTTP 读取、保存报告、重启回读；同意图不重复查询 |
| 扩展既有回归 | [regression.xml](regression.xml)：104 项中 103 通过、1 失败；失败为新增主链按数组首项检查确认状态，改为按 case_id 定位。85 项既有回归均通过，新文件修后另列 |
| 主链断言校正 | [final-entry.xml](final-entry.xml)：20 项中 18 通过、2 失败，均为同一断言将外层 blocked 错写成原因码 needs_reprepare；实际协议正确拒绝旧意图换计划，修正断言后再次验证 |
| 最终主链与边界 | [verified-entry.xml](verified-entry.xml)：20 项全部通过，退出 0；两条主链均覆盖 ZIP 字节与重启回读，其中一条使用真实宿主登记 venv。旧意图换计划被阻止，同意图重查不发送第二次 GET |
| 宿主 | 25 项通过，[host.log](host.log)；模拟 VS Code API/真实桥接子进程，不是 Trae 兼容验收 |
| 浏览器 | 17 项通过，[browser.xml](browser.xml)；含意图边界、上下文清理、from_saved 表单，不代签人工确认 |
| 静态与构建 | [final-release-commands.json](final-release-commands.json)：ruff、mypy（255 文件）、版本、锁、Schema、宿主、面板、VSIX、wheel、制品与隔离安装均退出 0 |
| 源码/制品 | [facts.json](facts.json)、[source-inventory.json](source-inventory.json)、[wheel-source-parity.json](wheel-source-parity.json)：241 Python 模块与 wheel 字节一致 |
| 全量 | 当前 5504 节点仅收集；没有新的完整全量通过结果；旧全量中断事实不变 |

扩展回归后补充准确计划身份进入准备摘要，防止同一准备意图静默切换内容相同的另一计划；最终构建由 final-release-commands 验证。before-identity-fix 文件保留修正前指纹；当前 facts/source-inventory/wheel-source-parity 对应修后源码。测试数字按独立回执列出，重叠范围不相加。

## 证明边界

主链测试的 Python 进程、HTTP GET、文件提交、报告和重启回读是真实组件；人类事件使用明示夹具；原主链环境身份使用夹具，真实宿主登记环境变体已通过，实际环境解析器、执行、HTTP、报告及对象保存均未替换。仅执行计划中的一个步骤，报告保持 incomplete，不把其他步骤算完成。适用来源加载、全部 L1/L2/L3、运行中修订与真实宿主须继续实际验收。

新增核验登记目前覆盖无需凭据、对象 ID 预先明确的 HTTP 读取；鉴权、动态对象 ID 提取、纯函数独立预期比对仍欠。全部计划/环境编辑目前仍有 JSON 输入，不能称完整图形向导。C-17 动态复用、真实恢复、DEC-014 原始记录追溯和原 27 项开放条目未因本批自动关闭。

操作方法见[0.4.3 指南](../../一期工程整改/04-交接/0.4.3-计划执行核验报告操作.md)。原失败回执保留；没有把版本包构建、单元/合同测试写成真实验收或双方签署。

日志/XML 入库仅清理行尾空格和末尾空白行；测试结果与失败正文保留，原始字节另存本机 `.runtime/workflow-raw-evidence/`。
