import { test, after } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { tmpdir } from "node:os";
import { build } from "esbuild";

// 进程层：环境清洗、安全 cwd、真实 spawn 与终止回退。不启动 Trae，也不依赖业务代码。
// 基于测试文件自身定位仓库根，不依赖调用者的 cwd。
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const base = join(root, ".runtime/tmp");
await mkdir(base, { recursive: true });
const fixture = await mkdtemp(join(base, "process-unit-"));
await build({
  entryPoints: ["src/process.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: join(fixture, "process.mjs"),
});
const { cleanEnvironment, safeWorkingDirectory, spawnProcess, spawnFailureCode, stopChild } =
  await import(pathToFileURL(join(fixture, "process.mjs")).href);

after(async () => {
  assert.ok(fixture.startsWith(join(base, "process-unit-")));
  await rm(fixture, { recursive: true, force: true });
});

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.stdout = new EventEmitter();
    this.stderr = new EventEmitter();
    this.killed = false;
  }
  kill() {
    this.killed = true;
    return true;
  }
}

test("the child environment drops inherited Python path and user-site settings", () => {
  process.env.PYTHONPATH = "C:/untrusted/repo";
  process.env.PYTHONHOME = "C:/other/python";
  process.env.PYTHONSTARTUP = "C:/startup.py";
  process.env.PYTHONUSERBASE = "C:/userbase";
  try {
    const env = cleanEnvironment();
    assert.equal(env.PYTHONPATH, undefined);
    assert.equal(env.PYTHONHOME, undefined);
    assert.equal(env.PYTHONSTARTUP, undefined);
    assert.equal(env.PYTHONUSERBASE, undefined);
    assert.equal(env.PYTHONSAFEPATH, "1");
    assert.equal(env.PYTHONNOUSERSITE, "1");
    assert.equal(env.PYTHONUTF8, "1");
    // 不修改宿主进程自身的环境。
    assert.equal(process.env.PYTHONPATH, "C:/untrusted/repo");
  } finally {
    delete process.env.PYTHONPATH;
    delete process.env.PYTHONHOME;
    delete process.env.PYTHONSTARTUP;
    delete process.env.PYTHONUSERBASE;
  }
});

test("the safe working directory is the OS temp directory", () => {
  assert.equal(safeWorkingDirectory(), tmpdir());
});

test("stopChild falls back to kill when the child has no pid", () => {
  const child = new FakeChild();
  stopChild(child);
  assert.equal(child.killed, true);
});

test("stopChild does not throw when kill fails", () => {
  const child = new FakeChild();
  child.kill = () => {
    throw new Error("already gone");
  };
  assert.doesNotThrow(() => stopChild(child));
});

test("spawnProcess runs a real child with the supplied cwd and environment", async () => {
  const output = await new Promise((resolvePromise, reject) => {
    const child = spawnProcess(process.execPath, ["-e", "process.stdout.write(process.cwd())"], {
      stdio: ["ignore", "pipe", "ignore"],
      cwd: tmpdir(),
      env: { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot },
    });
    let text = "";
    child.stdout?.setEncoding("utf8");
    child.stdout?.on("data", (chunk) => {
      text += chunk;
    });
    child.on("error", reject);
    child.on("close", () => resolvePromise(text));
  });
  assert.equal(output.toLowerCase(), tmpdir().toLowerCase());
});

test("spawnFailureCode only exposes the stable OS code", () => {
  assert.equal(spawnFailureCode({ code: "ENOENT", message: "C:/secret/path" }), "ENOENT");
  assert.equal(spawnFailureCode({ message: "no code" }), "UNKNOWN");
});
