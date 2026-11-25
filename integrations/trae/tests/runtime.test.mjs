import { test, after } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

// 假解释器只验证发现顺序与校验分支；不下载运行时，也不启动真实 Trae。
// 基于测试文件自身定位仓库根，不依赖调用者的 cwd。
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
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
const { findPython, pythonCandidates, ensureRuntime, INSTALL_HINT } = await import(
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
      if (outcome.hang) {
        // 不输出、不退出：由探测超时终止。
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

test("an explicit setting stops discovery instead of silently substituting another interpreter", async () => {
  const { spawn, calls } = fakeSpawn(() => ({ spawnError: "ENOENT" }));
  const result = await findPython(
    { pythonPath: "C:/missing/python.exe", workspacePath: join(fixture, "ws") },
    spawn,
  );
  assert.equal(result.ok, false);
  // 不再尝试 .venv / py -3 / python：用户指定的环境失败就明确报错。
  assert.deepEqual(calls.map((call) => call.command), ["C:/missing/python.exe"]);
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].source, "setting");
  assert.equal(result.candidates[0].detail.includes("ENOENT"), true);
  assert.match(result.installHint, /显式设置/);
});

test("discovery without a setting falls through venv → py -3 → python", async () => {
  const { spawn, calls } = fakeSpawn((command) => {
    if (command === "py") {
      return { stdout: probe("3.10.11", 3, 10) };
    }
    if (command === "python") {
      return { stdout: probe("3.12.1", 3, 12) };
    }
    throw new Error("unexpected command " + command);
  });
  const result = await findPython({ workspacePath: join(fixture, "ws") }, spawn);
  assert.equal(result.ok, true);
  assert.equal(result.pythonPath, "python");
  assert.deepEqual(result.candidates.map((item) => item.source), [
    "workspace-venv",
    "py -3",
    "python",
  ]);
  assert.equal(result.candidates[0].detail, "工作区 .venv 解释器不存在");
  assert.equal(result.candidates[1].detail, "Python 3.10.11 低于 3.11");
  assert.deepEqual(
    calls.map((call) => call.command),
    ["py", "python"],
  );
  assert.deepEqual(calls[0].args.slice(0, 2), ["-3", "-c"]);
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

test("candidate list is ordered and deduplicated without a setting", () => {
  const candidates = pythonCandidates({ workspacePath: "C:/ws" });
  assert.deepEqual(candidates.map((item) => item.source), [
    "workspace-venv",
    "py -3",
    "python",
  ]);
  assert.equal(candidates[1].command, "py");
  assert.deepEqual(candidates[1].args, ["-3"]);
});

test("a configured interpreter is the only candidate", () => {
  const candidates = pythonCandidates({ pythonPath: "python", workspacePath: "C:/ws" });
  assert.deepEqual(candidates.map((item) => item.source), ["setting"]);
  assert.equal(candidates[0].command, "python");
});

test("an interpreter with an older aitest package is rejected with a version diagnostic", async () => {
  const { spawn } = fakeSpawn(() => ({ stdout: probe("3.12.4", 3, 12, { aitest: "0.4.10" }) }));
  const result = await findPython({ pythonPath: "C:/custom/python.exe" }, spawn, {
    minPackageVersion: "0.5.0",
  });
  assert.equal(result.ok, false);
  assert.equal(result.candidates[0].failure, "package");
  assert.match(result.candidates[0].detail, /0\.4\.10.*0\.5\.0/);
});

test("a timed-out probe is retried once and reported as a timeout, not as a missing interpreter", async () => {
  let probes = 0;
  const { spawn } = fakeSpawn(() => {
    probes += 1;
    return { hang: true };
  });
  const result = await findPython({}, spawn, { probeTimeoutMs: 20 });
  assert.equal(result.ok, false);
  assert.equal(probes, 4, "两个可执行候选各重试一次");
  assert.equal(result.candidates.every((item) => item.failure === "timeout"), true);
  assert.match(result.candidates[0].detail, /已重试/);
  assert.match(result.missing.join("、"), /探测超时/);
});

test("ensureRuntime rejects invalid explicit pythonPath without falling back to portable download", async () => {
  const { spawn, calls } = fakeSpawn(() => ({ spawnError: "ENOENT" }));
  const runtimeHome = join(fixture, "unused-runtime-home");
  const result = await ensureRuntime(
    { pythonPath: "C:/explicit-missing/python.exe", workspacePath: join(fixture, "ws") },
    { spawn, runtimeHome, wheelDir: join(fixture, "empty-wheels") },
  );
  assert.equal(result.ok, false);
  assert.equal(result.source, "existing");
  assert.equal(result.failure?.code, "EXPLICIT_PYTHON_INVALID");
  assert.deepEqual(
    calls.map((call) => call.command),
    ["C:/explicit-missing/python.exe"],
  );
});
