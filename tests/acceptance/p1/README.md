# 一期验收登记（P1-AC01—P1-AC35）

本目录只做一件事：**登记一期 35 项验收场景要跑什么、缺什么、谁负责、证据放哪**。

- `status.json`：机器可读登记。字段与取值见 `docs/文档-feix-a/B包/15-验收框架设计说明.md`。
- 本文件：人读前置条件清单，重点是 B 牵头的 8 项。

**本目录不产生验收结论。** `result = verified` 必须同时满足证据位置非空且真实环境已验证；文档一致、单元与合同测试通过、夹具通过，都不能写成真实环境验收通过（根 `AGENTS.md` 第 5.5 条）。

**对账**：`tests/unit/test_acceptance_status.py` 锁定"场景、预期、关联功能与需求文档第 5 节逐字一致""牵头包与实施方案第 5 节一致"，需求文档改了而这里没同步会直接失败。

**更新边界**：非 B 牵头的条目由各自牵头包填写；场景与预期任何人不得改写，只在需求文档改动后同步。

---

## 1 全局事实（写于 2026-09-28，`develop` = `eaa315f`）

| 项 | 实测结果 |
| --- | --- |
| `application/ports.py` 中 B 需要的端口 | `WorkspaceUnitOfWork`、`RecordRepository`、`SourceSnapshotPort`、`ModelProvider`、`ProjectionPort`、`SecretPort` **均只有文档字符串，无方法签名** |
| `infrastructure/file_store/` | `unit_of_work.py`／`records.py`／`workspace.py` 仍是占位 |
| `domain/review/`、`application/review/` | 仍是占位；`develop` 上没有 D 的提交 |
| 目标环境 | Windows 11 x64、Python 3.13、选定 Trae 版本尚未确定 |

因此 B 牵头 8 项**没有一项可以填 `verified`**：每项都至少需要真实存储、真实执行事实或真实入口之一。

---

## 2 B 牵头 8 项的前置条件清单

每项的 `result`／`blockers` 以 `status.json` 为准，下表只给"要跑什么、缺什么"。

### P1-AC01 交付缺少运行说明和版本 → 提示必填、不推进为可验收

| 需要的事实 | 现状 |
| --- | --- |
| 真实项目（Git／plain 各一）提交缺运行说明与版本 | 未准备 |
| 交付说明用例经 `WorkspaceUnitOfWork` 落盘 | 缺（A 端口无签名） |
| 入口侧提示与阻止推进的效果 | 缺（D 未交付） |
| 已有可复用检查 | `tests/unit/test_deliveries.py`、`tests/unit/test_project_context_usecases.py` |

**缺什么**：真实落盘 + 真实入口。当前校验只存在用例层，无法证明产品路径上"不推进为可验收"。

### P1-AC03 只有一个模块 → 适用检查可用、跨模块显示不适用并说明原因

| 需要的事实 | 现状 |
| --- | --- |
| 只含一个模块的真实项目 | 未准备 |
| 该项目适用检查的实际执行结果 | 缺（C 的 L1／L2） |
| 跨模块传播显示"不适用 + 原因" | 纯规则已具备，未见真实报告 |
| 已有可复用检查 | `tests/unit/test_modules_and_dependencies.py` |

**缺什么**：真实落盘 + 真实执行事实。

### P1-AC12 规则或代码测试后变化 → 旧记录留原版本、新执行绑新版本

| 需要的事实 | 现状 |
| --- | --- |
| 一次已发布规则／计划修订及其后的真实变化 | 未准备 |
| 旧记录仍读得到原版本 | 缺（A 的不可变历史与修订读回） |
| 新执行绑定新版本 | 缺（C 的真实执行） |
| 已有可复用检查 | `tests/unit/test_rules.py`、`tests/unit/test_publish_orchestration.py`、`tests/unit/test_draft_generation.py` |

**缺什么**：A 的不可变历史 + C 的绑定事实。

### P1-AC17 同一模板处理两个项目、其一上下文缺失 → 草稿结构一致、缺口阻塞

| 需要的事实 | 现状 |
| --- | --- |
| 两个真实项目（完整／缺上下文） | 未准备 |
| 同模板生成的两份草稿可对比 | 纯规则具备；草稿不落盘 |
| 草稿来源标注 | 已实现（`GeneratedContent.revision_context`） |
| 未确认草稿不进入执行 | 缺入口（D） |
| 已有可复用检查 | `tests/contracts/test_template_content.py`、`tests/unit/test_draft_generation.py` |

**缺什么**：A 端口 + 按已裁定分工补齐 `SourceSnapshot` 字段与真实适配（B-Q01／C-Q08）+ D 的入口。
**注意**：`git` 形态的源码身份分工已裁定但尚未实现，`plain` 形态只有最小内容身份。

### P1-AC20 运行中修改用例、反向切驱动、依据失效 → 修订只影响未执行步骤

| 需要的事实 | 现状 |
| --- | --- |
| 一次执行到一半的真实运行 | **缺**（无真实运行） |
| 正在执行步骤的拒绝修改 | 缺（C 的步骤执行事实） |
| 运行中修订序列与基础计划修订序列都可查 | 缺（A 的记录修订） |
| 依据失效时暂停并列出需重跑用例 | 缺 |
| 已有可复用检查 | `tests/unit/test_phase_one_rules.py`、`tests/unit/test_plans.py` |

**缺什么**：该项**必须在一半已执行的运行上判定**，无法提前构造输入，故记为 `blocked` 而不是"已具备未跑"。

### P1-AC30 新建纯函数／HTTP 项目、无模型凭据、无模板 → 最小输入、人工建计划、固定选定内容

| 需要的事实 | 现状 |
| --- | --- |
| 两个真实新项目（纯函数／HTTP） | 未准备 |
| 无模型时人工建计划 | 已具备（`tests/unit/test_prepare_run_flow.py`） |
| 显式分析在发布前固定选定内容 | 缺真实源码读取（`SourceSnapshotPort` 无签名） |
| 分析后源码变化提示依据变化 | 只有清单摘要规则，无真实字节 |
| 已有可复用检查 | `tests/unit/test_source_manifest.py`、`tests/unit/test_model_outbound_policy.py` |

**缺什么**：按已裁定分工实现完整 `SourceSnapshot` + `SourceSnapshotPort` + D 的分析入口。

### P1-AC31 两个验收范围、集中逐项授权、MCP 伪造确认 → 每动作独立确认、原授权可失效

| 需要的事实 | 现状 |
| --- | --- |
| 两个独立验收范围 + 模板漏项注入 | 纯规则具备（`tests/unit/test_phase_one_rules.py`） |
| 启动前集中逐项授权与变化后失效 | 缺确认记录载体 |
| MCP 伪造确认／挑战错配／重复确认三条反例 | 缺（D 的 `agent_relay`） |
| 每个副作用动作的独立确认记录 | 缺（C 的动作 + A 的落盘） |

**缺什么**：D 的入口与确认记录、A 的落盘、C 的副作用动作。B 只拥有档位／范围与授权边界规则。

### P1-AC32 模型首次调用、关闭 AI、改接收地址／材料类别 → 只发选定脱敏材料、改动重新确认

| 需要的事实 | 现状 |
| --- | --- |
| 真实模型首次调用 | 缺（目前全经 `tests/support/memory_model.py`） |
| 真实脱敏字节与凭据解析 | 缺（`ProjectionPort`／`SecretPort` 无签名） |
| 关闭 AI 阻止新请求 | 纯规则与编排具备（`tests/unit/test_model_outbound_policy.py`、`tests/unit/test_model_orchestration.py`） |
| 改接收地址／材料类别后重新确认 | 纯规则具备 |

**缺什么**：A 的出站端口；另 B-Q06（模型请求是否需逐项授权）口径未裁定。

---

## 3 其他牵头包的条目

`status.json` 中非 B 牵头 27 项的 `prerequisites`／`artifact_versions`／`actual`／`evidence`／`unverified_reason`／`blockers` 均为 `null`，`result` 为 `untested`，等待各自牵头包填写。B 不代填结论。
