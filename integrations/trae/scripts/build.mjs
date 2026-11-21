import { cp, mkdir, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

// 共享面板在仓库根 panel/；先构建它，再把产物复制到 media/panel 随扩展安装。
const scriptDir = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDir, "..");
const repoRoot = resolve(extensionRoot, "..", "..");
const panelRoot = join(repoRoot, "panel");
const panelDist = join(panelRoot, "dist");
const panelOut = join(extensionRoot, "media", "panel");

const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const panelBuild = spawnSync(npm, ["run", "build:extension"], {
  cwd: panelRoot,
  stdio: "inherit",
  shell: process.platform === "win32",
});
if (panelBuild.error) {
  console.error(`无法启动面板构建：${panelBuild.error.message}`);
  process.exit(1);
}
if (panelBuild.status !== 0) {
  console.error(`面板构建失败（退出码 ${panelBuild.status}）；未更新 media/panel。`);
  process.exit(panelBuild.status ?? 1);
}

await rm(panelOut, { recursive: true, force: true });
await mkdir(panelOut, { recursive: true });
await cp(panelDist, panelOut, { recursive: true });

// 扩展只打包 Node 入口；面板资源按 media/panel 原样随扩展安装。
// 所有路径都基于 extensionRoot：从仓库根或任意目录执行都不会写错位置。
await mkdir(join(extensionRoot, "dist"), { recursive: true });
await build({
  entryPoints: [join(extensionRoot, "src", "extension.ts")],
  bundle: true,
  platform: "node",
  format: "cjs",
  external: ["vscode"],
  outfile: join(extensionRoot, "dist", "extension.js"),
});
