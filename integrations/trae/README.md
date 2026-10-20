# Trae / VS Code 兼容宿主适配

本目录提供共享面板的薄宿主适配，不写业务文件、不启动第二个核心、不实现判定规则。

- 侧栏视图：`aitest.panel`（Activity Bar 的「AI 测试」容器）
- 命令：`AI Test: 打开 AI 测试面板`、`AI Test: 打开参考图预览`
- 共享面板：`../../src/aitest/resources/panel/dist/index.js` 复制为 `dist/panel.js`
- 只读桥：`doctor`、`query`、`events`、`storage_usage`、`check_source`、`inspect_case_reuse`、`copy_repair_brief`
- 写动作、确认、运行、发布和导出不从 Webview 直接发起；未接入受控用户入口时按钮保持禁用

配置项：

| 配置 | 说明 |
| --- | --- |
| `aitest.pythonPath` | 已安装本包的解释器，或通过 `PYTHONPATH` 指向 `src` |
| `aitest.workspaceRoot` | 用户数据工作空间根目录，不是被打开的源码目录 |
| `aitest.projectId` | 当前稳定项目 ID；留空时不会猜测项目 |
| `aitest.demoMode` | 详情标签页显示参考图演示夹具；默认关闭，只用于界面评审 |

构建：

```powershell
cd src/aitest/resources/panel
npm ci
npm run build

cd ../../../../integrations/trae
npm ci
npm run package
```

产物：`dist/aitest-trae.vsix`。VSIX 编译和安装成功不等于真实 Trae 兼容验收；真实 Trae 版本、Webview 生命周期、信任、目录切换和核心恢复仍需登记实测证据。