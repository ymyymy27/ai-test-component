import { build } from "esbuild";
import { mkdir } from "node:fs/promises";

// 扩展只打包 Node 入口；面板资源按 media/ 原样随扩展安装，不复制到 dist。
await mkdir("dist", { recursive: true });
await build({
  entryPoints: ["src/extension.ts"],
  bundle: true,
  platform: "node",
  format: "cjs",
  external: ["vscode"],
  outfile: "dist/extension.js",
});
