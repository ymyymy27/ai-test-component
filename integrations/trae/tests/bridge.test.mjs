import { test, after } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { existsSync, readFileSync } from "node:fs";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

// 假子进程只验证桥的信封、超时与错误映射；不启动真实 Trae 或真实 CLI。
// 基于测试文件自身定位仓库根，不依赖调用者的 cwd。
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const base = join(root, ".runtime/tmp");
await mkdir(base, { recursive: true });
const fixture = await mkdtemp(join(base, "bridge-unit-"));
await build({
  entryPoints: ["src/bridge.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: join(fixture, "bridge.mjs"),
});
const { createBridge, DEFAULT_TIMEOUT_MS } = await import(
  pathToFileURL(join(fixture, "bridge.mjs")).href
);

after(async () => {
  assert.ok(fixture.startsWith(join(base, "bridge-unit-")));
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

function readInputFile(args) {
  const index = args.indexOf("--input");
  if (index < 0) {
    return null;
  }
  try {
    return JSON.parse(readFileSync(args[index + 1], "utf8"));
  } catch {
    return null;
  }
}

/** `handler(record, child)` 在监听器挂好后异步执行。 */
function fakeSpawn(handler) {
  const calls = [];
  const spawn = (command, args, options) => {
    const record = { command, args, options, input: readInputFile(args) };
    calls.push(record);
    const child = new FakeChild();
    record.child = child;
    setImmediate(() => handler(record, child));
    return child;
  };
  return { spawn, calls };
}

function config(overrides = {}) {
  return { pythonPath: "python", workspacePath: "C:/data/ws", ...overrides };
}

function respond(body, code = 0) {
  return (record, child) => {
    child.stdout.emit("data", body + "\n");
    child.emit("close", code);
  };
}

test("call spawns the CLI with workspace and input file, and parses one JSON line", async () => {
  const { spawn, calls } = fakeSpawn((record, child) => {
    child.stdout.emit(
      "data",
      "starting\n" + JSON.stringify({ ok: true, request_id: record.input.request_id, data: { projects: [] } }) + "\n",
    );
    child.emit("close", 0);
  });
  const bridge = createBridge(config({ pythonPath: "C:/python.exe", pythonArgs: ["-3"] }), spawn);
  const result = await bridge.call("project.list", { limit: 5 });

  assert.equal(result.ok, true);
  assert.deepEqual(result.data, { projects: [] });
  assert.equal(calls.length, 1);
  const record = calls[0];
  assert.equal(record.command, "C:/python.exe");
  assert.deepEqual(record.args.slice(0, 4), ["-3", "-m", "aitest.cli", "project.list"]);
  assert.equal(record.args[record.args.indexOf("--workspace") + 1], "C:/data/ws");
  assert.equal(record.input.command, "project.list");
  assert.deepEqual(record.input.payload, { limit: 5 });
  assert.equal(typeof record.input.request_id, "string");
  assert.equal(record.args.includes("--confirm"), false);
  assert.equal(existsSync(record.args[record.args.indexOf("--input") + 1]), false);
});

test("confirm is only attached when the caller asks for it", async () => {
  const { spawn, calls } = fakeSpawn(respond(JSON.stringify({ ok: true, data: {} })));
  const bridge = createBridge(config(), spawn);
  await bridge.call("run.start", { plan_id: "plan_1" }, { confirm: true });
  await bridge.call("run.start", { plan_id: "plan_1" });
  assert.equal(calls[0].args.includes("--confirm"), true);
  assert.equal(calls[1].args.includes("--confirm"), false);
});

test("business refusal keeps the CLI error and exit code", async () => {
  const envelope = {
    ok: false,
    error: { code: "CONFIRMATION_REQUIRED", message: "缺少执行前确认", next_step: "在面板确认" },
  };
  const { spawn } = fakeSpawn(respond(JSON.stringify(envelope), 1));
  const bridge = createBridge(config(), spawn);
  const result = await bridge.call("run.start", { plan_id: "plan_1" });
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "CONFIRMATION_REQUIRED");
  assert.equal(result.error.next_step, "在面板确认");
  assert.equal(result.exitCode, 1);
});

test("non-zero exit without JSON is structured, not success", async () => {
  const { spawn } = fakeSpawn(respond("Traceback (most recent call last):", 2));
  const bridge = createBridge(config(), spawn);
  const result = await bridge.call("doctor", {});
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "CLI_EXIT_NONZERO");
  assert.equal(result.exitCode, 2);
});

test("invalid JSON with a zero exit is a structured error", async () => {
  const { spawn } = fakeSpawn(respond("not json"));
  const bridge = createBridge(config(), spawn);
  const result = await bridge.call("doctor", {});
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "CLI_INVALID_JSON");
});

test("missing ok field and mismatched request_id are rejected", async () => {
  for (const body of [
    JSON.stringify({ data: {} }),
    JSON.stringify({ ok: true, request_id: "other-request", data: {} }),
    JSON.stringify({ ok: false, error: { message: "no code" } }),
  ]) {
    const { spawn } = fakeSpawn(respond(body));
    const result = await createBridge(config(), spawn).call("doctor", {});
    assert.equal(result.ok, false);
    assert.equal(result.error.code, "CLI_INVALID_JSON");
  }
});

test("timeout kills the child and never becomes success", async () => {
  const { spawn, calls } = fakeSpawn(() => {});
  const bridge = createBridge(config({ timeoutMs: 30 }), spawn);
  const result = await bridge.call("run.start", { plan_id: "plan_1" }, { confirm: true });
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "CLI_TIMEOUT");
  assert.equal(calls[0].child.killed, true);
});

test("spawn failure carries only a stable OS code", async () => {
  const { spawn } = fakeSpawn((record, child) => {
    const error = new Error("missing");
    error.code = "ENOENT";
    child.emit("error", error);
  });
  const bridge = createBridge(
    config({ pythonPath: "C:/private-credential-marker/python.exe" }),
    spawn,
  );
  const result = await bridge.call("doctor", {});
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "CLI_SPAWN_FAILED");
  assert.match(result.error.message, /ENOENT/);
  assert.equal(JSON.stringify(result).includes("private-credential-marker"), false);
});

test("the default timeout is well above the CLI lock wait", () => {
  assert.ok(DEFAULT_TIMEOUT_MS >= 90_000, `默认超时过短：${DEFAULT_TIMEOUT_MS}`);
});

test("spawn runs in a safe cwd with Python path overrides removed", async () => {
  const { spawn, calls } = fakeSpawn(respond(JSON.stringify({ ok: true, data: {} })));
  const bridge = createBridge(config(), spawn);
  await bridge.call("doctor", {});
  const options = calls[0].options;
  assert.equal(typeof options.cwd, "string");
  assert.equal((options.cwd ?? "").includes("aitest"), false);
  assert.equal(options.env.PYTHONPATH, undefined);
  assert.equal(options.env.PYTHONHOME, undefined);
  assert.equal(options.env.PYTHONSTARTUP, undefined);
  assert.equal(options.env.PYTHONSAFEPATH, "1");
});

test("the last JSON line is the envelope, not an earlier log line", async () => {
  const { spawn } = fakeSpawn((record, child) => {
    child.stdout.emit("data", JSON.stringify({ ok: false, error: { code: "LOG", message: "log" } }) + "\n");
    child.stdout.emit(
      "data",
      JSON.stringify({ ok: true, request_id: record.input.request_id, data: { value: 1 } }) + "\n",
    );
    child.emit("close", 0);
  });
  const result = await createBridge(config(), spawn).call("doctor", {});
  assert.equal(result.ok, true);
  assert.deepEqual(result.data, { value: 1 });
});

test("a failure envelope with a foreign request_id is rejected", async () => {
  const envelope = { ok: false, request_id: "foreign", error: { code: "BUSY", message: "x" } };
  const { spawn } = fakeSpawn(respond(JSON.stringify(envelope), 1));
  const result = await createBridge(config(), spawn).call("doctor", {});
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "CLI_INVALID_JSON");
});

test("serialization and payload size failures are separated from disk errors", async () => {
  const { spawn, calls } = fakeSpawn(respond(JSON.stringify({ ok: true, data: {} })));
  const bridge = createBridge(config(), spawn);
  const cyclic = { name: "x" };
  cyclic.self = cyclic;
  const serialization = await bridge.call("doctor", cyclic);
  assert.equal(serialization.error.code, "CLI_SERIALIZE_FAILED");

  const oversized = await bridge.call("doctor", { text: "x".repeat(2 * 1024 * 1024 + 10) });
  assert.equal(oversized.error.code, "CLI_PAYLOAD_TOO_LARGE");
  assert.equal(calls.length, 0);
});

test("aborting the call kills the child and returns CLI_CANCELLED", async () => {
  const { spawn, calls } = fakeSpawn(() => {});
  const bridge = createBridge(config(), spawn);
  const controller = new AbortController();
  const pending = bridge.call("run.repair", { run_id: "r" }, { confirm: true, signal: controller.signal });
  await new Promise((resolve) => setTimeout(resolve, 10));
  controller.abort();
  const result = await pending;
  assert.equal(result.error.code, "CLI_CANCELLED");
  assert.equal(calls[0].child.killed, true);
});

test("known credential values are redacted before the payload reaches disk", async () => {
  process.env.AITEST_TEST_TOKEN = "super-secret-value-123";
  try {
    let input = null;
    const { spawn } = fakeSpawn((record, child) => {
      input = readFileSync(record.args[record.args.indexOf("--input") + 1], "utf8");
      child.stdout.emit("data", JSON.stringify({ ok: true, data: {} }));
      child.emit("close", 0);
    });
    await createBridge(config(), spawn).call("doctor", { note: "super-secret-value-123" });
    assert.equal(input.includes("super-secret-value-123"), false);
    assert.equal(input.includes("***"), true);
  } finally {
    delete process.env.AITEST_TEST_TOKEN;
  }
});

test("invalid command and missing workspace are rejected before spawning", async () => {
  let spawned = 0;
  const spawn = () => {
    spawned += 1;
    return new FakeChild();
  };
  const bridge = createBridge(config(), spawn);
  assert.equal((await bridge.call("Not A Command", {})).error.code, "INVALID_REQUEST");
  const noWorkspace = createBridge(config({ workspacePath: "  " }), spawn);
  assert.equal((await noWorkspace.call("doctor", {})).error.code, "WORKSPACE_NOT_CONFIGURED");
  assert.equal(spawned, 0);
});
