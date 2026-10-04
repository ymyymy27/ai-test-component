# DEC-008：`PreparedRun` 是否冻结验收范围的 `scope_id`（用于漂移核对）

状态：**已裁定并归档；候选方案不再生效**

提出日期：2026-10-03

提出方：B 包（项目与计划）

受影响方：C（`PreparedRun` 的生产方）、D（消费与展示）

裁定方：袁（项目负责人）

裁定日期：2026-10-04

影响合同：`BC-001`（`PreparedRun` 字段）；`AB-001` 第 8.14／11 节（漂移核对链范围）

## 1. 待裁定问题

`PreparedRun` 现在只冻结 `acceptance_scope_revision`（**一个修订号**），
**没有 `scope_id`**。漂移核对链因此无法核对这条依据——要不要给它补一个标识字段？

## 2. 冲突依据（逐字引用）

| 来源 | 原文 |
| --- | --- |
| `docs/一期工程检查-B包.md` 第 3.1 节 B-04 | "虽然Case/规则/验收范围已有保存函数却未纳入核对……**读到旧修订不能替代当前来源一致**" |
| `src/aitest/contracts/prepared_run.py` 第 284 行（现行合同） | `acceptance_scope_revision: int = Field(ge=1)` —— **只有修订号** |
| `src/aitest/application/planning/prepare_run.py` 第 99 行（现行实现） | `acceptance_scope_revision: int`，其值取自 `plan.scope.revision`（**计划对象里的范围修订**） |
| `application/planning/persistence.py` 的记录约定 | `acceptance_scope` 记录的 `record_id` 是 `scope_id`、修订是**仓储修订**；读取入口是 `load_acceptance_scope(reader, project_id, scope_id, revision)` |
| 检查文档 B-04 完成条件 | "将准确已保存修订纳入完整依据核对……**读到旧修订不能替代当前来源一致**" |

## 3. 冲突点

**要核对"某个验收范围修订还在不在"，必须知道两件事：记录标识（`scope_id`）与修订号。**
`PreparedRun` 只给了后者。而且现有的那一个数字**语义还不确定**：

- 它取自 `plan.scope.revision`，那是**领域对象的修订**；
- 而记录里的修订是**仓储修订**；
- 两者在 `save_acceptance_scope` 上按 B-11 的不变量**本应相等**，
  但 `plan.scope.revision` 究竟等于哪一个，合同里没有写死。

**结果**：`drift` 现在只能把 `acceptance_scope` 列进 `uncovered`
（见 `application/planning/drift.py` 的 `UNCOVERED_SOURCES`，附原因
"只有修订号、没有 scope_id，不知道要读哪一条 scope 记录"）。
这属于**合同字段缺口**，不是"依据不存在"。

## 4. 选项与代价

### 选项甲：`PreparedRun` 增加 `acceptance_scope_id`（**B 侧倾向**）

- 做法：`SnapshotRef` 的同类做法——加一个 `acceptance_scope_id: str`（`min_length=1`），
  与既有 `acceptance_scope_revision` 一起冻结；
  `drift` 据此按 `scope_id + revision` 读记录核对。
- 代价：① **`PreparedRun` 是跨包合同**，属新增必填字段 → 提升合同版本、走完整流程，
  并同步 `BC-001`、生成 Schema、功能夹具与 `tests/contracts/**`；
  ② C 的生产侧要提供该字段（数据本来就在 C 手里：计划冻结的范围对象）；
  ③ 已产出的旧 `PreparedRun` 记录缺该字段 → 需要登记"旧记录按未覆盖处理"而不是伪造。
- 收益：`drift` 的 `uncovered` 从 2 类降到 1 类（只剩 `project_revision`），
  且 B-04 的"验收范围纳入核对"真正落地。

### 选项乙：不补字段；把 `acceptance_scope` 的 `uncovered` 明确登记为合同缺口

- 做法：维持现状，在检查项清单与 `drift` 注释里写清缺口性质；
  核对范围到此为止（模板、用例、规则、快照、绑定、环境、计划共 7 类）。
- 代价：① B-04 的"验收范围"这一项**永远无法核对**，只能靠"计划已核对"间接兜底；
  ② 若范围记录被单独改写（不经计划），漂移核对发现不了。

### 选项丙：不加 `scope_id`，改为**按项目 + 修订查找范围记录**

- 做法：`drift` 用有限查询按 `project_id` + `acceptance_scope` + `revision` 找记录。
- 代价：① 需要"按修订查到 `record_id`"的查询能力，`RecordQuery` 现在的入口是
  `record_id` 定向查询，不是"按修订反查"；② 若同一项目存在多个范围记录
  （不同 `scope_id`）且修订号相同，**无法判定该读哪一条**——歧义没有消除，只是推后。

## 5. B 侧倾向（**仅为建议，不作为结论**）

**倾向选项甲**：缺口在字段上，补字段是唯一能真正消除歧义的做法；
选项丙把歧义推给查询，选项乙放弃这类核对。

**同时需要一并确认的语义**：`acceptance_scope_revision` 到底等于
**领域对象的修订**还是**记录仓储修订**。按 B-11 的不变量两者应当相等，
但合同里没有写死；若二者可能不等，则本项除 `scope_id` 外还需要再加"按哪个修订核对"的说明。

## 6. 阻塞影响

- 不裁定则：`drift` 的验收范围核对永远缺位，B-04 该项只能以"合同缺口"结项；
- 若 `drift` 擅自按"猜一个 `scope_id`"或"按修订全项目扫"实现，
  会引入 A-15 同类问题（**范围混用**），且与"有限查询、不偷偷全扫"的存储合同冲突。

## 7. 裁定结论

- 结论：PreparedRun 增加冻结的 scope_id；按 scope_id + acceptance_scope_revision 读取范围。旧记录缺标识时要求重新准备，不按项目猜测范围。
- 判定日期：2026-10-03；确认人：袁（本次会话明确答复）。
- 现行入口：[AB-001](../../进行中/AB-001-端口与保存/contract.md)、[BC-001](../../进行中/BC-001-PreparedRun/contract.md)。
- 实现及验证：见 [ABC 修复证据清单](../../../ABC包问题修复证据清单-2026-10-03.md)；回归文件 `tests/unit/test_abc_decision_regressions.py`。此裁定不表示真实 Trae 或一期 AC 已验收。
