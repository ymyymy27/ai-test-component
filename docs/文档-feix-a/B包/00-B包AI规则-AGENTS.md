# B 包 AI 协作规则

本文件是仓库根 `AGENTS.md` 的补充，适用范围为 B 包（项目与计划，P1-FR01—FR07）负责的目录。
与根文件冲突时以根文件为准；本文件只写 B 包特有、根文件未覆盖的约束。

依据：项目协作规则第 2 条。

---

## 1 职责范围

- **功能**：P1-FR01—FR07（项目/模块/环境上下文、交付说明、变更影响与回归、模板与检查内容、规则版本、计划与用例、档位与驱动）。
- **主责分册**：`docs/项目文档/一期/架构文档/01-项目与计划.md`。
- **负责目录**：

  ```text
  src/aitest/domain/project/
  src/aitest/domain/planning/
  src/aitest/application/project/
  src/aitest/application/planning/
  src/aitest/application/ai_assistance.py
  src/aitest/resources/templates/
  src/aitest/contracts/prepared_run.py        （待新建）
  tests/contracts/fixtures/prepared_run/      （待新建）
  docs/文档-feix-a/、docs/修改日志/feix-a/
  ```

- **对外唯一业务交付**：`PreparedRun`（B 产生、C 读取、D 仅作展示）。
- **牵头验收**：P1-AC01、AC03、AC12、AC17、AC20、AC30、AC31、AC32。

## 2 禁止修改的范围

| 路径 | 所有者 | 原因 |
| --- | --- | --- |
| `src/aitest/application/ports.py` | A | 架构文档第 7 节规定其为唯一端口归属位置。新增端口需求提交至 `docs/接口对接/B-A-端口与保存需求.md` |
| `src/aitest/infrastructure/file_store/` | A | B 仅经端口保存材料，不直接读写业务文件 |
| `src/aitest/domain/execution/`、`domain/evidence/` | C | C 已在上述目录有实现（commit `d60781d`） |
| `src/aitest/application/execution/`、`application/evidence/` | C | 同上 |
| `src/aitest/domain/review/`、`application/review/` | D | 判定与报告 |
| `src/aitest/interfaces/`、`src/aitest/resources/panel/`、`integrations/trae/` | D | 入口与界面 |
| `tests/acceptance/p1/status.json` 中非 B 牵头的条目 | 各包 | 仅更新本包负责的 AC 行 |
| `main` / `develop` 分支 | 全组 | 项目协作规则第 1 条：不直接修改，合并经 PR |
| `docs/项目文档/**`（共享规范） | 全组 | 发现冲突先登记，不单方面修改（根 `AGENTS.md` 第 5.4 条） |

不得新建以下模块（`tests/architecture/test_boundaries.py` 检查其不存在）：
`interfaces/asgi_host.py`、`application/collaboration`、`domain/identity`、`domain/delivery`、
`application/diagrams.py`、`application/locations.py`。

## 3 硬性约束

下列条款为 B 包实现的硬性约束，违反任一条即判定为缺陷，不属于风格问题。

### 3.1 模板与草稿

- 模板只生成草稿；发布是独立的人工动作。
- 生成不得增加工具或执行入口；人工修订不被重新生成覆盖。
- 模板必须给出适用条件、稳定 `item_id`、必测集合、关键链路清单；空关键链路必须有 `no_critical_path_reason`。
- 模板正文不是可执行 shell（`contracts/templates.py` 以 `extra="forbid"` 强制）。

### 3.2 规则

- `RuleDraft` 未确认不生效；发布的 `RuleVersion` 不可变；新测试使用新版本，历史保留原版本。
- 导入规则保留来源与未识别扩展字段，未知执行字段不得静默启用。

### 3.3 用例与断言依据

- 三态不可合并：`missing` 阻止进入 full 必测；`present_unconfirmed` 可执行但计入未核实、不计入 `V`；`confirmed` 仍须实际证据。
- `assertion_basis_state` 是 Case 修订保存时的冻结值；`effective_assertion_basis_state` 是派生值，不回写冻结 Case。
- 依据文本变化后旧确认失效。
- 发布时必须登记独立核验方式；缺失时该用例不得计入已验证。
- 无预期结果或前置条件的用例不得发布为可直接验收。

### 3.4 计划、范围与门禁

- `T ⊆ M`；`full` 要求 `M ⊆ S` 且跳过项为 0。
- 不得删必测、弱化断言或变更适用性以规避门禁；无关新增用例不能抵消遗漏。
- `AcceptanceScope` 在计划发布前定义；运行冻结 `scope_revision`；范围变更重新发布，不追认原运行。
- 不适用项必须给出原因且不计入分母；"未执行"不等于"不适用"。

### 3.5 档位与驱动

- `ConclusionCeiling` 只由档位派生：`quick`/`on_demand` → `partial`；`full` → `passable`。
- 不得以覆盖率或驱动方式放宽结论上限。
- 驱动只允许 `planned → stepwise`；反向必须拒绝并提示新建运行。
- `quick`/`on_demand` 不派生证据等级。

### 3.6 源码与环境

- 内容身份必须来自实际字节摘要；元数据（mtime）仅作为变化提示。
- `plain` 形态完全省略 Git 字段，不使用 `None`、空值或"未知"占位。
- 工作空间位于同步盘或网络盘时拒绝建立并说明原因。
- 显式不隔离是合法取值，不等同于缺配置。

### 3.7 准备与幂等

- 按 `(project_id, client_id, prepare_request_id)` 登记；同键异输入摘要返回冲突，不覆盖。
- `intent_id` 与准备记录同一次提交；跨入口恢复使用 `intent_id`，不以 `request_id` 代替业务身份。
- 源码读取与摘要计算在短事务外完成。
- 业务顺序按提交序号判断，不使用系统时间。
- 同请求期间源码已变时返回"依据需重新准备"，不将新字节写入旧意图。

### 3.8 模型出站

- `source_snippets` 默认关闭；只发送显式选定的脱敏材料。
- 模型只输出草稿，不得直接修改断言、关闭缺陷或作出结论。
- 迟到响应标记过期，不覆盖当前人工版本。
- 模型不可用时人工路径仍可用；不在失败重试中切换供应方。
- GitHub 只读为可选能力；绿色状态不能替代业务通过。

### 3.9 架构

- `domain/` 只允许标准库，不导入 `pydantic`、`contracts`、`application`、`infrastructure`、`interfaces`。
- `application/` 不导入 `infrastructure`、`interfaces`。
- 领域对象使用 `@dataclass(frozen=True, slots=True)`，违反不变量时抛出 `ValueError`。
- 时间经 `Clock` 端口获取，不使用 `datetime.now()`。
- `contracts/schemas/*.json` 为生成物，只由 `scripts/generate_schemas.py` 产出，不手工编辑。

## 4 变更流程

1. `git checkout develop && git pull origin develop`，再切回功能分支执行 `git merge develop`（项目协作规则第 11 条）。
2. 先更新 `02-B包施工检查项清单.md` 与相关对接文档，再修改代码（项目协作规则第 3 条）。
3. 完成修改后执行以下检查：

   ```powershell
   uv run ruff check .
   uv run mypy
   uv run pytest -q
   uv run python scripts/generate_schemas.py
   git diff --exit-code -- src/aitest/contracts/schemas
   ```

   > CI 目前在 `develop` 上不触发（`ci.yml` 曾误写 `devlop`，修复分支为 `fix/ci-develop-branch-typo`）。
   > 该修复合并前，上述命令为唯一质量门。

4. 更新 `docs/修改日志/feix-a/日期-主题.md`。
5. 涉及跨包字段时，先更新 `docs/接口对接/` 下的文档；不得未经理接直接修改 A、C、D 的内部模型。

## 5 AI 协作提问模板

提交给 AI 的任务应采用统一格式，不以截图或口头描述代替需求说明（项目协作规则第 8 条）：

```text
【任务】<一句话>
【依据】<架构/功能/需求分册 + 具体章节>
【主责】B 包，对应 P1-FR0X
【输入】<已发布或已冻结的对象>
【输出】<产物>
【必须满足的不变量】<逐条列出，取自本文件第 3 节>
【异常分支】<阻塞、缺口、冲突时的处理>
【不要做】<明确排除他人范围>
【请先做】先给出领域不变量清单与数据模型草案，确认后再编写代码
```

模板末行要求先提交方案、经确认后再编写代码，以避免代码变更缺少对应设计说明。

## 6 交付说明格式

依据根 `AGENTS.md` 第 5.5 条，交付说明分四类列出：

**已实现** / **已通过静态·单元·合同检查** / **已通过真实环境验收** / **未验证**

文档一致、包可构建或夹具通过，均不得据此声称真实环境验收通过。
