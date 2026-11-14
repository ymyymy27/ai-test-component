# P1-AC30 新建纯函数/HTTP 项目：无模型、无模板、分析先于计划

## 范围

真实核心（Windows 11 x64，0.4.10，仓库提交 `740f5cb`）上的两个新建工作空间，均未配置模型凭据、未选择模板、尚无计划时先分析源码。

| 项目 | 工作空间 | 被测目录 | 源码 |
| --- | --- | --- | --- |
| 纯函数 | `guided-b6a75fe8a90844aebb2da51724732047`（workspace `56b670c0-…`） | `%TEMP%\aitest-ac30-pure` | `calc.py`（`add`/`divide`/`clamp`）+ `tests/test_calc.py` |
| HTTP | `guided-056d4deb55ea42babd6771d6ab78dd80`（workspace `b37377a3-…`） | `%TEMP%\aitest-ac30-http` | `service.py`（`import aiohttp`、`handle_total`、`route_table`） |

两个项目的环境均为 `setup` 生成的独立 Python 3.11 标准库环境；`model_outbound_policy` 查询两次都返回空列表，确认没有模型凭据或出站策略。

## 实际结果

### 1. 最小输入按所选检查确定，纯函数不索要数据库/平台账号

- `analyze_project` 的输入只有 `binding_id`、`purpose`、`source_scope`、`selected_paths`、`exclusion_rules`、`include_understanding`；没有数据库、平台账号或远端连接字段。
- 纯函数项目分析结果：`function_count=3`、`intent_count=3`、`capability_gap_count=0`、`unknown_count=0`；`project_signals.capabilities=["library"]`。
- 直接以这四项检查（语法/正常/边界/异常）建计划并发布成功，全程未要求数据库或平台凭据。

### 2. 无模型仍可人工建计划

- 未保存任何 `model_outbound_policy`（查询结果 `items=[]`），未调用模型。
- 手工四项检查生成 4 个 Case、4 次依据确认，发布计划 `plan-guided-b6a75fe8a90844aebb2da51724732047` 修订 1：`run_tier=on_demand`、`initial_driver=stepwise`、`template_versions=[]`。
- 发布回执 `published=true`、`revision=1`、`confirmation_id=69`。

### 3. 显式分析可在发布前固定选定内容，草稿不自动执行

- 分析固定快照：`snapshot_id=source-9fdcba54…`、`content_identity=sha256:c79e5e37…`、`pinned_snapshot_id=snap-85f576c04e0d89d9`、`record_revision=1`、`understanding_id=und-a64978bd75989b14` 修订 1；选定范围 `source_scope="."`、`selected_paths=["."]`。
- 先保存计划草稿 `plan-ac30-draft` 修订 1 后查询 `prepared_run`：`items=[]`（commit 31）。
- 发布后再次查询 `prepared_run`：`items=[]`（commit 69）。草稿与发布都没有自动准备或执行运行。

### 4. 不支持不冒充不适用

HTTP 项目分析返回确定性能力缺口，而不是把网页检查标成“不适用”：

```json
{
  "gap_id": "gap-ebf582cf42b2e67e",
  "scope": "service.py",
  "missing_capability": "scan:aiohttp",
  "capability_revision": "python-ast-scanner/1",
  "reason": "当前确定性扫描器不识别 aiohttp 的入口声明",
  "workaround": "人工补充业务功能清单",
  "blocks_full": true,
  "source_refs": ["service.py"]
}
```

`capability_gap_count=1`、`unknown_count=0`；检测到的函数 `route_table`/`handle_total` 的 `trigger` 记为 `library`（扫描器没有把 aiohttp 路由伪装成 HTTP 入口）。该缺口 `blocks_full=true`，要求人工补充清单，未被当作不适用放行。

### 5. 分析后源码变化提示依据变化

- 修改前 `check_source`：`changes.state="unchanged"`。
- 把 `calc.py` 的 `add()` 改为 `return left + right + 0.0` 后再次 `check_source`：`changes.state="changed"`、`modified=["calc.py"]`、`binding_state="unchanged"`。
- 重新分析：新 `snapshot_id=source-206a3d2b…`、`content_identity=sha256:0f1f9b78…`（原 `sha256:c79e5e37…`），`source_current_ref.record_revision=2`，`understanding_revision=2`。依据变化在核心读取动作与理解修订上可见。

## 边界与未验证

- **依据变化只有核心读取动作与理解修订，没有发布前的产品级提示**：面板源码未调用 `check_source`，也没有“旧草稿已过期/需重新生成”的提示；本轮不能据此写“界面提示依据变化”已完成。
- 由 CLI/`CoreClient` + 核心面板确认驱动，未在真实 Trae 面板点击；未做面板截图。
- 两个项目都只做分析、计划与草稿/发布检查，没有执行测试或生成报告（AC30 不要求）。
- HTTP 项目的 `aiohttp` 路由未被识别是扫描器能力缺口，不是产品“不适用的网页检查”；没有用该 HTTP 项目建计划。

## 结论

第 1—4 项有真实核心回执；第 5 项只有 `check_source` 事实与理解修订递增，缺少发布前的产品级“依据变化”提示。**P1-AC30 保持 `not_verified`**，不升级为 `verified`。
