# Trae 薄扩展骨架

仅提供命令和共享面板容器，不写业务文件、不启动第二核心、不生成 MCP 配置。
采用候选 VSIX API 编译，**尚未在真实 Trae 中国版/国际版安装验证**。
编译成功不代表宿主兼容；安装、Webview、信任、目录切换和生命周期均待验证。

从仓库根目录先构建 `src/aitest/resources/panel`，再在本目录执行：

```sh
npm ci
npm run package
```

制品为 `dist/aitest-trae.vsix`。命令：`AI Test: 打开一期骨架面板`。
不提供假执行按钮；命名管道和业务用例完成后再连接核心。

未来手动 stdio 配置为 `aitest mcp-relay --binding <binding_id>`，当前该命令明确返回不可用。
不要将骨架配置登记为已可工作的 MCP 服务，也不要覆盖已有编辑器配置。

显式配置 `aitest.projectId` 和 `aitest.runId` 才能读取所选运行主屏；留空不从源码目录猜测。`aitest.workspaceRoot` 是用户数据工作空间。扩展会发送项目/运行上下文，配置变化清空旧投影并拒绝读取期间迟到的回执；`read_view` 须由 doctor 广告后使用。面板只读，不代签人工确认或执行业务写入。

本次 Node 组件测试使用明确的假 CLI/VS Code API，浏览器验证使用真实 Chromium 和模拟宿主；这些不能证明真实 Trae 兼容。完整证据与剩余产品用户流见 [本次验证](https://github.com/ymyymy27/ai-test-component/blob/fix/docs/validation/p1-completion-repair-20261013/README.md)。
