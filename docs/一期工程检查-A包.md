# 一期工程检查-A包

检查日期：2026年10月1日；源码基线：拉取后的 `origin/develop`，`0c890eded76fe6c31ad84c211445025de67f599e`（`A包 lu (#38)`）。

拆分日期：2026年10月1日。本文从《一期工程分包检查》拆出，覆盖 A 包**已实现代码的正确性、未实现能力、跨包接入和验收缺口**。原合并文档可从拆分前的 develop `d62ebf0` 恢复；本次保留原源码基线、问题编号、证据和验收结论。拆分记录见[袁的修改日志](修改日志/袁/2026-10-01-一期工程检查按ABCD拆分.md)，原审计过程见[源码检查日志](修改日志/袁/2026-10-01-一期工程分包与整体源码检查.md)。

其他包：[B 包](一期工程检查-B包.md) · [C 包](一期工程检查-C包.md) · [D 包](一期工程检查-D包.md)。整体 FR/AC 检查见[当前代码分析与一期工程对比](当前代码分析与一期工程对比.md)。

## 1. 依据、范围与证据边界

- 先读[文档总览](项目文档/README.md)、[总体架构](项目文档/总体架构.md)、[阅读索引](项目文档/阅读索引.md)，再对照[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[功能](项目文档/一期/功能文档/01-功能文档.md)、架构 00—06 及[面板与工作台设计](项目文档/一期/设计文档/01-面板与工作台.md)。一期仍为 **17 FR / 35 AC**，未增删或重编号。
- 用户提供的《一期工程四部分拆分与低对接实施方案(1).md》（2026-09-24）用作 ABCD 职责与验收牵头划分参考；现行字段、状态、时序以项目分册及后续接口裁定为准。附件中的协作指令不作为本次额外操作授权。
- 对照[接口总台账](接口对接/README.md)和[裁定归档](接口对接/归档)。不修改 `docs/项目文档/`，不把源码进展自动登记为接口冻结或真实验收完成。
- 全量清点 `src/aitest/` **111 个 Python 文件**、面板与 Trae **2 个产品 TypeScript 文件**以及 **75 个 Python 测试文件**；阅读实现与调用链，重点检查身份、集合规则、事务、恢复、授权、投影和装配。清点包含空包文件和占位，文件数不是实现率，也不是覆盖率。mypy 的 123 个分析文件包含跟随导入，不能替代产品文件数。
- [测试摘要](validation/p1-audit-20261001/test-summary.json)记录失败名称与环境；[隔离复现](validation/p1-audit-20261001/probes.py)及[结果](validation/p1-audit-20261001/probe-results.json)使用临时目录和虚构材料，不调用真实模型、宿主或业务系统。一次性全量 AST 清点和校验中间文件已清理，原始版本可从 PR #40 的 Git 历史恢复；保留可复查的问题证据。

本文用“已实现”表示有实际控制流，用“占位”表示空类/说明或显式 `NotImplementedError`，用“未接通”表示已有实现尚未接入正式端口、统一核心或产品入口。风险标为“阻断交付”“高风险”“接入缺口”“待核对”；不以源码行数估算完成百分比。

## 2. 本包职责与当前结论

| 包 | 责任与现有进展 | 主要未闭合点 | 牵头 AC |
| --- | --- | --- | --- |
| A：本地核心底座 | 协议/端口、文件工作单元、记录/对象/索引/事件、恢复/备份/迁移、来源/模型/凭据适配已有较多代码 | 原子提交、持久幂等、备份闭包与路径安全有可复现缺陷；管道/宿主仍占位，默认装配不完整 | 28（1 项） |

四个交接面仍是**本地协议、工作单元与存储端口、PreparedRun、ExecutionFacts**。文件所在目录不自动决定语义归属：`SourceSnapshot` 按 DEC-001 由 B 负责来源规则，模型保留在执行领域，A 实现快照/来源端口，C 核对实际加载，D 读取投影。纯文件摘要核验由 A 提供技术能力；同一业务对象的独立核验仍须 C 实现并由 D 消费。

验收登记取自 `tests/acceptance/p1/status.json`。A 包牵头 P1-AC28（1 项），当前为 untested，真实验收 verified=0。所有 `evidence_path` 为空，本次未改变登记。四包合计仍为 `verified=0`、`not_verified=7`、`blocked=1`、`untested=27`（35 项）；牵头数量不表示该包独立完成全部依赖。

## 3. 已有实现复核与缺口

### 3.1 已有能力逐域检查

| 源码范围 | 实际实现与已有检查 | 复核结论 |
| --- | --- | --- |
| `contracts/`、`application/ports.py` | 版本、Command、错误、QuerySpec、事件、响应、身份和多类端口；7 份 Schema 可重新生成且无差异 | 正式签名比旧检查更完整；仍有双 Response 模型和不完整维护/制品端口，见 A-01 |
| `bootstrap.py`、`interfaces/local/` | 核心缓存、注册窗口、用例注入、LocalAPI 会话去重/事务路由、launcher/connector 和 worker 入口 | 业务装配、真实通道、进程生命周期未闭合，见 A-02；本地相关测试失败 |
| `file_store/workspace.py`、`locking.py`、`atomic.py`、`unit_of_work.py`、`records.py` | 工作空间身份、OS 锁、预期修订、记录历史、原子文件替换、事务及意图原语；锁和提交测试存在 | OS 锁是短事务锁，尚未证明唯一长生命周期核心；跨文件提交、持久意图重传有缺陷，见 A-03/A-04 |
| `objects.py`、`integrity.py`、`recovery.py`、`fake.py` | 对象按摘要存取/读时校验、JSON/对象扫描、基础恢复及内存替身 | 对象篡改单点测试已有；缺引用闭包/缺失对象核查和执行抢救编排，不能据此证明掉电恢复 |
| `index.py`、`events.py` | 索引过滤/排序/分页；事件 staging、确定性 ID、fsync、尾部修复/重建 | 已不是占位；同提交边界、坏索引与稳定分页仍不满足合同，见 A-04/A-05 |
| `backup.py`、`migrations.py`、`maintenance.py` | 备份创建/校验/恢复；迁移 inspect/plan/apply/resume/rollback；空间诊断/临时回收预览 | 已不是占位；完整闭包、安全目标、活动执行门禁及回收资格仍缺，见 A-06/A-07 |
| `adapters/source_snapshot.py`、`source_control.py` | 实际本地文件散列/复制、Git 命令、可选 GitHub HTTPS 只读查询 | 历史字节未固定、快照元数据可覆盖、路径未逐项限界；plain/Git 的完整流程仍未接通，见 A-08 |
| `credentials.py`、`projections.py`、`adapters/model.py`、`deepseek.py`、`connections.py`、`clock.py` | 环境/Windows Credential 解析、材料过滤、HTTP 模型调用、连接适配、时钟；对应单元检查已有 | 已不是占位；正常响应/JSON 投影可能泄露材料，出站目标/失败响应与真实连接核对仍缺，见 A-09/A-10 |
| `adapters/execution/verification.py` | 独立重新读取文件并校验摘要 | 是文件完整性核验，不是函数/API/业务持久化核验；不能替代 AC07 |

### 3.2 逐项问题、受影响合同与下一步

| 编号 / 风险 | 源码证据与实际问题 | 影响与完成条件 |
| --- | --- | --- |
| A-01 接入缺口 | `contracts/responses.py` 与 `contracts/views.py` 各定义 Response；LocalAPI/Schema 用后者，端口用前者。`ReportArtifactPort`、`MaintenancePort` 仅说明；B 另有 substrate/model Protocol，凭据解析签名与 SecretPort 不同 | FR16、AC16/27/28/31/35；由 A 统一完整签名/版本，B/C/D 给消费语义，使用同一响应/凭据/制品合同并做真实转接，不能只靠 re-export 通过依赖检查 |
| A-02 阻断交付 | `pipe.py`、`editor_host.py` 显式未实现，且缺 `check_peer_identity` 导致 pytest 收集失败。`core_worker.py` 只读消息、不派发业务，默认一连接后退出。默认 `CoreBootstrap.create` 未注入事件、安全投影和 B/C/D 全部用例 | AC19/26/27/31/34；实现本机当前用户/会话/实例核对、长生命周期排他写入者、多个客户端、活动执行保活/draining/重连，并经真实 Trae/CLI/MCP 同核心验收 |
| A-03 高风险 | `FileUnitOfWork` 同一持久 intent 再提交可生成第 2 个记录修订；复现 **A-INTENT-01**。`append_intent` 的局部去重不能覆盖工作单元路由，直接 append/batch 也可绕过统一提交 | AC19/31；意图结果与记录/索引同提交持久化，身份和业务载荷均校验；同意图跨入口/重启返回原结果，明确重跑建立新意图；旧记录读取与 epoch 校验同测 |
| A-04 高风险 | `records.py:commit_transaction` 在最终 records 发布之前已写事件/commit/索引。注入 records 替换失败后，记录仍为修订 0，索引/事件各可见 1 条，活动标记被清，恢复报 `repaired`：**A-COMMIT-01**。默认 UOW 也没有接正式 FileEventJournal | AC19/27/28/34；建立唯一可靠提交边界，未提交投影不可读；异常保留恢复事实并按权威边界重建。需要逐个发布点故障注入及多进程/掉电证据 |
| A-05 高风险 | `index.py` 的 offset 游标没有固定 commit/query 摘要；分页间插入记录导致重复读 ID 2：**A-QUERY-01**。坏行被忽略仍 `ok`：**A-QUERY-02**。查询/详情分别整读 indexes/records JSON，事件也整读日志，不是分片摘要查询 | AC27/34；按有限 QuerySpec 建索引键、摘要投影、固定提交游标与 epoch/generation；坏索引显式维护状态。快照与游标一次成对发布，多事件/重复投递不漏不重 |
| A-06 高风险 | `backup.py` 采用目录白名单，备份“校验通过”仍漏 snapshots、diagnostics、exports、migrations：**A-BACKUP-01**；manifest 中 `../` 可写出恢复目标：**A-PATH-02**。`integrity.py` 未验证所有永久引用可达；在线活动 spool 未明确排除/冻结 | AC16/28/35；以永久引用闭包做完整历史备份，逐项 hash/路径/链接校验；活动材料另列未纳入；恢复前验证再写入空目标，缺口不伪装完整 |
| A-07 高风险 | `migrations.py:apply` 有未核实活动标记仍 `applied`：**A-MIGRATION-01**；现有备份复用时缺完整校验，迁移只覆盖基础格式字段。`maintenance.py` 用临时文件名判断回收，未核实活动引用/已提交引用，且命名与 atomic 的残留格式不完全对应 | AC28；先核实/抢救活动执行，再建立可校验备份与新 generation；永久缺口按主责合同显式 A/G 处理。回收需证明是可回收临时材料，禁止按位置/名字猜资格 |
| A-08 高风险 | `pin` 只存清单，原源码改变后旧字节不可取，物化先复制新字节再报错：**A-SOURCE-01**；选定 one.py 后新增未选 two.py 也报变化：**A-SOURCE-02**；同内容不同 purpose 覆盖同 ID 元数据：**A-SOURCE-03**；篡改清单的 `../` 物化越界且 `verified=true`：**A-PATH-01** | FR01、AC12/13/19/25/30；A 保存不可变历史字节和元数据、限定选定范围并拒绝绝对/上级/符号链接越界。B 转换为正式 SourceSnapshot；C 核对真实解释器/入口/加载来源，复制成功不等于执行对应 |
| A-09 高风险 | `SafeMaterialProjector` 可保留 JSON 形式虚构 api_key 且称 `complete`：**A-SECRET-01**；默认 bootstrap 的正常 handler 响应 token 未过滤：**A-SECRET-02**。`LocalAPI._error` 输出异常原文，正常 handler 投影可缺省；已知凭据值未形成贯穿采集/模型/视图/导出的统一过滤输入 | AC07/16/32/35；过滤发生在各落盘/出站出口之前，正常/异常/嵌套结构均处理；不安全材料排除并登记缺口。测试不能只有敏感键在顶层的成功样本 |
| A-10 高风险 / 未验证 | HTTP 模型适配使用构造时 endpoint，未逐次证明与策略确认目标相同；失败 detail 原响应片段未保证脱敏，成功正文结构校验不足。Git 异常可退化为“不是仓库”，rename 的 NUL 双路径解析未完整处理。连接重试原语未形成持久统一连接状态 | AC13/15/25/32/33；A 返回实际目标/版本/来源/结构化安全错误，B 持久登记出站策略及调用，C/D 按动作依赖降级。真实 Git/模型/Windows Credential 与真实宿主尚未验收；静态风险需专项输入验证 |

A 包原“Schema、ruff、mypy 未通过”的结论已过时：这三项在本次基线上通过。事务锁测试剩余失败为返回字典多了 `intent_id: None`，属于返回合同/断言不一致；不能把该失败本身当作 OS 锁失效证据。提交与幂等缺陷有上述独立复现证据。

## 4. 共享验证结果与失败归属

以下保留原审计的**整体检查记录**，不是 A 包独立测试统计；各包均需结合自己的问题和跨包依赖读取。

环境：Windows 11 x64（build 22631），CPython 3.13.13，Node 24.14.1，npm 11.13.0；锁文件安装 `uv sync --extra dev --locked` 通过，产品版本 0.4.0。

| 检查 | 结果 | 含义 |
| --- | --- | --- |
| `uv run python scripts/check_versions.py` | 通过 | 版本一致 |
| `uv run ruff check .` | 通过 | 含本次隔离复现/清点脚本；不是业务正确性证明 |
| `uv run mypy` | 通过，基线 123、文档复核 125 个分析文件（含跟随导入） | 类型门通过；不是产品源码文件数 |
| Schema 重新生成 + `git diff --exit-code -- src/aitest/contracts/schemas` | 通过 | 7 份生成物一致；现有 Schema 仍可能缺合同字段 |
| 全量 `uv run pytest` | **收集失败，1 error** | `test_a_pipe_peer_rejection.py` 无法导入 `check_peer_identity`；未运行成完整测试集 |
| 排除上述文件的诊断运行 | **737 passed / 16 failed / 4 warnings**，19.47 s | 不是全量通过；15 项管道/宿主/装配失败，1 项 UOW 返回字典不一致。完整名称见测试摘要 |
| 20 项隔离复现观察 | 19 项具体问题及 1 项额外复用约束待核对 | 问题观测不等于修复通过；脚本执行成功不表示被测代码通过 |
| 面板 npm ci/build、Trae npm ci/package | 通过 | TypeScript 检查、共享 JS 和 6 文件 VSIX 可构建；未验收真实 Trae |
| 面板 Playwright | 初次缺 Chromium；安装测试浏览器后 **1 passed** | 仅静态面板窄宽度导航/信息可见性，不涉及核心业务 |
| develop 基线远端 CI | **失败**，[run 36729829040](https://github.com/ymyymy27/ai-test-component/actions/runs/36729829040) | 版本/ruff/mypy/Schema 通过，pytest 同样收集失败；普通 PR 不执行仅 tag 触发的 release 制品步骤 |
| P1-AC01—35 真实环境验收 | **0/35 verified** | 没有真实宿主、多类业务、掉电或完整留存证据；未验证项保留 |

命令、失败分类和复现结果见[证据说明](validation/p1-audit-20261001/README.md)。本次源码检查没有修复上述产品缺陷或删除失败测试。原检查 PR 的 CI/合并追踪在[修改日志](修改日志/袁/2026-10-01-一期工程分包与整体源码检查.md)；随后的一次性文件清理、CI 诊断和发布准备改进另见[清理与 CI 复核日志](修改日志/袁/2026-10-01-中间产物清理与CI复核.md)，不改变本文的产品基线与验收结论。

后续 develop `d62ebf0` 的 [CI 36754947548](https://github.com/ymyymy27/ai-test-component/actions/runs/36754947548) 运行可收集测试后为 **737 passed / 16 failed / 1 collection error / 4 warnings**。15 项占位/装配失败、1 项 UOW 返回值差异及缺失身份校验函数均集中在 A 包；该结果不代表 B/C/D 已完成一期验收。本次只拆分文档，不登记为这些产品问题已修复。

## 5. 交接与下一轮完成条件

1. **A 先闭合权威提交、安全和本地核心**：修复持久意图/ghost index-event、历史快照/备份闭包/越界路径、投影出口；实现管道与进程身份/保活，统一正式端口。必须保留失败前可读边界、记录与证据，不能靠去掉测试取得绿灯。
2. **四包共同交付 AC 证据**：按需求第 5 节逐项给输入、软件/源码/制品版本、预期/实际和原始证据路径。PreparedRun/ExecutionFacts 夹具交接、静态质量、单元通过、真实环境验收分别登记；任何单包“完成”不自动推出一期完成。

本包推进须同时核对其他包的交接条件：[B 包](一期工程检查-B包.md) · [C 包](一期工程检查-C包.md) · [D 包](一期工程检查-D包.md)。整体推进顺序与逐项 FR/AC 对照仍见[当前代码分析与一期工程对比](当前代码分析与一期工程对比.md)。
