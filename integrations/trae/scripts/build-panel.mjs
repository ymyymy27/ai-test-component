import { cp, mkdir, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// 只构建共享面板并复制到 media/panel；不打包扩展。
const scriptDir = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDir, "..");
const repoRoot = resolve(extensionRoot, "..", "..");
const panelRoot = join(repoRoot, "panel");
const panelOut = join(extensionRoot, "media", "panel");

const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const panelBuild = spawnSync(npm, ["run", "build"], {
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
await cp(join(panelRoot, "dist"), panelOut, { recursive: true });
