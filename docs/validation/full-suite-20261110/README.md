# 全量 Python 测试记录（2026-11-10）

## 命令

```bash
.venv/Scripts/python.exe -m pytest -q --import-mode=importlib -n 8 --dist loadfile
```

`--import-mode=importlib` 用于规避 `tests/contracts` 与 `tests/unit` 同名测试模块的收集冲突。

## 结果

- 收集：**6104** 个节点。
- 首轮：**41 failed**，其余通过/跳过。
- 已更新 35 项过期夹具/契约：
  - `publish_plan` 的 `full` 现在必须带覆盖契约和受控发布证明；普通发布机制测试改用 `on_demand`。
  - 单次完成尝试的步骤状态按新语义断言为 `pending_verification`。
  - 面板 `PLAIN_WRITE_ACTIONS` 移到 `onboarding.ts` 并与扩展白名单逐项一致。
  - 初始准备的请求/登记改用唯一标识，避免夹具复用固定 `prepare_request_id` 产生冲突。
- **剩余 6 项失败**，未改为通过：

### 1. 源码固定字节闭包（4 项）

`tests/unit/test_pinned_source_closure.py`：

- `test_current_manifest_rechecks_its_referenced_fixed_bytes`
- `test_new_commit_digest_list_includes_pinned_manifest_and_blobs`
- `test_new_source_file_marker_cannot_omit_a_fixed_blob`
- `test_legacy_v2_without_source_file_list_is_readable_but_cannot_certify_lost_bytes`

现象：当前提交清单没有带出 `source_material_files`/固定 blob 摘要；删除 blob 后 `read_current(verify_material=True)` 不报错，新清单允许 `source_material_files=[]`。这属于**源码固定字节闭包**的存储/校验缺口，不是面板问题。

### 2. 授权消费者（2 项）

`tests/unit/test_authorization_current_action.py::test_saved_step_revision_change_cannot_consume_prepared_challenge`
  - 投影后的运行不含授权动作指向的步骤（`StopIteration`），需核对准备投影与动作步骤身份。

`tests/unit/test_unused_authorization_origin_consumption.py::test_actual_runtime_revision_requires_consumer_and_rolls_back_revocation_with_snapshot`
  - 预期“未使用授权消费者”抛错但未抛，需核对授权消费者门禁。

## 其它验证

- 面板 Playwright：31/31 通过。
- 扩展测试：35/35 通过。
- `test_view_queries.py` 12 项、`test_completion_boundaries.py`、`test_evidence_read_view.py`、`test_onboarding.py` 通过。
- Schema 重新生成无差异。

## 边界

- 这是组件/集成层全量，不是真实 Trae、真实 Tk 原生确认、真实模型供应商、掉电恢复或其他宿主验收。
- 剩余 6 项未修复前，不能宣称“全量通过”或一期完成。
- 验收登记与 AC 状态不变。
