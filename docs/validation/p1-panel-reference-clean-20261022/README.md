# 参考图面板干净分支验证

日期：2026-10-22

分支：`D-panel-reference-clean`

基线：`develop@732e45f05a52a26493cb92607469f6204040818c`

## 目的

旧分支 `D-判定报告-郭` 从较早节点拉出，分支总历史显示 95 个提交，且落后最新 `develop` 506 个提交。该分支的 PR 相对 `develop` 虽只有 6 个独立提交，但仍存在冲突，不适合作为可合并 PR。

本分支在最新 `develop` 上重新整理：

- 不覆盖现有生产面板。
- 将参考图交互作为独立 `reference` 预览模块。
- 只新增参考图预览、构建脚本、验证测试和扩展预览命令。

## 文件

- `src/aitest/resources/panel/src/reference/replica.ts`
- `src/aitest/resources/panel/src/reference/replica-styles.ts`
- `src/aitest/resources/panel/preview/index.html`
- `src/aitest/resources/panel/preview/gallery.html`
- `src/aitest/resources/panel/preview/server.mjs`
- `src/aitest/resources/panel/tests/verify-reference.mjs`
- `integrations/trae/src/extension.ts` 的 `aitest.openReferencePreview` 命令

## 构建与验证

| 检查 | 结果 |
| --- | --- |
| 面板 TypeScript 检查 | 通过 |
| 生产面板 esbuild | 通过 |
| 参考图 esbuild | 通过 |
| 扩展 TypeScript 检查 | 通过 |
| 扩展 esbuild | 通过 |
| 10 个参考状态 DOM 测试 | 通过 |
| 菜单、活动栏、搜索、SCM、扩展、终端测试 | 通过 |
| 独立预览端口 `4174` | 返回 200 |

命令：

```powershell
cd src/aitest/resources/panel
npm run build:reference
npm run test:reference
npm run preview:reference
```

## 边界

- 参考图预览属于界面评审材料，不发送核心命令，不代表产品 AC 已通过。
- 现有生产面板仍是最新 `develop` 的实现，不被参考图模块替换。
- 真实 Trae 兼容性和真实 A/C 数据端到端仍未验证。
