# 0.4.0 骨架验证记录

日期：2026-09-22。环境：Windows 11 x64 10.0.26100、Python 3.13.15、Node.js 24.15.0。

| 检查 | 实测结果 |
| --- | --- |
| Python单元/合同/架构/底层故障测试 | 29 passed，原始JUnit见pytest.xml |
| Ruff | All checks passed |
| mypy strict | 无错误 |
| 共享面板TypeScript及esbuild | 构建成功 |
| Trae候选扩展TypeScript及VSIX | 构建和打包成功，未做宿主兼容验收 |
| Playwright + 本机Edge headless | 1 passed；360px导航/键盘焦点/无横向溢出/无页面异常 |
| Python sdist→wheel | 构建成功 |
| 独立venv安装wheel | templates/doctor/relay退出码和结果符合骨架合同，见installed-wheel.json |
| wheel与VSIX面板一致性 | 三方实际字节相同；六模板与Schema完整，见artifacts.json |

制品SHA-256对应本机打包结果，不表示真实Trae安装成功。CI已配置Windows/Linux构建检查；本记录不预报远程CI结果。
完整事务中断恢复、业务执行、权限管道及35项产品AC未实现/未测试，不由上述测试替代。

复现命令见根README。使用本机Edge运行面板测试时设置AITEST_BROWSER_CHANNEL=msedge；默认CI安装Chromium。
