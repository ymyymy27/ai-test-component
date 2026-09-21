import { build } from "esbuild";
import { copyFile, mkdir } from "node:fs/promises";
// The shared panel must be built first. Never keep a second panel source in the adapter.
await mkdir("dist", { recursive: true });
await copyFile("../../src/aitest/resources/panel/dist/index.js", "dist/panel.js");
await build({ entryPoints: ["src/extension.ts"], bundle: true, platform: "node",
  format: "cjs", external: ["vscode"], outfile: "dist/extension.js" });
