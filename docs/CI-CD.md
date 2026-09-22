# CI/CD 使用说明

本仓库交付Python包和Trae候选VSIX，持续交付产物是安装包与GitHub Release草稿。没有服务器部署、PyPI上传或扩展市场发布步骤。

## 持续集成

工作流：[ci.yml](../.github/workflows/ci.yml)。

- 向`main`、`devlop`推送，或提交目标为这两个分支的PR时运行。
- Actions页面可手动运行CI；版本发布流程也复用同一套检查。
- 同一分支／PR的新提交取消旧检查；Windows、Ubuntu分别检查，单平台失败不提前取消另一平台。
- 每个平台最多25分钟；运行使用Python 3.13、Node.js 22和已锁定依赖。

检查内容：核心／面板／扩展版本一致性、Ruff、mypy、TypeScript、Playwright、Schema生成一致性、pytest、sdist和wheel构建、VSIX打包、共享面板字节一致性、独立环境安装wheel后的CLI冒烟。

独立安装仍按当前骨架合同验证：templates列出六模板，doctor返回NOT_READY，relay返回不可用且不污染stdout。这些检查不冒充已实现业务执行。

Actions产物：

| 名称 | 内容 |
| --- | --- |
| `skeleton-windows-latest`／`skeleton-ubuntu-latest` | wheel、sdist、VSIX、制品摘要记录 |
| `test-reports-windows-latest`／`test-reports-ubuntu-latest` | pytest／Playwright的JUnit结果，以及失败时可用的浏览器截图和trace |

产物保留14天。测试失败时仍上传已生成的报告；没有执行到测试阶段时可能没有报告。统一检查名为`CI gate`，两个平台全部成功才通过。可在仓库分支规则中选择它作为必需检查；本次配置不自动修改分支保护。

## 版本交付

工作流：[release.yml](../.github/workflows/release.yml)。

1. 在`main`完成版本修改；`pyproject.toml`、Python `__version__`、面板及扩展的package.json/package-lock.json必须保持一致。
2. 将代码推送到main并确认检查通过。
3. 对相应提交推送匹配版本标签，例如包版本为0.4.0时使用`v0.4.0`：

```sh
git switch main
git pull --ff-only
git tag v0.4.0
git push origin v0.4.0
```

标签必须匹配包版本，且提交必须属于远程main历史。通过后重新运行完整CI，再使用该次运行的Windows制品创建Release草稿，附wheel、sdist、VSIX、制品元数据及SHA256SUMS。不会从其他分支或旧运行提取制品。

到仓库Releases页面检查草稿后再手动发布。本任务只配置交付流程，不创建版本标签或发布正式版本。相同标签已存在Release时不会自动覆盖，重跑前需检查现有草稿，避免替换已交付制品。

## 权限与验证范围

普通CI只拥有仓库读取权限；仅生成Release草稿的任务使用`contents: write`和GitHub自带短期token，无需另配PAT。PR使用`pull_request`触发，不使用高权限的`pull_request_target`执行外部代码。

Windows／Ubuntu构建通过只是工程兼容检查，不代表二期跨系统产品验收。真实Trae安装、一期35项业务验收及完整运行恢复仍以各自证据为准。

参考：[GitHub工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)、[Actions制品](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts)。
