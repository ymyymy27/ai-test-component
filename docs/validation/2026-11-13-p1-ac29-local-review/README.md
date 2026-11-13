# P1-AC29 本地复核独立记录验证

## 范围

真实产品核心（Windows 11 x64，产品版本 0.4.10，提交 `3424753`）上的本地复核场景。执行入口为 CLI `dispatch`，人工确认走核心原生确认窗口；不是 Trae 面板点击链路。

- 工作空间：`1bd21c5b-f55a-4ffd-9f15-582e5815db74`
- 项目：`guided-935db490-d7d6-476f-ba25-60a260428424`
- 运行：`run-08b7fd9099c759c28ad59fa42a43c0cb4e2b233d26e97957088f47a183ba988d`
- 报告：`report-real-1`

## 输入与步骤

1. 报告 `report-real-1` 修订 3 已有业务结论 `failed`，当时未关联问题。
2. 对该修订记录第一次本地复核 `review-real-1`：结论 `failed_confirmed`，说明“独立复核：pkgA 两项断言失败，报告证据完整，业务失败结论成立。”
3. 对该修订记录第二次本地复核 `review-real-2`：结论 `failed_confirmed`，说明“第二次复核：失败节点与证据引用一致，复核不改变报告内容修订。”
4. 读取报告修订 3，仍可读且内容修订未变。
5. 关联问题已从 draft 流转到 closed（修订 6）；以 `expected_revision=3` 创建 `report-real-1` 新修订，得到修订 4。
6. 读取修订 4，业务结论仍为 `failed`，`issue_revisions` 含 `issue:pytest:f434ca836e25232fa22ab75a` 修订 6。
7. 对修订 3 导出证据包，`review_ids` 为两条复核，`kind=evidence_bundle`。

## 实际结果

复核记录（独立 aggregate，不写入报告正文）：

| 复核 | 绑定报告修订 | 复核人 | 结论 | 时间 |
| --- | --- | --- | --- | --- |
| `review-real-1` | 3 | native-ui 会话 | `failed_confirmed` | 2026-11-13T13:42:17Z |
| `review-real-2` | 3 | native-ui 会话 | `failed_confirmed` | 2026-11-13T13:44:46Z |

报告：

- 修订 3：`failed`，无关联问题；两次复核后仍可读取，内容修订仍为 3。
- 修订 4：`failed`，关联问题修订 6；由问题变化后生成，旧修订与复核记录保留。
- 复核没有把失败改成通过，也没有改写执行/断言事实。

导出：

- 导出记录：`export-0cbc9c57607cec290e303158bace7033a3e139843ede3eb2cd3c07ca9525fcf4`
- 制品：`objects/guided-935db490-d7d6-476f-ba25-60a260428424/932c599c525af7a7b633a2b54abac041e6d37f2c7daa8824155cca4f17be7da5`
- 摘要：`sha256:932c599c525af7a7b633a2b54abac041e6d37f2c7daa8824155cca4f17be7da5`，34240 字节，`application/zip`
- `manifest.json`：`review_ids=[review-real-1, review-real-2]`，`review_revisions={review-real-1:3, review-real-2:3}`
- `report.md` 含两条复核结论与说明。
- 对 `report.md`、`manifest.json` 小写扫描 `authorization: bearer`、`password=`、`token=`、`api_key=`、`secret_key=`、`client_secret=`：0 命中。

## 判定

AC29 的预期在本真实核心场景中成立：复核是独立记录，不改变报告内容修订；问题变化生成新报告修订；旧修订与复核可追溯；导出包含复核结论且未发现敏感模式。

## 边界

- 本次只覆盖 AC29；不据此升级其他 AC。
- 复核人来自核心原生 UI 会话；未使用 Trae 面板点击路径。
- 导出敏感扫描是模式扫描，不替代完整凭据登记表对拍。
