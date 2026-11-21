import { test, after } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdtemp, mkdir, readFile, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

// 产品入口以 CJS 打包（dist/extension.js）；这里直接验证 CJS 产物可生成、可加载，
// 并覆盖 runtime 在 CJS 下的默认 wheelDir 分支。
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const extensionRoot = join(root, "integrations", "trae");
const base = join(root, ".runtime/tmp");
await mkdir(base, { recursive: true });
const fixture = await mkdtemp(join(base, "build-unit-"));

after(async () => {
  assert.ok(fixture.startsWith(join(base, "build-unit-")));
  await rm(fixture, { recursive: true, force: true });
});

test("the extension bundles to a CJS entry that keeps vscode external", async () => {
  const outfile = join(fixture, "extension.cjs");
  await build({
    entryPoints: [join(extensionRoot, "src", "extension.ts")],
    bundle: true,
    platform: "node",
    format: "cjs",
    external: ["vscode"],
    outfile,
  });
  assert.equal(existsSync(outfile), true);
  const text = await readFile(outfile, "utf8");
  assert.match(text, /require\("vscode"\)/);
  assert.match(text, /COMMAND_NOT_ALLOWED/);
  assert.equal(text.includes("export {"), false);
});

test("the CJS runtime has a default wheel directory and reports it missing before downloading", async () => {
  const outfile = join(fixture, "runtime.cjs");
  await build({
    entryPoints: [join(extensionRoot, "src", "runtime.ts")],
    bundle: true,
    platform: "node",
    format: "cjs",
    outfile,
  });
  const runtime = createRequire(pathToFileURL(outfile).href)("./runtime.cjs");
  const home = join(fixture, "default-wheel-home");
  let downloaded = 0;
  const result = await runtime.ensureRuntime(
    {},
    {
      runtimeHome: home,
      platform: "win32",
      spawn: () => {
        const { EventEmitter } = createRequire(import.meta.url)("node:events");
        const child = new EventEmitter();
        child.stdout = new EventEmitter();
        child.stdout.setEncoding = () => {};
        child.stderr = new EventEmitter();
        child.stderr.setEncoding = () => {};
        child.kill = () => true;
        setImmediate(() => child.emit("close", 1));
        return child;
      },
      resolveAsset: async () => ({
        version: "3.13.7+20250818",
        url: "https://example.invalid/python.tar.gz",
        sha256: "a".repeat(64),
      }),
      download: async () => {
        downloaded += 1;
        return { sha256: "a".repeat(64), bytes: 1 };
      },
    },
  );
  // 默认 wheelDir 是 <bundle>/../media/runtime；测试夹具里不存在，必须立即失败且不下载。
  assert.equal(result.ok, false);
  assert.equal(result.failure.code, "WHEEL_MISSING");
  assert.equal(downloaded, 0);
});
