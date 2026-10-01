# 2026-09-30 B 包：`SourceSnapshot` 字段口径冻结（AB-001 第 11 节）

分支：`feat/b-sourcesnapshot-fields`（原，未提 PR）；重新施加为 `feat/b-sourcesnapshot-fields-v2`
基线：`develop` = `9e1d645`（原）→ 重新施加时 = `d1b929b`
依据：项目负责人裁定 DEC-001（2026-09-30）；`docs/接口对接/README.md` 与同目录 `AGENTS.md`
对应 FR：P1-FR01（源码快照的内容身份）

> **重新施加说明（2026-10-01）**：原分支成文于 2026-09-30 但未及时提交评审，挂起到 2026-10-01。
> 重新施加时**只取 `AB-001` 第 11 节与本文件**：原分支对 `docs/接口对接/README.md`、
> `BC-001/delivery.md`、`02-B包施工检查项清单.md` 的改动已被此后合入的多个 PR 取代，
> 重新施加会造成回退，因此不取。

---

## 1 本次做了什么

按 DEC-001 的裁定（"`SourceSnapshot` 的建立时机、`purpose`、范围、排除规则、内容身份算法、
Git/plain 身份、复取范围与失效判据由 B 主责"），在 `docs/接口对接/进行中/AB-001-端口与保存/contract.md`
新增**第 11 节：`SourceSnapshot` 字段口径**，供 C 评审执行兼容性后落地。

遵守 `docs/接口对接/AGENTS.md` 第 5 节修改流程：**先改 `contract.md`，未改任何代码、Schema 或夹具**。

### 1.1 冻结内容

| 小节 | 内容 |
| --- | --- |
| 11.1 形式互斥 | 复用项目绑定 `bind_form` 的**同一模式**：`git`／`plain` 形态决定字段存在性，不适用字段**真正省略**，不用 `null`／空串占位 |
| 11.2 冻结字段表 | 17 个字段的名称、类型、必填性与判据 |
| 11.3 `content_identity` 口径 | 输入、规范字节、不参与计算的输入（mtime／绝对路径／遍历顺序／`snapshot_id`）、跨平台要求、可复现要求 |
| 11.4 `SourceSnapshotPort` 语义 | `pin`／`read_pinned`／`materialize`／`detect_changes` 四个方法的语义（签名形式由 A 冻结） |
| 11.5 待 C 确认 | 4 项：Q1 形式互斥、Q2 字段兼容、Q3 `content_identity` 迁移、Q4 与 `PreparedRun` 修订的关系 |
| 11.6 不改变的事项 | 不改项目文档、不建第二套 `SourceSnapshot`、不手工改生成物、不把"冻结字段"写成"已实现" |

### 1.2 与既有实现的关系（避免重复定义）

- `plain` 形态的身份值**沿用 B 既有的 `SourceManifest`**（`domain/project/context.py`：`source_scope`／
  `manifest_digest`／`files`／`exclusion_rules`／`refetch_dependencies`／`refetch_scope`），
  第 11 节**不新定义第二套算法**；`SourceSnapshot` 是其上游的不可变固定事实。
- `plain_manifest_digest` 直接取 `SourceManifest.manifest_digest`。
- 类位置仍唯一在 `domain/execution/sources.py`（C 的目录），不搬迁、不复制。

### 1.3 元数据与总台账同步

- `AB-001` 元数据：`contract_version` 0.5 → **0.6**；`next_owner` → **C**；`next_action` 改为
  "C 评审第 11 节（Q1—Q2）；A 冻结端口签名与本地 Git 适配；B 提交字段口径给项目负责人确认并给 Q3 迁移说明"。
- `docs/接口对接/README.md` 第 10.1 节总台账的 AB-001 行同步更新。

---

## 2 检查结果（**必须如实说明：基线本身是红的**）

本次改动**只有两个 Markdown 文件**，未触碰代码、Schema 或夹具。检查在**干净基线 `origin/develop`（`9e1d645`）**
上同样失败，因此下列失败**与本次改动无关，但会出现在任何新 PR 的 CI 上**：

| 检查 | 结果 | 基线对比 |
| --- | --- | --- |
| `uv run ruff check .` | **失败**（22 项可自动修复） | 基线同样失败 |
| `uv run mypy`（strict） | **失败**（40 errors / 6 files / 120 sources） | 基线同样失败 |
| `uv run python scripts/generate_schemas.py` + `git diff --exit-code` | **有差异**：`Event.json`、`Response.json` 未重新生成 | 基线同样有差异 |
| `uv run pytest` | 2 failed / 33 errors | 基线 2 failed / 33 errors |
| `git diff --check` | 通过 | — |

### 2.1 基线失败的具体内容（实测，供定位）

```
FAILED tests/architecture/test_boundaries.py::test_dependency_direction
  AssertionError: assert 'aitest.application.planning.substrate'
    in {'aitest.application.errors', 'aitest.application.ports'}

FAILED tests/unit/test_command_adapter.py::test_command_adapter_stops_running_process
```

**根因（实测）**：`infrastructure/` 直接 import 了 `aitest.application.planning.substrate`
（B 的薄底座模块），而 `tests/architecture/test_boundaries.py` 规定
`infrastructure` 只能依赖 `aitest.application.ports` 与 `aitest.application.errors`。

**CI 证据**：`develop` 上最近三次运行（`8d9883ce`、`65cb5a53`、`9e1d645b`）结论均为 `failure`；
最后一次 `success` 是 `83360cdc`（A 包交付之前）。即 **CI 自 2026-09-29 A 包交付起持续失败**。

**本次不修复该失败**：它属 A 包交付范围（`infrastructure/` 与 `application/planning/substrate` 均不在 B 的改动边界内），
本记录如实登记，不因为"与本次改动无关"就写成全部通过。

---

## 3 交付说明（四类）

| 类别 | 内容 |
| --- | --- |
| **已实现** | `AB-001/contract.md` 第 11 节（B 主责的 `SourceSnapshot` 冻结字段口径）、元数据与总台账同步、本日志 |
| **已通过静态·单元·合同检查** | `git diff --check` 通过；本次为文档改动，未改代码，故不产生新的代码检查结论 |
| **已通过真实环境验收** | **无**。字段仅为冻结口径，尚未落地；无真实快照固定与对拍证据 |
| **未验证** | 第 11 节的字段与 `content_identity` 口径未落地、未对拍；Q1—Q4 待 C 确认；基线的三项检查失败与架构违规未修复（非 B 范围） |

---

## 4 未执行的操作

- 未推送、未创建 PR。
- 未修改任何代码、Schema、夹具或测试。
- 未修改 `docs/项目文档/**`。
- 未移动或重命名任何文件。
- 未修改 `AB-001` 之外的合同。
