import { test, after } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

// 假解释器只验证发现顺序与校验分支；不下载运行时，也不启动真实 Trae。
const root = resolve("../..");
const base = join(root, ".runtime/tmp");
await mkdir(base, { recursive: true });
const fixture = await mkdtemp(join(base, "runtime-unit-"));
await build({
  entryPoints: ["src/runtime.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: join(fixture, "runtime.mjs"),
});
const { findPython, pythonCandidates, INSTALL_HINT } = await import(
  pathToFileURL(join(fixture, "runtime.mjs")).href
);

after(async () => {
  assert.ok(fixture.startsWith(join(base, "runtime-unit-")));
  await rm(fixture, { recursive: true, force: true });
});

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.stdout = new EventEmitter();
    this.stdout.setEncoding = () => {};
    this.stderr = new EventEmitter();
    this.stderr.setEncoding = () => {};
    this.killed = false;
  }
  kill() {
    this.killed = true;
    return true;
  }
}

/** responder(command, args) → {stdout, code, spawnError}。 */
function fakeSpawn(responder) {
  const calls = [];
  const spawn = (command, args, options) => {
    calls.push({ command, args, options });
    const child = new FakeChild();
    setImmediate(() => {
      const outcome = responder(command, args);
      if (outcome.spawnError) {
        const error = new Error("spawn failed");
        error.code = outcome.spawnError;
        child.emit("error", error);
        return;
      }
      if (outcome.stdout) {
        child.stdout.emit("data", outcome.stdout);
      }
      child.emit("close", outcome.code ?? 0);
    });
    return child;
  };
  return { spawn, calls };
}

function probe(version, major, minor, extra = {}) {
  return JSON.stringify({ version, major, minor, aitest: "0.1.0", ...extra });
}

test("configured interpreter wins and stops discovery", async () => {
  const { spawn, calls } = fakeSpawn(() => ({ stdout: probe("3.12.4", 3, 12) }));
  const result = await findPython(
    { pythonPath: "C:/custom/python.exe", workspacePath: join(fixture, "ws") },
    spawn,
  );
  assert.equal(result.ok, true);
  assert.equal(result.pythonPath, "C:/custom/python.exe");
  assert.deepEqual(result.pythonArgs, []);
  assert.equal(result.version, "3.12.4");
  assert.equal(result.packageVersion, "0.1.0");
  assert.equal(result.missing.length, 0);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].args.includes("-c"), true);
});

test("discovery falls through setting → venv → py -3 → python", async () => {
  const { spawn, calls } = fakeSpawn((command) => {
    if (command === "C:/missing/python.exe") {
      return { spawnError: "ENOENT" };
    }
    if (command === "py") {
      return { stdout: probe("3.10.11", 3, 10) };
    }
    if (command === "python") {
      return { stdout: probe("3.12.1", 3, 12) };
    }
    throw new Error("unexpected command " + command);
  });
  const result = await findPython(
    { pythonPath: "C:/missing/python.exe", workspacePath: join(fixture, "ws") },
    spawn,
  );
  assert.equal(result.ok, true);
  assert.equal(result.pythonPath, "python");
  assert.deepEqual(result.candidates.map((item) => item.source), [
    "setting",
    "workspace-venv",
    "py -3",
    "python",
  ]);
  assert.equal(result.candidates[0].detail.includes("ENOENT"), true);
  assert.equal(result.candidates[1].detail, "工作区 .venv 解释器不存在");
  assert.equal(result.candidates[2].detail, "Python 3.10.11 低于 3.11");
  assert.deepEqual(
    calls.map((call) => call.command),
    ["C:/missing/python.exe", "py", "python"],
  );
  assert.deepEqual(calls[1].args.slice(0, 2), ["-3", "-c"]);
  assert.equal(calls.every((call) => call.args.includes("-c")), true);
});

test("no interpreter reports both missing items and an install hint without installing", async () => {
  const { spawn, calls } = fakeSpawn(() => ({ spawnError: "ENOENT" }));
  const result = await findPython({}, spawn);
  assert.equal(result.ok, false);
  assert.equal(result.pythonPath, null);
  assert.deepEqual(result.missing, ["Python 3.11+", "aitest 包"]);
  assert.equal(result.installHint, INSTALL_HINT);
  assert.match(result.installHint, /3\.11/);
  assert.equal(calls.every((call) => call.args.includes("-c")), true);
});

test("a valid Python without aitest reports only the missing package", async () => {
  const { spawn } = fakeSpawn((command) => {
    if (command === "py") {
      return { stdout: probe("3.12.1", 3, 12, { import_error: "ModuleNotFoundError" }) };
    }
    return { stdout: probe("3.12.1", 3, 12, { import_error: "ModuleNotFoundError" }) };
  });
  const result = await findPython({}, spawn);
  assert.equal(result.ok, false);
  assert.deepEqual(result.missing, ["aitest 包"]);
  assert.equal(result.candidates.at(-1).versionOk, true);
  assert.match(result.candidates.at(-1).detail, /import aitest 失败/);
});

test("candidate list is ordered and deduplicated", () => {
  const candidates = pythonCandidates({ pythonPath: "python", workspacePath: "C:/ws" });
  assert.deepEqual(candidates.map((item) => item.source), [
    "setting",
    "workspace-venv",
    "py -3",
  ]);
  assert.equal(candidates[2].command, "py");
  assert.deepEqual(candidates[2].args, ["-3"]);
});
