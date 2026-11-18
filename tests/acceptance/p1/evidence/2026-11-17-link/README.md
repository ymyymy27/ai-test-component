# P1-AC27 完整链路断言与定位（2026-11-17）

## 输入

- 一次性 CLI 工作空间与运行：项目根为临时目录，用例执行 `command: echo ok`，运行完成。
- 真实日志文件 `app.jsonl`：两行 JSONL，`trace_id=req-ac27`，节点 `log:1 → log:2`，其中 `log:2` 状态为 `failed`。
- 只读查询命令 `fake_db.py`：按请求 ID 输出一条 JSON 记录，节点 `db:1` 挂在 `log:2` 下。
- 项目配置 `link`：`log_paths` 指向该日志文件，`log_fields` 映射 `request_id/module/status/message`，`db_query` 执行只读脚本。

## 执行

```powershell
python -m aitest.cli link.import --workspace <ws> --input <request>
python -m aitest.cli link.assert --workspace <ws> --input <request>
python -m aitest.cli run.status --workspace <ws> --input <request>
```

## 结果

- `ac27-link.json`：导入 3 个节点（`log:1`、`log:2`、`db:1`），无缺口；断言列表保留 `log:2` 的 `order_1 vs missing` 失败断言。
- `ac27-assert.json`：对 `log:2` 写入 `order_1 == order_1` 断言，返回 `passed`。
- `ac27-run-status.json`：`link.location` 指向首错节点 `log:2`，上游 `log:1`，下游 `db:1`，位置解析为 `app.jsonl:2`（已知位置）。

软件：Python 3.11.9，ai-test-component 0.5.0；执行日期 2026-11-17。
