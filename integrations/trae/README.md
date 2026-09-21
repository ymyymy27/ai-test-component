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
