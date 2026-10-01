---
contract_id: AB-001
title: 端口与保存语义
provider: A
consumer: B
contract_version: "0.7"
contract_status: reviewing
provider_implementation: partial
consumer_implementation: partial
verification_status: not_run
last_verified_commit: null
blockers: []
next_owner: A/C
next_action: A 冻结 current_revision / commit_seq / next_commit_seq 三个只读方法（准备链路已依赖）并确认装配点接法；C 回写第 11 节 SourceSnapshot 字段口径的执行兼容性结论（Q1/Q2），按新的契约 PR 补齐字段
---

# B-A 跨包需求：B 包所需端口与保存语义

版本：0.5
日期：2026-09-30
提出方：B 包（项目与计划）
接收方：A 包（本地核心底座）；第 5 节的口径冲突同时抄送裁定方
状态：**三项归属与范围已由项目负责人裁定；端口签名、适配器和快照字段待实现与对拍**
依据：一期架构文档《01-项目与计划》第 7、8、9、11 节；《04-存储与恢复》第 2、9、13 节；需求 P1-FR01、P1-FR03；组长实施方案第 3 节

---

## 1 目的与前提

架构文档《01-项目与计划》第 7 节末明确：

> 依赖方向：`application → domain`，外部能力一律经 `application/ports.py` 的端口。本册不直接读写业务文件，
> 记录落盘统一走《04-存储与恢复》的工作单元。

因此 B 包**不直接读写任何业务文件**，全部落盘经 A 的工作单元与端口。本文档列出 B 所需的最小端口面，
请 A 统一加入 `application/ports.py`。

**B 包不会自行修改 `application/ports.py`**（该文件的所有者是 A），仅在本文档提出签名需求。

---

## 2 B 需要保存的记录清单

依据架构文档《01-项目与计划》第 7 节"记录字段与模块归属"表：

| 记录 | 主责模块 | B 的用途 |
| --- | --- | --- |
| `LocalProject` / `LocalProjectBinding` | `domain/project/context.py` | FR01：项目身份与目录绑定（git / plain 两形态） |
| `Module` / `Dependency` | `domain/project/context.py` | FR01：模块与依赖边 |
| `Task` / `AcceptanceItem` / `Delivery` | `domain/project/context.py` | FR02：任务与标准交付说明 |
| `EnvironmentRef` | `domain/project/context.py` | FR01：环境引用 |
| `SourceSnapshot` | `domain/execution/sources.py`（**归属存疑，见第 5 节**） | FR01：源码内容身份 |
| `TemplatePack` / `CriticalPath` | `domain/planning/templates.py` | FR04：模板与关键链路 |
| `GeneratedContent` | `application/ai_assistance.py` 编排后经工作单元保存 | FR04：草稿与来源修订 |
| `RuleDraft` / `RuleVersion` | `domain/planning/rules.py` | FR05：规则版本 |
| `Case` / `CaseLink` | `domain/planning/plans.py` | FR06：用例与关联 |
| `Plan` / `AcceptanceScope` | `domain/planning/plans.py` | FR06/07：冻结计划与验收范围 |
| `PreparationRecord` | B 的应用用例（经工作单元登记） | FR07：准备意图与幂等 |
| `PreparedRun` | B 产生，C 读取 | FR07：B 的唯一对外业务交付 |

---

## 3 端口签名需求

以下为**语义需求**，具体签名形式由 A 决定。全部方法都要求支持 `expected_revision` 与幂等结果。

### 3.1 `RecordRepository`

```text
save_project(project, expected_revision) -> RevisionRef
read_project(project_id, revision) -> LocalProject        # 按准确修订读取，不返回“最新”
list_projects(project_id) -> Page[ProjectSummary]         # 列表读摘要，详情按引用读取

save_binding(binding, expected_revision) -> RevisionRef
read_binding(binding_id, revision) -> LocalProjectBinding

save_module / read_module / list_modules
save_dependency_set / read_dependency_set

save_task / read_task
save_delivery / read_delivery
save_acceptance_item / read_acceptance_item

save_environment(environment, expected_revision) -> RevisionRef
read_environment(environment_id, revision) -> EnvironmentRef

save_template_ref / read_template_ref
save_generated_content / read_generated_content
save_rule_draft / publish_rule_version -> RevisionRef
read_rule_version(rule_id, revision) -> RuleVersion

save_case(case, expected_revision) -> RevisionRef
read_case(case_id, revision) -> Case                     # 必须能按准确修订读取
save_case_link / read_case_links
save_plan(plan, expected_revision) -> RevisionRef
read_plan(plan_id, revision) -> Plan
save_acceptance_scope(scope, expected_revision) -> RevisionRef
read_acceptance_scope(scope_id, revision) -> AcceptanceScope
```

**关键要求：**

1. **按准确修订读取。** 实施方案第 3 节明确禁止 B 向 C 传"当前最新计划"。
   所有 `read_*` 必须接受显式修订参数，且不得默默回退到最新值。
2. **`expected_revision` 冲突处理。** 架构文档第 8 节：
   "跨用例必须比对 `expected_revision`；来源过期或并发编辑冲突返回**当前修订和差异提示**，
   **不自动覆盖用户编辑**。"
3. **列表读摘要，详情按引用读取。** （`AGENTS.md` 第 4 节 / 存储第 13 节有限 `QuerySpec`）

### 3.2 准备意图登记（FR07 核心）

```text
register_preparation(project_id, client_id, prepare_request_id, payload_hash, intent_id,
                     source_revisions) -> PreparationRecord
find_preparation(project_id, client_id, prepare_request_id) -> PreparationRecord | None
find_preparation_by_intent(intent_id) -> PreparationRecord | None
```

**语义要求（架构文档第 11 节）：**

- 在工作单元内按 `(project_id, client_id, prepare_request_id)` 登记 `PreparationRecord` 及 `intent_id`；
- **同一键但输入摘要不同 → 返回冲突**，不得覆盖；
- **并发完成同一准备请求时只能发布一条 `PreparationRecord`**，其余调用方取得已发布结果；
- `intent_id` 必须与准备记录**同一次提交**；
- 跨入口恢复通过 `intent_id` 或准备查询取得已有意图，**不得用新入口的 `request_id` 替代业务身份**。

### 3.3 `WorkspaceUnitOfWork`

B 的应用用例需要：

- 明确的**短事务边界**（提交点由用例决定）；
- 在**同一事务内**提交记录、引用、索引与幂等结果；
- **提交后可见性**（提交成功即对后续读取可见）；
- 事务外的外部调用（源码读取与摘要计算、模型调用）——这点由 B 保证，但需要 A 的事务不隐含持有长锁。

架构文档第 11 节：

> 准备需执行的源码读取与摘要计算在**短事务外**完成；提交时校验项目、绑定、计划、规则、模板、环境修订及
> 准备请求是否仍有效。

### 3.4 `Clock`

B 的领域对象不使用系统时间。架构文档第 9 节：`Clock` 提供记录和控制所需时间。
B 需要 `now()`；业务顺序以**提交序号**判断，不按可调整的系统时间选最新（功能文档第 5 节）。

### 3.5 `SourceSnapshotPort`、`SourceControlPort`、`ModelProvider`、`SecretPort`、`ProjectionPort`

**先厘清"端口定义归谁"与"适配器实现归谁"是两件事**（原文依据见下表"归属依据"列）：

| 端口 | 端口定义的归属 | 适配器实现 | B 的用途 | 关键约束 |
| --- | --- | --- | --- | --- |
| `SourceSnapshotPort` | **B**（《01-项目与计划》第 9 节"本册的端口"） | A（`infrastructure/`） | 建立 analysis / prepare 用途快照，按实际字节计算内容摘要 | 元数据（mtime）只是变化提示，**不证明内容相同** |
| `SourceControlPort` | **B**（同上） | A（`infrastructure/adapters/source_control.py`） | 本地 Git 元信息/差异；可选 GitHub 只读 | **plain 形态不注册、不调用**；GitHub 不可用不阻塞本地 |
| `ModelProvider` | **B**（同上） | A（`infrastructure/`） | 策略校验后的脱敏投影草案请求 | 模型**只输出草稿**；迟到响应标过期 |
| `SecretPort` | **A**（《04-存储与恢复》第 9 节"本册的端口"） | A | 按用途解析引用（模型 / 被测 HTTP / 核验数据库 / GitHub 分别授权） | 只返回引用解析结果，不向视图返回正文；不能只允许模型密钥 |
| `ProjectionPort` | **待明确**（两份分册的"本册的端口"表都未列） | A | 生成安全投影 | 源码片段默认关闭；无法安全投影则排除并显示分析缺口 |

**需要一并裁定的两处口径冲突**（不自行选一种解释，依根 `AGENTS.md` 第 1.3 节）：

1. **`Clock`**：《01-项目与计划》第 9 节与《04-存储与恢复》第 9 节**都把 `Clock` 列为本册的端口**，重复列了同一个端口。
2. **`ProjectionPort`**：B 在《01-项目与计划》第 9 节的正文里被指定为使用者，但该节"本册的端口"表未收录它；
   《04-存储与恢复》第 9 节也未列。它归谁定义、由谁实现，目前没有原文可依。

**B 的诉求**：`SourceSnapshotPort`、`SourceControlPort`、`ModelProvider` 三个端口的**协议定义由 B 提供**
（B 是其一期唯一的主要使用者），**A 提供适配器实现**；`SecretPort` 按《04》归 A，B 只作为使用者。
若 A 或组长认为端口定义应统一由 A 维护，请一并裁定，B 按裁定调整。

---

## 4 关于 `application/ports.py` 的协作方式（重要）

**现状**：C 包已在 `origin/feat/package-c-execution`（commit `d60781d`）中修改了 `application/ports.py`，
新增 `SpoolStore` 协议并为 `ExecutionPort` 补充了 4 个方法签名。改动本身符合依赖方向
（`application` 依赖 `domain` 是允许的）。

**建议的协作约定：**

1. **`application/ports.py` 保持单文件，不要拆成包。** 原因：
   `tests/architecture/test_boundaries.py` 有一条硬编码断言——

   ```python
   if relative.parts[0] == "infrastructure" and name.startswith("aitest.application"):
       assert name in {"aitest.application.ports", "aitest.application.errors"}
   ```

   拆成 `application/ports/` 会让该断言失败，属于跨包破坏性改动。

2. **各包只在自己的段落追加，不重排、不整理他人已有的类。** git 对非相邻 hunk 的合并是可靠的，
   重排会导致所有人的 PR 冲突。

3. **A 是唯一所有者与合并仲裁人。** B/C/D 需要新端口时先在本目录提需求文档，由 A 统一加入。

4. **合并顺序**：若 B 的 PR 与 C 的 PR 都动了该文件，后合并方负责解冲突并重跑
   `uv run pytest tests/architecture -q`。

---

## 5 裁定前问题与方案记录

> 本节保留裁定前的冲突、证据与备选方案供追溯。现行结论以第 10 节为准，不再按本节的“待裁定”措辞阻塞实施。

### 5.1 `SourceSnapshot` 归属

**冲突事实：**

| 来源 | 原文 |
| --- | --- |
| 架构文档《01-项目与计划》第 7 节记录表 | `SourceSnapshot` 的主责模块是 `domain/execution/sources.py` |
| 架构文档《01-项目与计划》第 1、2 节 | 源码快照的建立、内容身份、排除规则、复取依赖是 **FR01（B 包）** 的职责 |
| 需求文档 P1-FR01 | "**源码快照**：Git 保存基准提交、未提交新增/修改/删除内容及排除规则……" |
| 仓库现状 | C 包已修改 `domain/execution/sources.py`（117 行新增） |

**即：领域对象的物理位置在 C 的目录，但"何时建立快照、快照用途、内容身份规则"是 B 的职责。**

#### 5.1.1 需要裁定的具体内容（B 的主张）
B 主张按**"定义层"与"位置层"分开**处理，而不是把整个记录判给某一方：

| 层 | 内容 | B 的主张 |
| --- | --- | --- |
| **定义层** | 快照建立时机、`purpose` 取值（`analysis` / `prepare`）、选定范围与依赖闭包的选择规则、排除规则、**内容身份的计算口径**、`git`／`plain` 两形态的身份统一、复取依赖与可复取范围的登记、有效性（"变了没有"）判定 | **归 B**：这些是 P1-FR01 的内容身份职责，且与 B 的 `PreparedRun`（`InputRevisions.snapshot_revision`）直接耦合 |
| **位置层** | `SourceSnapshot`／`SourceFile` 等 **Python 类定义放在哪个文件** | **保留在 `domain/execution/sources.py`**，不移动文件（避免破坏 C 的代码、C 的测试与架构测试） |

这样处理的三个理由：

1. **C 的改动面最小**：只需**补字段**（见 5.2），不需要重构或搬迁；
2. **职责与需求一致**：需求 P1-FR01 把源码快照的建立与内容身份列在 B 的 FR 内；
3. **不产生两套实现**：位置只有一个，规则只有一套，B 与 C 各自识别出的风险（"源码身份字段可能出现两套实现"）从结构上消失。

**请 A（或组长）裁定该分工**，并同步修正架构文档中可能引起歧义的表述（第 7 节记录表与第 1、2 节的措辞）。

#### 5.1.2 支撑该主张的实测证据：C 现有字段与架构要求的差集

**实测**（`develop` = `8a72b0e`）：C 已在 `src/aitest/domain/execution/sources.py` 定义
`SourceFile` 与 `SourceSnapshot`。以架构文档《01-项目与计划》第 2 节"源码快照与检查有效性"表
要求的字段为基准，逐项比对：

| 架构文档第 2 节要求的字段 | C 现有 `SourceSnapshot` | 差集 |
| --- | --- | --- |
| `source_snapshot_id` | `snapshot_id` | 有（命名不同） |
| 仓库与基准提交（`git` 形态） | 无（只有 `binding_revision`） | **缺** |
| 或文件清单摘要（`plain` 形态） | 无（只有 `content_identity` 单值） | **缺** |
| 工作目录范围（选定路径） | 无 | **缺** |
| 排除规则 | 无 | **缺** |
| 文件清单摘要（逐文件相对路径/大小/内容摘要） | `files: tuple[SourceFile, ...]`（`relative_path`／`size`／`sha256`） | 有 |
| 差异摘要 | 无 | **缺** |
| 内容引用 | 无 | **缺** |
| 创建时间 | 无 | **缺** |
| 复取依赖与可复取范围 | 无 | **缺** |

**结论（只陈述差集事实，不评价 C 的实现）**：C 当前实现覆盖了"逐文件摘要 + 一个内容身份字符串"，
其余 **7 项架构要求字段尚未出现**。这说明该记录目前**没有单一所有者**：物理位置在 C 的目录，
而字段语义的绝大部分（`git`／`plain` 身份、排除规则、复取范围）正是 B 的主责内容。

**给 A／组长的三选一**（B 推荐第一项）：

| 选项 | 做法 | 影响 |
| --- | --- | --- |
| **甲（B 推荐）** | 定义层归 B、位置层留 C 目录；C 按 5.1.2 差集补字段，B 提供规则与判据 | 符合两份分册；C 只需补字段；一套实现 |
| 乙 | `SourceSnapshot` 整体归 B（含类定义） | 需移动 C 的代码并改架构第 7 节表，C 的现有测试与 import 需跟着改 |
| 丙 | 整体归 C，B 只提供"选哪些路径、按什么规则排除"的输入 | B 失去内容身份定义权，与 P1-FR01 对 B 的职责分配不符 |

**裁定前 B 的行为**：不新建第二套 `SourceSnapshot`，不修改 `domain/execution/`，
`git` 形态源码身份暂不实现；`plain` 形态的既有最小内容身份（`domain/project/context.py` 的 `SourceManifest`）
保持不变，等裁定后再决定它与 `SourceSnapshot` 的关系。

### 5.2 三个端口的定义归属与两处口径重复

**冲突事实（原文可核对）：**

| 端口 | 《01-项目与计划》第 9 节"本册的端口" | 《04-存储与恢复》第 9 节"本册的端口" | 现状 |
| --- | --- | --- | --- |
| `ModelProvider` | **列出（B）** | 未列 | `ports.py` 中无签名 |
| `SourceSnapshotPort` | **列出（B）** | 未列 | `ports.py` 中无签名 |
| `SourceControlPort` | **列出（B）** | 未列 | `ports.py` 中无签名 |
| `Clock` | **列出** | **列出（重复）** | 已有签名 |
| `ProjectionPort` | 正文指定 B 为使用者，**表内未列** | 未列 | `ports.py` 中无签名 |

**也就是说**：这三个端口按原文是 **B 自己的端口**，但 `application/ports.py` 的所有者按第 4 节约定是 A，
于是形成"**定义权与提交权分离**"——B 有定义权却无提交权，A 缺席时端口无法落地。

**B 的诉求（三选一，B 推荐第一项）：**

| 选项 | 做法 | 影响 |
| --- | --- | --- |
| **甲（B 推荐）** | 端口定义仍以本文件第 8 节草案为准，**由 B 在自己的 PR 内、按第 4 节"只追加本包段落"的约定加入 `application/ports.py`**，PR 内注明并请 A 事后复核 | 与 C 当年的做法一致（C 已改过两次）；不重排他人段落，冲突面小；**需要先修改 B 包 AI 规则第 2 节**（该规则现禁止 B 修改 `ports.py`） |
| 乙 | 维持现状：B 只提需求，等 A 加入 | 零风险，但 A 缺席期间 B 的 Sprint 2/5 端口类工作全部停在原地 |
| 丙 | 由组长指定专人统一维护 `ports.py` | 职责最清晰，但需要有人实际接手 |

**需要一并裁定的两处口径重复**：`Clock` 被两份分册同时列出；`ProjectionPort` 的归属两册都未列。

### 5.3 GitHub 只读：B 需要哪些远端信息（B-Q04）

需求与架构只规定"GitHub 只读为**可选**能力、绿色状态不能替代业务通过、`plain` 不注册不调用 Git 能力"，
**未规定获取方式**（`git remote` 元信息 / `gh` CLI / HTTPS API）与失败降级口径。本节给出 B 侧的最小需求面，
供 A 实现 `SourceControlPort` 适配器时对照。

**B 需要的信息（只读，共 4 类）：**

| # | 需要的信息 | B 的用途 | 缺失时的行为 |
| --- | --- | --- | --- |
| 1 | 本地 Git 元信息：仓库标识、当前分支、基准提交 | 建立 `git` 形态绑定的基准（`BindingForm.GIT` 的三个字段） | 本地 Git 也可用时正常降级为"无远端信息"，不阻塞 |
| 2 | 工作区相对基准的变更：新增/修改/删除文件清单 | 源码快照的差异摘要、变更影响与回归范围（FR03） | 保留未知，不用其他字段冒充 |
| 3 | 远端变更：目标分支相对本地的落后/领先提交 | 提示"依据可能已过期" | **不阻塞任何本地运行** |
| 4 | 远端已有检查状态（CI 结果） | 仅供报告**分开展示**，不参与判定 | 报告中标注"未获取"，**不得默认通过** |

**硬性约束（B 侧已实现/将实现）：**

- `plain` 形态**不注册、不调用**任何 Git 能力（`tests/contracts/test_project_vocabulary.py` 已锁定键的省略）；
- GitHub 绿色状态**永远不能**作为 L1 通过或业务通过的依据；
- 远端不可达时按"**可选能力不可用**"处理，与"本地 Git 不可用"分开报告；
- 凭据经 `SecretPort` 按 `GitHub` 用途解析，**不进入配置、日志、面板、导出**。

**请 A 确认**：① 获取方式（建议优先本地 `git` 元信息，GitHub 远端走 HTTPS，避免依赖 `gh` CLI 是否安装）；
② 第 3、4 类信息是否纳入一期实现范围（若纳入，B 需要适配器的返回结构）；③ 失败降级口径。

---

## 6 待 A 确认问题清单

1. 第 3 节列出的端口方法是否照单加入，还是希望 B 先提交一版签名草案？
2. `register_preparation` 的"同键异摘要返回冲突"是在端口层实现，还是由 B 的应用用例在事务内检查？
3. **`SourceSnapshot` 的归属请裁定**（第 5.1 节，B 推荐选项甲）。
4. **三个端口的定义归属与提交方式请裁定**（第 5.2 节，B 推荐选项甲）；
   并请一并明确 `Clock` 的重复列出与 `ProjectionPort` 的归属。
5. **GitHub 只读的获取方式与范围请确认**（第 5.3 节，B-Q04）。
6. `PreparedRun` 是否需要注册为查询结果类型（供 D 展示）？若需要，请给出查询入口建议。
5. 工作单元是否提供"提交后可见性"的读取入口，还是 B 需要单独的查询端口？

---

## 7 确认记录

| 日期 | 版本 | 变更 | 确认方 |
| --- | --- | --- | --- |
| 2026-09-24 | 0.1 | 初稿 | B 包（待 A 回复） |

---

## 8 端口签名草案（B 包提供，待 A 采用或修正）

> **本节性质**：B 在等 `WorkspaceUnitOfWork` / `RecordRepository` / `SourceSnapshotPort` 的签名期间，
> 按第 3 节的语义需求写了一版**可直接照抄的草案**，供 A 采用或据以反驳。
> **B 不修改 `application/ports.py`**（该文件所有者按第 4 节约定是 A）。
> 项目负责人已裁定由 A 维护 `application/ports.py` 的物理文件，B 提供并确认业务语义；详见第 10 节。
> A 若采用别的形态，B 按 A 的形态改自己的应用用例，不改本节以外的既有代码。
>
> 草案遵守 A 已建立的既有约定：`application/ports.py` **保持单文件**、
> 各包只在自己的段落追加（第 4 节）。

### 8.1 建议同时放在 `application/ports.py` 的公共类型

```python
from dataclasses import dataclass
from typing import Literal

AggregateKind = Literal[
    "project", "binding", "module", "dependency_set", "task", "delivery",
    "acceptance_item", "environment", "source_snapshot", "template_ref",
    "generated_content", "rule_draft", "rule_version", "case", "case_link",
    "plan", "acceptance_scope", "preparation_record", "model_outbound_policy",
]


@dataclass(frozen=True, slots=True)
class RevisionRef:
    """一次写入产生的不可变修订引用。"""

    aggregate_kind: AggregateKind
    record_id: str
    revision: int
    digest: str


@dataclass(frozen=True, slots=True)
class CommitResult:
    """一次提交的结果。

    `commit_seq` 是**提交序号**：B 的业务顺序一律按它判断，不使用系统时间
    （功能文档第 5 节）。`created` 按 `(aggregate_kind, record_id)` 索引本次提交
    产生的修订，调用方据此拿到新修订号，不必再读一次。
    """

    commit_seq: str
    created: Mapping[tuple[AggregateKind, str], RevisionRef]


@dataclass(frozen=True, slots=True)
class Page[T]:
    """项目范围内的稳定分页；列表读摘要，详情按引用读取。"""

    items: tuple[T, ...]
    next_cursor: str | None
```

### 8.2 `WorkspaceUnitOfWork`（草案）

```python
class WorkspaceUnitOfWork(Protocol):
    """短事务边界；同一提交内保存记录、引用、索引与幂等结果。"""

    def commit_seq(self) -> str:
        """当前提交序号；已提交状态下的业务顺序依据。"""
        ...

    def stage_record(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> RevisionRef:
        """在**同一事务内**暂存一条不可变修订。

        `expected_revision` 为 `None` 表示"新建"。与当前修订不一致时抛
        `StaleRevisionError`，并带上**当前修订与差异提示**，**不自动覆盖用户编辑**。
        """
        ...

    def stage_preparation(
        self,
        record: PreparationRecord,
        *,
        payload: Mapping[str, object],
    ) -> RevisionRef:
        """登记准备记录与 `intent_id`——**必须在同一提交内**（架构文档第 11 节）。

        同一 `(project_id, client_id, prepare_request_id)`：
        摘要相同返回原记录的修订；摘要不同抛 `PreparationConflictError`
        （**不覆盖**）。
        """
        ...

    def commit(self) -> CommitResult:
        """提交并发布索引；提交成功后对后续读取可见。"""
        ...

    def rollback(self) -> None:
        """放弃本次暂存；不产生任何可见修订。"""
        ...
```

**待 A 定的三处**：

1. **谁提供实例**：`bootstrap.py` 注入，还是另有工厂？B 的应用用例需要一个入口拿到它。
2. **暂存顺序**：B 希望"先 `stage_*` 再 `commit`"，以便在提交前知道修订号（`payload_hash`
   要引用各来源修订）。若 A 采用"提交时才分配修订号"，B 需要改为两阶段写法。
3. **提交后可见性**：第 6 节第 5 问——工作单元是否提供提交后的读入口，还是 B 走
   `RecordRepository`？B 的用例倾向后者（读写分开，便于测试）。

### 8.3 `RecordRepository`（草案）

```python
class RecordRepository(Protocol):
    """不可变修订的读取与项目范围分页；**所有 read 必须接受显式修订**。"""

    # --- 项目与绑定 -------------------------------------------------
    def read_project(self, project_id: str, revision: int) -> Mapping[str, object]: ...
    def list_projects(self, *, cursor: str | None = None, limit: int = 50) -> Page[Mapping[str, object]]: ...
    def read_binding(self, binding_id: str, revision: int) -> Mapping[str, object]: ...

    # --- 环境 -------------------------------------------------------
    def read_environment(self, environment_id: str, revision: int) -> Mapping[str, object]: ...

    # --- 计划与用例 -------------------------------------------------
    def read_plan(self, plan_id: str, revision: int) -> Mapping[str, object]: ...
    def read_acceptance_scope(self, scope_id: str, revision: int) -> Mapping[str, object]: ...
    def read_case(self, case_id: str, revision: int) -> Mapping[str, object]: ...
    def read_case_links(self, case_id: str, revision: int) -> Mapping[str, object]: ...

    # --- 规则与模板 -------------------------------------------------
    def read_rule_version(self, rule_id: str, revision: int) -> Mapping[str, object]: ...

    # --- 幂等查询（按业务身份，不返回"最新"）------------------------
    def find_preparation(
        self, project_id: str, client_id: str, prepare_request_id: str
    ) -> Mapping[str, object] | None: ...
    def find_preparation_by_intent(self, intent_id: str) -> Mapping[str, object] | None: ...
```

**关键要求（对应第 3.1 节）**：

1. **按准确修订读取**：所有 `read_*` 必须接受显式修订参数，**不得默默回退到最新值**。
   "B 不向 C 传当前最新计划"这条约束就落在签名上。
2. **返回原始 payload**：B 用自己的 `application/project/serialization.py` 还原领域对象，
   因此端口不必了解 B 的领域类型（**避免端口依赖 domain 具体类**）。
   若 A 更愿意直接回领域对象，B 也接受，但需要在 `ports.py` 里 import B 的类型，请 A 判断。
3. **列表读摘要**：`list_*` 只返回摘要，详情按引用读取（第 3.1 节第 3 条）。

### 8.4 `SourceSnapshotPort`（待 A 按裁定冻结）

```python
class SourceSnapshotPort(Protocol):
    """建立与复取真实被测内容，读取源码变化；内容身份来自实际字节摘要。"""

    def pin(
        self,
        *,
        canonical_path: str,
        purpose: Literal["analysis", "prepare"],
        selected_paths: Sequence[str] = (),
        exclusion_rules: Sequence[str] = (),
    ) -> Mapping[str, object]:
        """按实际字节固定一份快照；`mtime` 只作变化提示，不证明内容相同。"""
        ...

    def materialize(self, snapshot_id: str, destination: str) -> Mapping[str, object]:
        """把已固定内容物化到指定目录；返回实际路径映射与内容摘要。"""
        ...

    def read_pinned(self, snapshot_id: str) -> Mapping[str, object]:
        """按稳定标识读取已固定快照的元数据（不重新扫描目录）。"""
        ...

    def detect_changes(self, snapshot_id: str) -> Mapping[str, object]:
        """与已固定快照比较，返回变化清单；无法证明未变时不得报"未变"。"""
        ...
```

**已裁定的类型边界**（第 10 节）：
`SourceSnapshot` 的**领域对象**在 C 的 `domain/execution/sources.py`，而**建立时机、`purpose`
取值、排除规则与内容身份计算规则**归 B。B 的 `InputRevisions.snapshot_revision` 需要把它
规约成一个整数修订号。上图草案用 `Mapping[str, object]` 回避了类型归属问题，
字段名以《一期架构01：项目与计划》的冻结语义和唯一 `SourceSnapshot` 模型为准，不再以归属未定为由暂停接线。

### 8.5 方法 → 产品要求对照

| 方法 | 对应要求 | 依据 |
| --- | --- | --- |
| `WorkspaceUnitOfWork.commit` | "记录、引用、索引与幂等结果**同一事务**提交" | 架构文档第 8 节；根 `AGENTS.md` 第 3 节 |
| `WorkspaceUnitOfWork.stage_record(expected_revision)` | "比对 `expected_revision`；冲突返回当前修订和差异提示，**不自动覆盖用户编辑**" | 架构文档第 8 节末 |
| `WorkspaceUnitOfWork.stage_preparation` | "在工作单元内按 `(project_id, client_id, prepare_request_id)` 登记 `PreparationRecord` 及 `intent_id`；**输入摘要不同返回冲突**；**与准备记录同一次提交**" | 架构文档第 11 节 |
| `RecordRepository.read_*(revision)` | "所有 `read_*` 必须接受显式修订参数，不得默默回退到最新值" | 实施方案第 3 节；本文件第 3.1 节 |
| `RecordRepository.find_preparation*` | "prepare/start 响应丢失通过同键查询返回原结果"；"跨入口恢复通过 `intent_id` 或准备查询" | 架构文档第 11 节 |
| `RecordRepository.list_projects` | "列表读摘要，详情按引用读取"；有限 `QuerySpec` | 存储与恢复第 13 节 |
| `SourceSnapshotPort.pin` | "内容身份必须来自实际字节摘要；元数据（mtime）仅作为变化提示" | B 包 AI 规则第 3.6 节；架构文档第 2 节 |

### 8.6 并发语义（第 6 节第 2 问的 B 方建议）

**建议在端口层（事务内）实现**，理由是"并发完成同一准备请求只能发布一条
`PreparationRecord`，其余取得已发布结果"（架构文档第 11 节）——这句话描述的是
**并发下的写入结果**，应用用例在事务外无法证明它。因此：

- `stage_preparation` 在同一事务内按三个身份键检查已有记录；
- 摘要相同 → 返回原记录修订，**不新建**；
- 摘要不同 → 抛 `PreparationConflictError`，携带原记录的 `intent_id`、`payload_hash`
  与 `created_at_commit`，供调用方给出明确提示；
- B 的 `decide_preparation()` 只负责**单线程下的判定与提示内容**，不承担并发保证。

### 8.7 B 已按本草案实现的规则（可独立验证，不含 I/O）

| 产物 | 位置 |
| --- | --- |
| `InputRevisions`、`PreparationRequest`、`PreparationRecord` | `src/aitest/application/planning/preparation.py` |
| `payload_hash()`（业务输入摘要；不含传输层参数） | 同上 |
| `decide_preparation()` 四态判定（`new`/`reused`/`conflicted`/`needs_reprepare`） | 同上 |
| 绑定序列化：不适用键真正省略、可往返 | `src/aitest/application/project/serialization.py` |

设计依据见 `docs/文档-feix-a/B包/10-准备意图与幂等规则设计说明.md`。
**A 的签名一旦落地，B 只需在这些规则外面加编排，规则本身不再改动。**

### 8.8 B 接线后确认需要的三个底层方法（2026-10-01）

B 已按第 8.2／8.3 节把 `application/planning/substrate_adapter.py` 从骨架实现为可用转接头，
并在 `tests/unit/test_substrate_adapter.py` 里用 A 的 `FileUnitOfWork` /
`FileRecordRepository` **真落盘**跑通 `prepare_run`（含"重启后按业务身份读回"）。

A 的现有实现已经具备其中大部分能力，但下面三项**只存在于具体实现里、没有进
`application/ports.py` 的协议**，因此 B 现在只能靠"注入什么用什么"接线，
无法在类型与合同层面确认它们会一直存在：

| 需求 | A 现状 | 为什么 B 需要它 |
| --- | --- | --- |
| `RecordRepository.current_revision(aggregate_kind, record_id) -> int` | `FileRecordRepository` 已有同名方法 | ① 修订冲突时 B 必须返回**当前修订与差异提示**（架构 01 第 8 节末），A 的 `ValueError("revision conflict")` 不带这个值；② 按业务身份查回准备记录要读"当前修订" |
| `WorkspaceUnitOfWork.commit_seq() -> str` | 无 | 准备登记的 `created_at_commit` 与"依据需重新准备"提示都要在**未提交**时读当前提交序号；`prepare_run` 的阻塞与复用分支**不暂存任何记录** |
| `WorkspaceUnitOfWork.next_commit_seq() -> str` | 无 | `PreparationRecord.created_at_commit` 必须在 `commit()` **之前**写进不可变 payload。A 的提交序号**按记录递增**，B 侧语义是"本次提交完成后会得到的序号" |

**建议签名（A 可采用或改形态）**：

```python
class RecordRepository(Protocol):
    def current_revision(self, *, aggregate_kind: str, record_id: str) -> int: ...

class WorkspaceUnitOfWork(Protocol):
    def commit_seq(self) -> str: ...
    def next_commit_seq(self) -> str: ...
```

**兼容性**：三者都是**只读新增**，不改变任何已发布字段、记录形状或错误语义。
`FileUnitOfWork` / `FileRecordRepository` 已经持有对应事实
（`records.json` 的提交计数、按 `(kind, record_id)` 的修订条数），
补齐属于**暴露**，不是新增能力。

**B 侧的临时接法（A 冻结后移除）**：`PortsUnitOfWork` 接受可选的 `CommitSequenceSource`；
集成测试用 A 的 `RecoveryOrchestrator.inspect()["committed_sequences"]` 提供提交序号，
不访问 A 的存储内部文件。A 冻结签名后由装配点换成正式访问器，
B 的用例与测试不改。这三个方法缺失时，转接头抛 `SubstrateContractError` 并在消息里指到本节，
不用默认值顶替。

**另需一并确认的一处口径**：`commit_sequence` 是**工作空间全局**计数（`records.json` 的 `commit`），
B 目前只依赖它在**同一项目内单调**。一期若允许多项目共用一个工作空间，
`created_at_commit` 的跨项目可比性需要明确；B 不自行假定。

### 8.9 B 侧已完成的接线（2026-10-01）

| 项 | 位置 | 状态 |
| --- | --- | --- |
| 薄转接头 | `src/aitest/application/planning/substrate_adapter.py` | 已实现；`application` 层不 import `infrastructure`，底层由装配点注入 |
| 真实存储集成测试 | `tests/unit/test_substrate_adapter.py` | 14 项通过（真落盘 + 重启读回 + 修订冲突 + 索引缺失显式报维护） |
| 准备记录身份与落盘形状 | `src/aitest/application/planning/preparation.py` | 记录标识/意图标识由三元组派生摘要，**带项目与客户端命名空间** |
| 摘要口径 | `src/aitest/application/planning/prepare_run.py` | 摘要键集合与 `PAYLOAD_FIELDS` 逐字一致，有对照测试 |

**仍未接通**：B 的用例**进入产品统一入口**还缺装配点改造——`bootstrap.CoreBootstrap.create()`
目前只把 `FileUnitOfWork` 交给 `LocalAPI(transaction_port=...)`，
`register_use_cases` 注册的 handler 拿不到工作单元与只读仓储
（`Handler = Callable[[Command], Mapping]`，没有依赖注入）。
装配点归 A；本包不修改该文件。所需的端口面见第 3 节。

### 8.10 B 的用例已可经统一入口运行（2026-10-01，B 侧自证）

上一节的"仍未接通"**在本轮被绕过一步，但没有被取消**：B 改成
**在注册时把依赖闭包进 handler**，因此不需要 A 先改 `Handler` 签名也能跑通。实测如下。

| 项 | 位置 | 状态 |
| --- | --- | --- |
| 动作表与依赖包 | `src/aitest/application/usecase_registry.py`（新增） | `BUseCaseDependencies` + `build_b_use_case_registry()`；**不 import** `bootstrap` / `interfaces` / `infrastructure` |
| 注册到入口 | `src/aitest/interfaces/local/b_registration.py`（新增） | `register_b_use_cases(api, deps)`：把 B 的动作并进 `LocalAPI.handlers`，同名动作**拒绝覆盖** |
| 合同测试 | `tests/contracts/test_b_use_case_registration.py`（新增，10 项通过） | 用**真实 `Command` + 真实 `FileUnitOfWork`** 经 `LocalAPI.dispatch()` 写入并读回；含重启读回、只读动作免写身份、修订冲突、索引待重建、参数非法五类反例 |

本轮暴露并已固定的动作（**仅项目上下文类**）：

| 动作 | 语义 | 记录类别 |
| --- | --- | --- |
| `save_context` | 保存项目（模块随项目一起） | `project` |
| `save_binding` | 保存 Git/plain 绑定 | `binding` |
| `save_environment` | 保存环境引用 | `environment` |
| `save_dependency_graph` | 保存模块依赖图 | `dependency_set` |
| `query` | 有界查询（读动作） | — |

错误码（由 `BUseCaseError.code` 透到 `Response.error.code`）：
`B_INVALID_PARAMETER`、`B_REVISION_CONFLICT`、`B_PREPARATION_CONFLICT`、
`B_INDEX_MAINTENANCE_REQUIRED`、`B_INVALID_QUERY_CURSOR`。

两处细节值得 A/C/D 知悉：

1. **写动作的成功结果只报 `aggregate_kind` / `record_id` / `revision`，不报提交序号**。
   原因：`application/project/persistence.py` 的 `save_*` 自带 `open`/`commit` 并只返回
   `StagedRevision`，返回时提交序号已经前进，事后补读会拿到**下一次**的序号。
   B 选择少报一个字段，而不是报一个会误导"业务顺序"的值。若将来需要随写返回提交序号，
   接口形态需要改（由拥有 `save_*` 语义的一方决定），B 不在本轮自行发明。
2. **`query` 在索引缺失时返回 `B_INDEX_MAINTENANCE_REQUIRED`，不返回空列表**。
   实测依据：`PortsRecordReader.query()` 把 A 的 `status=maintenance_required` 翻成
   `IndexMaintenanceRequired`；若直接透传会变成 `INTERNAL_ERROR`，
   调用方无法把"索引待重建"与"真的没有数据"分开。

**两项仍然只归 A，本包不动**：

1. **装配点接线**：产品路径目前是 `CoreBootstrap.create()` 一次性装配；
   要让 B 的动作在**跨进程唯一核心**里也生效，仍需装配点把
   `BUseCaseDependencies` 交给 `register_b_use_cases()`。本包不修改 `bootstrap.py`。
2. **`Handler` 依赖注入（可选）**：若 A 愿意把 `Handler` 扩成可收依赖，
   B 的改动只是把"注册时闭包"换成"装配时注入"，动作表与测试不变。
   本包不主张必须改签名——现有形态已经可用。

**尚未包括**：`publish_rules`、`publish_plan`、`generate_draft`、
模型出站类动作。它们各自需要参数字段的适配（`PreparationInputs` 有二十余个字段），
另行分批，不在此节声称已接通。

### 8.11 `prepare_run` 已进统一入口；B 侧交付说明另立文件（2026-10-02）

- `prepare_run` 的**参数适配层已实现并注册**（`usecase_registry.py` 的
  `_preparation_inputs()` / `handle_prepare_run()`）：键名与 `PreparationInputs` 逐字一致，
  成功返回 `PreparedRun.model_dump(mode="json")`，与 `BC-001` 同一套字段。
  合同测试 `tests/contracts/test_prepare_run_entrypoint.py`（**10 项**）。
- **一条需要 A 注意的实测事实**：`prepare_run` **已经依赖提交序号**——
  其阻塞分支与 `created_at_commit` 都要在未提交时读 `commit_seq` / `next_commit_seq`；
  缺少注入时转接头抛 `SubstrateContractError`。也就是说第 8.8 节那三个方法
  **不再是"将来才需要"，而是准备链路的现行前置**。
- B 侧的实现／验证／缺口按 `接口对接/AGENTS.md` 第 4 节单独登记在
  [`delivery-B.md`](delivery-B.md)，本文件不再重复。

---

## 9 变更记录

| 日期 | 版本 | 变更 | 确认方 |
| --- | --- | --- | --- |
| 2026-09-24 | 0.1 | 初稿：记录清单、端口语义需求、协作约定、`SourceSnapshot` 归属冲突 | B 包（待 A 回复） |
| 2026-09-28 | 0.2 | 补第 8 节：**可直接照抄的端口签名草案**（公共类型、三个 Protocol、方法→要求对照、并发语义建议）；补第 8.7 节 B 已实现的纯规则部分 | B 包（待 A 采用或修正） |
| 2026-09-28 | 0.3 | 第 5 节拆为 5.1 `SourceSnapshot` 归属（补 B 主张与**字段差集实测证据**）、5.2 端口定义归属与 `Clock`／`ProjectionPort` 口径重复、5.3 **GitHub 只读 B 侧需求（B-Q04）**；修正第 3.5 节端口归属记述 | B 包（待裁定） |
| 2026-09-28 | 0.4 | 正文的提出方／接收方／确认方统一改用**包名**（不使用成员名），与本目录其余文档一致 | B 包 |
| 2026-09-30 | 0.5 | 项目负责人裁定 `SourceSnapshot` 分工、端口维护方式、横切端口归属及 Git/GitHub 一期边界；三项由待裁定转为待实现 | 袁（项目负责人） |
| 2026-10-01 | 0.6 | 补第 8.8 节：B 接线后确认需要 A 冻结的**三个只读方法**（`current_revision` / `commit_seq` / `next_commit_seq`）及缺少时的行为；补第 8.9 节记录 B 侧已完成的接线与**仍未接通的产品入口**。本节只提需求，不改 B 侧协议 | B 包（待 A 确认并冻结） |
| 2026-10-01 | 0.7 | 补第 11 节：**`SourceSnapshot` 字段口径**（B 主责，按第 10.1 节裁定给出字段、形式互斥、内容身份与失效判据），供 C 评审执行兼容后在其唯一模型里落地。**只冻结字段语义，不改变任何现行 Schema 字节**。本节内容于 2026-09-30 写成于 `feat/b-sourcesnapshot-fields`，该分支未及时提交评审；现基于当前 `develop` 重新施加 | B 包（待 C 评审） |
| 2026-10-01 | 0.8 | 补第 8.10 节：B 把依赖**闭包进 handler**，因此不必先等装配点改造即可经统一入口运行项目上下文类动作；登记 5 个动作、5 个错误码、合同测试 10 项，并说明"写动作不报提交序号"与"索引缺失不返回空列表"两处细节。装配点接线与 `Handler` 依赖注入仍归 A | B 包（知悉性登记，待 A 确认装配点接法） |
| 2026-10-02 | 0.9 | 补第 8.11 节：`prepare_run` 参数适配层已实现并注册（合同测试 10 项）；**登记"准备链路已依赖提交序号"这一实测事实**——第 8.8 节三个方法由"将来需要"变为现行前置。B 侧交付说明另立 `delivery-B.md`，本节不重复 | B 包（知悉性登记，待 A 确认接法与冻结签名） |
| 2026-10-02 | 1.0 | 第 11 节标题明确为**最终口径**：该口径按第 10.1 节裁定写成，属 B 主责范围内的字段定义，可据以实施 | B 包（字段口径已定；待 C 回写 Q1／Q2 兼容性并按新契约 PR 实施） |

---

## 10 项目负责人裁定（2026-09-30）

裁定人：袁（项目负责人）。以下结论覆盖第 5、6、8 节中对应的“待裁定”或备选方案表述。

### 10.1 `SourceSnapshot`

- 采用第 5.1 节方案甲：建立时机、`purpose`、选定范围与依赖闭包、排除规则、内容身份算法、Git/plain 身份、复取范围和失效判据由 B 的《项目与计划》主责。
- `SourceSnapshot` / `SourceFile` 类继续唯一放在 `domain/execution/sources.py`，不搬迁、不复制；C 只消费冻结快照并维护实际执行来源核对事实。
- A 实现 `SourceSnapshotPort`、物化与持久化适配；B 提供字段/判据，C 评审执行兼容。现有缺失字段是待实现项，不再是归属问题。

### 10.2 端口定义与提交

- `application/ports.py` 保持协议唯一来源并由 A 维护物理文件、处理合并冲突；业务包提交完整签名草案并确认业务语义，A 确认基础设施可实现性。A 无法及时维护时只能由项目负责人显式指定代维护人，禁止另建同名 Protocol。
- 属于一期的端口必须在一期冻结方法签名、实现和合同测试。`stage_preparation` 所表达的同键幂等/冲突和同次提交语义属于一期；允许复用通用工作单元原语，不强制保留同名专用方法。
- `Clock` 和 `ProjectionPort` 的唯一技术归属是总体架构第 12 节与 `application/ports.py`。各业务分册只定义使用场景或冻结策略；A 维护相应端口及基础设施实现。
- 合并顺序为：业务合同确认 → A 写入端口 → 适配器与调用方接线 → 架构/合同/故障测试。多个端口改动按合同确认顺序合并，后合并方负责解冲突和复测。

### 10.3 Git 与 GitHub

- 本地仓库身份及工作区变更清单是一期 `git` 形态必需能力；由受控 `git` CLI 取得并按所需命令做能力探测，不冻结无证据的最低版本号。
- 远端领先/落后和远端检查状态是一期可选 GitHub 能力，不作为一期本地闭环的完成阻塞；统一通过 HTTPS API 获取，不依赖 `gh` CLI，凭据只经 GitHub 用途的 `SecretRef` / `SecretPort`。
- 本地 Git 不可用会阻塞依赖 Git 身份的固定/准备且不得静默转成 `plain`；远端未配置、未认证、限流、网络失败或服务不可用只降级远端状态，不阻塞本地固定、准备和运行。
- 远端状态只分开展示，永不直接产生 L1 或业务通过；`plain` 不注册、不调用 Git 能力，也不出现仓库字段。

---

## 11 `SourceSnapshot` 字段口径（B 主责，本节为最终口径）

依据第 10.1 节裁定，`SourceSnapshot` 的**建立时机、`purpose`、范围、排除规则、内容身份算法、Git/plain 身份、
复取范围与失效判据**由 B 主责。本节给出**冻结字段口径**，由 C 在 `domain/execution/sources.py` 落地
（类位置不变）。

**本节只冻结字段语义，不改变任何现行 Schema 字节。** 落地前 `SourceSnapshot` 仍按现状运行。
本口径按第 10.1 节裁定写成，属 B 主责范围内的字段定义，**无需另行确认即可据以实施**。

### 11.1 形式互斥（与 `LocalProjectBinding.bind_form` 同一模式）

`SourceSnapshot` 采用与项目绑定**完全相同**的形态互斥规则，不引入第二种表达方式：

| `source_form` | 必须存在 | 必须省略（**不是 `null`、不是空串**） |
| --- | --- | --- |
| `git` | `git_base_commit`、`git_diff_digest` | `plain_manifest_digest` |
| `plain` | `plain_manifest_digest` | `git_base_commit`、`git_diff_digest` |

依据：B 包 AI 规则第 3.6 节"`plain` 形态完全省略 Git 字段，不使用 `None`、空值或'未知'占位"；
序列化时**真正省略该键**（`application/project/serialization.py` 已实现该行为，并有 `key not in payload` 断言）。

### 11.2 冻结字段表

| 字段 | 类型 | 必填 | 含义与判据 |
| --- | --- | --- | --- |
| `snapshot_id` | `str` | 是 | 稳定标识；同一逻辑快照的重新固定产生**新** `snapshot_id`，不复用 |
| `project_id` | `str` | 是 | 归属项目 |
| `purpose` | `Literal["analysis", "prepare"]` | 是 | **取值只有这两个**；`analysis` 用于显式分析，`prepare` 用于准备运行 |
| `binding_revision` | `int` (≥1) | 是 | 固定时的绑定修订，用于判"绑定已变" |
| `source_form` | `Literal["git", "plain"]` | 是 | 决定下列形态字段的**存在性** |
| `selected_paths` | `tuple[str, ...]` | 是 | 工作目录范围（选定路径）；相对路径，POSIX 分隔符，不得为空 |
| `exclusion_rules` | `tuple[str, ...]` | 是 | 排除规则；无排除时为空元组（**合法**，不写 `null`） |
| `files` | `tuple[SourceFile, ...]` | 是 | 逐文件 `relative_path`／`size`／`sha256`；路径唯一、无盘符、无 `..` |
| `content_identity` | `str` | 是 | 见 11.3 的计算口径 |
| `plain_manifest_digest` | `str \| None` | `plain` 必填 | 文件清单摘要；**取值来自 B 的 `SourceManifest.manifest_digest`**，不另起算法 |
| `git_base_commit` | `str \| None` | `git` 必填 | 基准提交 |
| `git_diff_digest` | `str \| None` | `git` 必填 | 工作区相对基准的未提交新增/修改/删除内容摘要 |
| `content_ref` | `str \| None` | 否 | 内容引用（对象摘要）；内容未留存时为 `None` 并同时登记缺口 |
| `created_at` | `datetime \| None` | 否 | 创建时间；经 `Clock` 取得，不使用系统时间 |
| `refetch_dependencies` | `tuple[str, ...]` | 是 | 复取依赖（仓库对象、LFS、子模块）；无则为空元组 |
| `refetch_scope` | `str \| None` | 否 | 可复取范围；**缺失即为缺口**，不留空冒充完整 |

**与 B 既有实现的关系**：`plain` 形态的身份值以 `domain/project/context.py` 的 `SourceManifest`
（`source_scope`／`manifest_digest`／`files`／`exclusion_rules`／`refetch_dependencies`／`refetch_scope`）为准，
本节**不新定义第二套**；`SourceSnapshot` 是其上游的不可变固定事实。

### 11.3 `content_identity` 计算口径

1. **输入**：`source_form`、`files`（按 `relative_path` 升序规范化后）与形态身份
   （`git`：`git_base_commit` ＋ `git_diff_digest`；`plain`：`plain_manifest_digest`）。
2. **规范字节**：对每个文件按 `relative_path`、`size`、`sha256` 生成规范记录行，按路径升序拼接；
   再拼入形态身份；最后取摘要。
3. **不使用**：文件系统时间戳、绝对路径、盘符、目录遍历顺序、`purpose`、`snapshot_id`。
   `mtime` **只作变化提示，永不参与身份计算**。
4. **跨平台**：路径判定必须使用 `PureWindowsPath`；**入记录的路径统一为 POSIX 相对路径**，
   避免同一内容在 Windows／Linux 上算出不同身份。
5. **可复现**：同一输入必须得到同一 `content_identity`；夹具与测试据此做逐字节断言。

### 11.4 `SourceSnapshotPort`（B 提供语义，A 实现）

端口方法的**语义**如下（具体签名形式由 A 按第 10.2 节冻结；B 不自行改 `application/ports.py`）：

```text
pin(canonical_path, purpose, selected_paths, exclusion_rules) -> SourceSnapshot 的 payload
    按实际字节固定；mtime 只作变化提示，不证明内容相同
read_pinned(snapshot_id) -> payload
    按稳定标识读取已固定快照的元数据（不重新扫描目录）
materialize(snapshot_id, destination) -> 实际路径映射与内容摘要
    物化到指定目录；物化副本不进入永久对象库
detect_changes(snapshot_id) -> 变化清单
    无法证明"未变"时不得报"未变"
```

### 11.5 待 C 确认（执行兼容性，不是归属问题）

| # | 待确认项 | 责任方 | 下一动作 | 阻塞影响 |
| --- | --- | --- | --- | --- |
| Q1 | §11.1 形式互斥是否符合 C 侧对 `plain` 的解析预期 | C | 在本文档追加确认 | 不阻塞现状（现行 `SourceSnapshot` 不含这些字段） |
| Q2 | §11.2 字段名与类型是否与 C 侧 `sources.py` 现有 `SourceFile`／`SourceSnapshot` 兼容 | C | 同上 | 决定 C 的补字段改动是否为纯新增 |
| Q3 | §11.3 `content_identity` 改由 B 口径计算后，C 现有 `content_identity` 构造值如何迁移 | B＋C | B 给迁移说明 | 决定是否需提升 Schema 主版本 |
| Q4 | `SourceSnapshot` 是否登记为 `PreparedRun.InputRevisions.snapshot_revision` 的来源修订 | B | B 在 BD/BC 合同中引用本节 | 影响 `PreparedRun` 快照修订语义 |

**B 侧下一步**：本节字段口径已定（见本节开头说明）。C 回写 Q1／Q2 的执行兼容性结论后，
按**新的契约 PR** 在 `domain/execution/sources.py` 补齐字段；字段实现不在本合同内散改。

### 11.6 本节不改变的事项

- **不改动 `docs/项目文档/**`**：架构第 7 节的表述歧义由裁定记录引用，不在本项目改动。
- **不新建第二套 `SourceSnapshot`**：类位置仍唯一在 `domain/execution/sources.py`。
- **不手工改生成 Schema 与夹具**：字段落地后由声明所有者重新生成。
- **不把本节的"冻结字段"写成"已实现"**：`provider_implementation` 仍为 `partial`。
