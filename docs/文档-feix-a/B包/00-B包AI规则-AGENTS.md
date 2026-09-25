# B 包 AI 协作规则

本文件是仓库根 `AGENTS.md` 的**补充**，适用范围为 B 包（项目与计划，P1-FR01—FR07）负责的目录。
与根文件冲突时**以根文件为准**；本文件只写 B 包特有、根文件未覆盖的约束。

依据：组长《一期工程四部分拆分与低对接实施方案》第 2 条——"为了**防止 ai 乱改东西**，越过了自己负责的范围、
自己的权限，或是对代码的结构、编写风格有要求等，我们要写一份 AI 规则，`AGENTS.md`"。

---

## 1 我负责什么

- **功能**：P1-FR01—FR07（项目/模块/环境上下文、交付说明、变更影响与回归、模板与检查内容、规则版本、计划与用例、档位与驱动）。
- **主责分册**：`docs/项目文档/一期/架构文档/01-项目与计划.md`。
- **我的目录**：

  ```text
  src/aitest/domain/project/          （与根 AGENTS.md 一致）
  src/aitest/domain/planning/
  src/aitest/application/project/
  src/aitest/application/planning/
  src/aitest/application/ai_assistance.py
  src/aitest/resources/templates/
  src/aitest/contracts/prepared_run.py        （待新建）
  tests/contracts/fixtures/prepared_run/      （待新建）
  docs/文档-feix-a/、docs/修改日志/feix-a/
  ```

- **唯一对外业务交付**：`PreparedRun`（B 产生、C 读取、D 仅作展示）。
- **牵头验收**：P1-AC01、AC03、AC12、AC17、AC20、AC30、AC31、AC32。

## 2 绝对不要碰（防止越界与冲突）

| 路径 | 所有者 | 原因 |
| --- | --- | --- |
| `src/aitest/application/ports.py` | **A** | 架构文档第 7 节：唯一的端口归属位置。需要新端口先写 `docs/接口对接/B-A-端口与保存需求.md` |
| `src/aitest/infrastructure/file_store/` | **A** | B 只经端口保存材料，不直接读写业务文件 |
| `src/aitest/domain/execution/`、`domain/evidence/` | **C** | C 已在这些目录有实现（commit `d60781d`） |
| `src/aitest/application/execution/`、`application/evidence/` | **C** | 同上 |
| `src/aitest/domain/review/`、`application/review/` | **D** | 判定与报告 |
| `src/aitest/interfaces/`、`src/aitest/resources/panel/`、`integrations/trae/` | **D** | 入口与界面 |
| `tests/acceptance/p1/status.json` 中非 B 牵头的条目 | 各包 | 只更新自己负责的 AC 行 |
| `main` / `develop` 分支 | 全组 | 组长第 1 条：不直接改，合并走 PR |
| `docs/项目文档/**`（共享规范） | 全组 | 发现冲突先登记，**不单方面修改**（根 `AGENTS.md` 第 5.4 条） |

**不要新建这些模块**（`tests/architecture/test_boundaries.py` 会检查其不存在）：
`interfaces/asgi_host.py`、`application/collaboration`、`domain/identity`、`domain/delivery`、
`application/diagrams.py`、`application/locations.py`。

## 3 B 包的硬性不变量

写任何代码前先读这一节。违反其中任意一条即为缺陷，不是风格问题。

**模板与草稿**
- 模板**只能生成草稿**；发布是独立的人工动作。
- 生成不得增加工具或执行入口；**人工修订不被重新生成覆盖**。
- 模板必须给出适用条件、稳定 `item_id`、必测集合、关键链路清单；**空关键链路必须有 `no_critical_path_reason`**。
- 模板正文**不是可执行 shell**（`contracts/templates.py` 用 `extra="forbid"` 强制）。

**规则**
- `RuleDraft` 未确认不生效；发布 `RuleVersion` 不可变；新测试用新版，历史保留原版。
- 导入规则保留来源与未识别扩展字段，但**未知执行字段不得静默启用**。

**用例与断言依据**
- 三态不可合并：`missing` 阻止进入 full 必测；`present_unconfirmed` 可执行但计未核实、**不计 V**；`confirmed` 仍须实际证据。
- `assertion_basis_state` 是 Case 修订保存时的冻结值；`effective_assertion_basis_state` 是**派生值**，**不回写冻结 Case**。
- 依据文本变化后**旧确认失效**。
- 发布时必须登记**独立核验方式**；缺失则该用例不得计入已验证。
- 无预期结果或前置条件的用例不得发布为可直接验收。

**计划、范围与门禁**
- `T ⊆ M`（模板下限包含于必测）；`full` 要求 `M ⊆ S` 且**跳过项为 0**。
- 不得删必测、不得弱化断言、不得变更适用性来规避门禁；无关新增用例不能抵消遗漏。
- `AcceptanceScope` 计划发布前定义；运行冻结 `scope_revision`；**范围变更重新发布，不追认原运行**。
- 不适用项必须给出原因且不计入分母；**"未执行"不等于"不适用"**。

**档位与驱动**
- `ConclusionCeiling` **只由档位派生**：`quick`/`on_demand` → `partial`；`full` → `passable`。
- 不得用覆盖率或驱动方式放宽结论上限。
- 驱动只允许 `planned → stepwise`；反向必须拒绝并提示新建运行。
- `quick`/`on_demand` 不派生证据等级。

**源码与环境**
- 内容身份必须来自**实际字节摘要**；元数据（mtime）只是变化提示。
- `plain` 形态**完全省略** Git 字段，不用 `None` / 空值 / "未知"占位。
- 工作空间位于同步盘/网络盘时**拒绝建立**并说明原因。
- 显式不隔离是**合法值**，不等于缺配置。

**准备与幂等**
- 按 `(project_id, client_id, prepare_request_id)` 登记；同键异输入摘要**返回冲突，不覆盖**。
- `intent_id` 与准备记录**同一次提交**；跨入口恢复用 `intent_id`，**不用 `request_id`** 代替业务身份。
- 源码读取与摘要计算在**短事务外**。
- 业务顺序按**提交序号**判断，不用系统时间。
- 同请求期间源码已变 → 返回"依据需重新准备"，**不把新字节塞进旧意图**。

**模型出站**
- `source_snippets` **默认关闭**；只发送显式选定的脱敏材料。
- 模型**只输出草稿**，不能直接改断言、关闭缺陷、作结论。
- 迟到响应标过期，**不覆盖当前人工版本**。
- 模型不可用时可人工继续；**不在失败重试中暗换供应商**。
- GitHub 只读是可选能力；**绿色状态不能替代业务通过**。

**架构**
- `domain/` **只允许标准库**，不 import `pydantic` / `contracts` / `application` / `infrastructure` / `interfaces`。
- `application/` 不 import `infrastructure` / `interfaces`。
- 领域对象用 `@dataclass(frozen=True, slots=True)`，违反不变量抛 `ValueError`（与现有代码一致）。
- 时间经 `Clock` 端口，不用 `datetime.now()`。
- `contracts/schemas/*.json` 是**生成物**，只由 `scripts/generate_schemas.py` 产出，**不手工编辑**。

## 4 改代码前后的固定动作

1. `git checkout develop && git pull origin develop`，再回到功能分支 `git merge develop`（组长第 11 条）。
2. **先更新 `02-B包施工检查项清单.md` 与对接文档，再动代码**（组长第 3 条：用文档对接）。
3. 改完跑四条命令：

   ```powershell
   uv run ruff check .
   uv run mypy
   uv run pytest -q
   uv run python scripts/generate_schemas.py
   git diff --exit-code -- src/aitest/contracts/schemas
   ```

   > ⚠️ CI 目前在 `develop` 上**不会触发**（`ci.yml` 曾误写 `devlop`；B 已提 `fix/ci-develop-branch-typo`）。
   > **在该修复合并前，这四条命令是唯一的质量门，一条都不能省。**

4. 更新 `docs/修改日志/feix-a/日期-主题.md`。
5. 涉及跨包字段：先更新 `docs/接口对接/` 下的文档，**不偷偷改 A/C/D 的内部模型**。

## 5 给自己（AI）的提问模板

组长第 8 条：不要直接把任务截图让 AI"看着干"。使用下面格式：

```text
【任务】<一句话>
【依据】<架构/功能/需求分册 + 具体章节>
【主责】B 包，对应 P1-FR0X
【输入】<已发布/冻结的对象>
【输出】<产物>
【必须满足的不变量】<逐条列出，取自本文件第 3 节>
【异常分支】<阻塞、缺口、冲突时怎么办>
【不要做】<明确排除他人范围>
【请先做】先给出领域不变量清单与数据模型草案，我确认后再写代码
```

最后一行是关键：**先出方案、人工确认、再写代码**，避免"代码改了但自己看不懂"。

## 6 交付说明必须分四类

（根 `AGENTS.md` 第 5.5 条）

**已实现** / **已通过静态·单元·合同检查** / **已通过真实环境验收** / **未验证**

文档一致、包可构建、夹具通过，**都不能写成真实环境验收通过**。
