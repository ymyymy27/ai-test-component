# 一期工程检查-C包

检查日期：2026年10月1日；源码基线：拉取后的 `origin/develop`，`0c890eded76fe6c31ad84c211445025de67f599e`（`A包 lu (#38)`）。

拆分日期：2026年10月1日。本文从《一期工程分包检查》拆出，覆盖 C 包**已实现代码的正确性、未实现能力、跨包接入和验收缺口**。原合并文档可从拆分前的 develop `d62ebf0` 恢复；本次保留原源码基线、问题编号、证据和验收结论。拆分记录见[袁的修改日志](修改日志/袁/2026-10-01-一期工程检查按ABCD拆分.md)，原审计过程见[源码检查日志](修改日志/袁/2026-10-01-一期工程分包与整体源码检查.md)。

其他包：[A 包](一期工程检查-A包.md) · [B 包](一期工程检查-B包.md) · [D 包](一期工程检查-D包.md)。整体 FR/AC 检查见[当前代码分析与一期工程对比](当前代码分析与一期工程对比.md)。

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
| C：执行与证据 | 命令子进程、串行运行、超时/停止、spool、脱敏、证据发布、恢复与事实组装已有实现 | 完成的下游依据未失效；执行前持久意图/授权/实际加载来源缺失；其余业务适配器占位 | 04—09、13、19、24、25（10 项） |

四个交接面仍是**本地协议、工作单元与存储端口、PreparedRun、ExecutionFacts**。文件所在目录不自动决定语义归属：`SourceSnapshot` 按 DEC-001 由 B 负责来源规则，模型保留在执行领域，A 实现快照/来源端口，C 核对实际加载，D 读取投影。纯文件摘要核验由 A 提供技术能力；同一业务对象的独立核验仍须 C 实现并由 D 消费。

验收登记取自 `tests/acceptance/p1/status.json`。C 包牵头 P1-AC04—09、13、19、24、25（10 项），当前均为 untested，真实验收 verified=0。所有 `evidence_path` 为空，本次未改变登记。四包合计仍为 `verified=0`、`not_verified=7`、`blocked=1`、`untested=27`（35 项）；牵头数量不表示该包独立完成全部依赖。

## 3. 已有实现复核与缺口

### 3.1 已有能力逐域检查

| 源码范围 | 已实现及检查 | 复核结论 |
| --- | --- | --- |
| `domain/execution/runs.py`、`sources.py`、`domain/evidence/evidence.py` | Run/Step/Attempt/Handle/游标/证据/来源值对象、状态约束、恢复词汇 | 值对象能表达事实，但当前实例/真实性由调用方填入，尚无正式持久来源证明 |
| `application/execution/runner.py`、`recovery.py` | 串行依赖调度、inspect/collect/stop、检查点、未知副作用等待/安全重试；runner/recovery/closure 测试 | 已有实际控制流；必须修正执行前意图顺序及下游失效，见 C-01/C-02 |
| `adapters/execution/command.py`、`redaction.py` | 参数数组/环境白名单、子进程、Windows Job Object/POSIX 进程组、超时/停止、分块前过滤；command/redaction 实测 | 真实本机命令测试通过不等于业务 L1/L2/L3 通过；跨核心进程恢复与业务断言仍缺 |
| `file_store/spool.py`、`checkpoints.py`、`application/evidence/publication.py` | 封口块、摘要/路径检查、游标、脱敏摘要、可抢救输出、永久对象/证据发布 | 已有本地闭环；检查点直接文件保存，尚未与 A 业务记录和幂等结果共同发布 |
| `application/execution/facts.py`、`contracts/execution_facts.py` | ExecutionFacts 版本化组装、必需身份/引用、success/failure/unknown/quick 夹具与 Schema | 组装时 source_commit/cursor/修订/完整性由调用方提供，不能证明来自同一 A 提交；覆盖计数占位不能充当 D 的有效整用例集合 |
| `source_checks.py`、`evidence_review.py`、五类执行适配器 | Python checks、HTTP、Agent、manual_evidence、external_result 仍说明类占位 | 需真实动作/安全采集/失败/人工与导入路径；verification 已有文件 hash 实现，不能仍写“六类全部占位” |

### 3.2 逐项问题与完成条件

| 编号 / 风险 | 问题及证据 | 影响 / 主责下一步 |
| --- | --- | --- |
| C-01 高风险 | `invalidate_downstream_attempts` 只处理活动态，完成的下游消费旧上游仍不失效：**C-INVALIDATION-01**；同计划修订提前返回，未形成控制依赖/传递闭包与整用例旧复用撤销 | AC19/20/24；新 Attempt 立即撤销旧 R；所有实际消费旧结果的下游（含完成项）立即依据过期，同/异输出摘要均处理；无依赖分支保留，历史结论不改 |
| C-02 高风险 | SerialRunner 在 `ExecutionPort.start` 后才保存检查点；缺 A 持久 start 意图结果与动作授权校验；Attempt/Handle 核对不能替代实际输入/来源/授权修订核对 | AC19/24/31；先短事务保存可靠意图/授权/当前 Attempt，再事务外执行；同意图双入口只启动一次，未知副作用不自动重放；新执行重新授权 |
| C-03 阻断交付 | Python/HTTP/Agent/人工/外部导入适配器占位，业务级独立 Verification 未实现；只用命令 exit code/文件 hash 不能判必要业务断言或真实持久化 | FR08—13、AC04—09/24/25；逐类实现真实输入、独立核验、Mock/混合/未知、请求日志关联及人工证据；不支持应显式缺能力，不冒充不适用 |
| C-04 高风险 | 快照复制未证明 editable/PYTHONPATH/绝对入口加载来源；`source_checks.py` 占位；CommandAdapter 子进程状态在实例内存，换核心实例没有完整持久进程身份再附着路径 | AC13/19/25；核对实际解释器/入口/加载来源，失联先 inspect/collect/抢救；不能按 PID 或目录复制成功宣称对应源码；真实宿主退出/强杀/掉电尚未验收 |
| C-05 接入缺口 | FileCheckpointStore/EvidencePublisher 与 A UOW 不共同提交；ExecutionFactsAssembler 默认完整性及传入 commit/cursor 未与权威边界成对读取，采集缺口/可读性也未统一校验 | AC07/09/21/24/27/28；C 经 A 端口保存 Attempt/证据/引用/索引/意图，可靠保存后发布；按准确修订读事实，缺证据保留缺口、不补造完整事实 |
| C-06 未验证 | 暂停/取消/退回/补证、传输恢复与新副作用 Attempt、多类实际动作与宿主保活没有产品级入口和 AC 证据 | C 牵头 10 项仍 untested；补真实 Windows/Trae 的当前 Attempt、停止未确认、失败分支、旧依据过期、跨运行继承不能拼 E/R/V 等场景 |

## 4. 共享验证结果与失败归属

以下保留原审计的**整体检查记录**，不是 C 包独立测试统计；各包均需结合自己的问题和跨包依赖读取。

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

1. **C 补有效事实与执行适配**：执行前可靠意图/逐项授权，实际加载来源核对，新 Attempt 和传递依赖立即失效；补 Python/HTTP/Agent/人工/导入和业务独立核验，验证重启/未知副作用。
2. **四包共同交付 AC 证据**：按需求第 5 节逐项给输入、软件/源码/制品版本、预期/实际和原始证据路径。PreparedRun/ExecutionFacts 夹具交接、静态质量、单元通过、真实环境验收分别登记；任何单包“完成”不自动推出一期完成。

本包推进须同时核对其他包的交接条件：[A 包](一期工程检查-A包.md) · [B 包](一期工程检查-B包.md) · [D 包](一期工程检查-D包.md)。整体推进顺序与逐项 FR/AC 对照仍见[当前代码分析与一期工程对比](当前代码分析与一期工程对比.md)。
