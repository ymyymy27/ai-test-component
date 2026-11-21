import { test, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { tmpdir } from "node:os";
import { build } from "esbuild";

// 显式 VS Code API / bridge / runtime 夹具：证明扩展接线，不启动真实 Trae。
// 这里允许运行时失败、postMessage 拒绝、多个 webview 并存，避免夹具把所有依赖都当成必然成功。
// 基于测试文件自身定位仓库根，不依赖调用者的 cwd。
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const extensionRoot = join(root, "integrations", "trae");
const base = join(root, ".runtime/tmp");
await mkdir(base, { recursive: true });
const fixture = await mkdtemp(join(base, "extension-unit-"));
const shim = join(fixture, "host.mjs");
await writeFile(
  shim,
  `
import { join } from "node:path";

export const state = {
  settings: { pythonPath: "", workspacePath: "" },
  workspaceFolders: [],
  trusted: true,
  projects: [],
  runtime: { ok: true, pythonPath: "fake-python", pythonArgs: [], version: "3.12.1",
    packageVersion: "0.5.0", candidates: [], missing: [], installHint: "fixture hint" },
  runtimeReject: 0,
  runtimeChecks: [],
  preparedPython: null,
  discoveryChecks: 0,
  commands: new Map(),
  sent: [],
  panels: [],
  views: [],
  panelOptions: null,
  bridgeCalls: [],
  bridgeConfigs: [],
  responses: [],
  openDialogResult: [],
  revealThrows: false,
  opened: [],
  openExternalResult: true,
  errors: [],
  infos: [],
  warnings: [],
  lifecycle: [],
  nextWebviewId: 1,
  bridgeDelayMs: 0,
  activeBridges: 0,
  maxActiveBridges: 0,
  holdBridge: false,
  rejectPosts: false,
};

class FakeWebview {
  constructor(kind) {
    this.id = "wv-" + state.nextWebviewId++;
    this.kind = kind;
    this._html = "";
    this._receive = null;
    this.options = {};
    this.cspSource = "vscode-resource:";
  }
  get html() { return this._html; }
  set html(value) {
    this._html = value;
    state.lifecycle.push({ id: this.id, kind: this.kind, event: "html", listener: Boolean(this._receive) });
  }
  asWebviewUri(uri) { return { toString: () => "webview:" + uri.fsPath }; }
  onDidReceiveMessage(cb) {
    this._receive = cb;
    state.lifecycle.push({ id: this.id, kind: this.kind, event: "listen" });
    return { dispose() { state.receiveDisposed = true; } };
  }
  postMessage(message) {
    state.sent.push({ webview: this.id, kind: this.kind, message });
    if (state.rejectPosts) {
      return Promise.reject(new Error("Webview is disposed"));
    }
    return Promise.resolve(true);
  }
  fire(message) {
    if (!this._receive) {
      throw new Error("webview 尚未注册消息监听");
    }
    return this._receive(message);
  }
}

function makePanel() {
  const webview = new FakeWebview("editor");
  return {
    webview,
    reveal() {
      state.revealed = (state.revealed ?? 0) + 1;
      if (state.revealThrows) {
        throw new Error("Webview is disposed");
      }
    },
    onDidDispose(cb) { this.panelDispose = cb; },
    dispose() { this.disposed = true; this.panelDispose?.(); },
  };
}

export function createView() {
  const webview = new FakeWebview("sidebar");
  const view = {
    webview,
    showCount: 0,
    show() { view.showCount += 1; },
    onDidDispose(cb) { view.viewDispose = cb; },
    dispose() { view.viewDispose?.(); },
  };
  return view;
}

export const workspace = {
  get isTrusted() { return state.trusted; },
  get workspaceFolders() { return state.workspaceFolders; },
  getConfiguration: () => ({
    get: (key, fallback) => state.settings[key] ?? fallback,
  }),
  onDidChangeConfiguration: (cb) => {
    state.configurationListener = cb;
    return { dispose() {} };
  },
  onDidChangeWorkspaceFolders: (cb) => {
    state.folderListener = cb;
    return { dispose() {} };
  },
};

export const commands = {
  registerCommand: (name, cb) => {
    state.commands.set(name, cb);
    return { dispose() {} };
  },
  executeCommand: async (...args) => { state.executed = args; },
};

export const window = {
  registerWebviewViewProvider: (id, provider, options) => {
    state.viewProvider = { id, provider, options };
    return { dispose() {} };
  },
  createWebviewPanel: (viewType, title, column, options) => {
    state.panelOptions = options;
    const panel = makePanel();
    state.panels.push(panel);
    return panel;
  },
  showWarningMessage: (text) => { state.warnings.push(text); },
  showInformationMessage: (text) => { state.infos.push(text); },
  showErrorMessage: (text) => { state.errors.push(text); },
  showOpenDialog: async () => state.openDialogResult,
  setStatusBarMessage: () => ({ dispose() {} }),
};

export const ViewColumn = { One: 1 };
export const Uri = {
  joinPath: (...parts) => {
    const fsPath = join(...parts.map((part) => (typeof part === "string" ? part : part.fsPath)));
    return { fsPath, toString: () => fsPath };
  },
  file: (fsPath) => ({ fsPath, toString: () => fsPath }),
};

export const env = {
  openExternal: async (uri) => {
    state.opened.push(uri.fsPath);
    return state.openExternalResult;
  },
};

export function createBridge(config) {
  state.bridgeConfigs.push(config);
  return {
    async call(command, payload, options) {
      state.bridgeCalls.push({ command, payload, options, config });
      if (state.holdBridge) {
        await new Promise((resolve) => {
          if (!options.signal) {
            setTimeout(resolve, 5000);
            return;
          }
          options.signal.addEventListener("abort", resolve, { once: true });
        });
        if (options.signal?.aborted) {
          return { ok: false, error: { code: "CLI_CANCELLED", message: "调用已被取消" } };
        }
      }
      if (state.bridgeDelayMs > 0) {
        state.activeBridges += 1;
        state.maxActiveBridges = Math.max(state.maxActiveBridges, state.activeBridges);
        await new Promise((resolve) => setTimeout(resolve, state.bridgeDelayMs));
        state.activeBridges -= 1;
      }
      if (command === "project.list") {
        return { ok: true, request_id: "fixture", data: { projects: state.projects } };
      }
      return state.responses.shift() ?? { ok: true, request_id: "fixture", data: {} };
    },
  };
}

export async function ensureRuntime(settings, deps) {
  state.runtimeChecks.push(settings);
  state.runtimeDeps = deps;
  if (state.runtimeReject > 0) {
    state.runtimeReject -= 1;
    throw new Error("运行时准备异常");
  }
  return state.runtime;
}

export async function findPython(settings) {
  state.discoveryChecks += 1;
  state.discoverySettings = settings;
  return state.runtime;
}

export function findPreparedPython() {
  return state.preparedPython;
}

export function defaultRuntimeHome() {
  return "C:/fake-runtime-home";
}
`,
);

await build({
  entryPoints: ["src/extension.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: join(fixture, "extension.mjs"),
  plugins: [
    {
      name: "explicit-host-fixture",
      setup(builder) {
        builder.onResolve({ filter: /^(vscode|\.\/bridge|\.\/runtime)$/ }, () => ({
          path: pathToFileURL(shim).href,
          external: true,
        }));
      },
    },
  ],
});

const { state, createView } = await import(pathToFileURL(shim).href);
const { activate } = await import(pathToFileURL(join(fixture, "extension.mjs")).href);

activate({ subscriptions: [], extensionUri: { fsPath: extensionRoot } });

const tick = () => new Promise((resolve) => setTimeout(resolve, 5));

async function until(predicate, label) {
  for (let index = 0; index < 400; index += 1) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("未等到：" + label);
}

function messagesFor(id) {
  return state.sent.filter((entry) => entry.webview === id).map((entry) => entry.message);
}

function waitResult(id, label = "result") {
  return until(
    () => state.sent.some((entry) => entry.message.type === "result" && entry.message.id === id),
    label,
  );
}

function reset() {
  state.errors.length = 0;
  state.infos.length = 0;
  state.warnings.length = 0;
  state.bridgeCalls.length = 0;
  state.bridgeDelayMs = 0;
  state.holdBridge = false;
  state.rejectPosts = false;
  state.maxActiveBridges = 0;
  state.activeBridges = 0;
  state.runtimeReject = 0;
  state.openExternalResult = true;
  state.trusted = true;
  state.settings.pythonPath = "";
  state.settings.workspacePath = "";
  state.opened.length = 0;
  state.workspaceFolders = [];
  state.projects = [];
  state.runtime = {
    ok: true,
    pythonPath: "fake-python",
    pythonArgs: [],
    version: "3.12.1",
    packageVersion: "0.5.0",
    candidates: [],
    missing: [],
    installHint: "fixture hint",
  };
  state.sent.length = 0;
  // 清运行时缓存，避免上一个用例的成功结果被复用。
  state.configurationListener?.({ affectsConfiguration: () => true });
  disposeWebviews();
  // 每个用例都从一个全新的编辑器面板开始，ready/队列不跨用例继承。
  state.commands.get("aitest.openPanel")();
}

function disposeWebviews() {
  for (const view of state.views.splice(0)) {
    view.dispose();
  }
  for (const panel of state.panels.splice(0)) {
    panel.dispose();
  }
}

function lastEditor() {
  return state.panels[state.panels.length - 1];
}

after(async () => {
  assert.ok(fixture.startsWith(join(base, "extension-unit-")));
  await rm(fixture, { recursive: true, force: true });
});

test("activation registers only the three commands, the sidebar view and folder changes", () => {
  assert.deepEqual([...state.commands.keys()].sort(), [
    "aitest.checkEnvironment",
    "aitest.openPanel",
    "aitest.runStatus",
  ]);
  assert.equal(state.viewProvider.id, "aitest.sidebar");
  assert.equal(state.viewProvider.options.webviewOptions.retainContextWhenHidden, true);
  assert.equal(typeof state.folderListener, "function");
});

test("openPanel inlines the shared panel build with CSP, nonce and injected bridge", () => {
  reset();
  state.commands.get("aitest.openPanel")();
  assert.equal(state.panels.length, 1);
  // 只是打开面板不探测项目，绑定等面板请求上下文时再算。
  assert.equal(state.discoveryChecks, 0);
  const webview = state.panels[0].webview;
  assert.equal(state.panelOptions.enableScripts, true);
  assert.equal(state.panelOptions.localResourceRoots.length, 1);
  assert.equal(webview.html.includes("AI 辅助测试"), true);
  assert.equal(webview.html.includes("{{"), false);
  assert.match(webview.html, /script-src 'nonce-[a-f0-9]{48}'/);
  assert.match(webview.html, /<style nonce="[a-f0-9]{48}">/);
  assert.match(webview.html, /<script nonce="[a-f0-9]{48}">/);
  assert.equal(webview.html.includes("Content-Security-Policy"), true);
  assert.equal(webview.html.includes("window.aitestHost"), true);
  assert.equal(webview.html.includes("acquireVsCodeApi"), true);
  assert.equal(webview.html.includes("getContext"), true);
  assert.equal(webview.html.includes(" crossorigin"), false);
  assert.equal(webview.html.includes('src="./panel.js"'), false);
  assert.equal(webview.html.includes('href="./panel.css"'), false);
  assert.equal(webview.html.includes("panel.html"), false);
  assert.equal(webview.html.includes("pipe"), false);
  const inlineScripts = [
    ...webview.html.matchAll(/<script nonce="[a-f0-9]{48}">([\s\S]*?)<\/script>/g),
  ].map((match) => match[1]);
  assert.ok(inlineScripts.length >= 2);
  assert.doesNotThrow(() => new Function(inlineScripts[inlineScripts.length - 1]));
});

test("the message listener is registered before the HTML is assigned", () => {
  const events = state.lifecycle.filter((entry) => entry.id === state.panels[0].webview.id);
  const listen = events.findIndex((entry) => entry.event === "listen");
  const html = events.findIndex((entry) => entry.event === "html");
  assert.ok(listen >= 0 && html >= 0);
  assert.ok(listen < html, "先监听再赋 HTML，避免 ready 早到丢失");
  assert.equal(events[html].listener, true);
});

test("ready publishes settings context and checkEnvironment runs doctor", async () => {
  reset();
  state.panels[0].webview.fire({ type: "ready" });
  await until(() => messagesFor(state.panels[0].webview.id).some((m) => m.type === "context"), "context");
  const context = messagesFor(state.panels[0].webview.id).find((m) => m.type === "context");
  assert.match(context.workspacePath, /aitest[\\/]workspaces[\\/]default$/);
  assert.equal(context.pythonPath, "");

  state.infos.length = 0;
  await state.commands.get("aitest.checkEnvironment")();
  assert.match(state.infos.at(-1), /环境检查/);
});

test("panel call dispatches through the bridge with discovered runtime and settings", async () => {
  reset();
  state.settings.workspacePath = "C:/data/ws";
  state.settings.pythonPath = "C:/configured/python.exe";
  state.runtime = { ...state.runtime, pythonPath: "C:/found/python.exe", pythonArgs: ["-3"] };
  state.panels[0].webview.fire({ type: "call", id: "call-1", command: "doctor", payload: { verbose: true } });
  await waitResult("call-1");
  const call = state.bridgeCalls.at(-1);
  assert.equal(call.command, "doctor");
  assert.deepEqual(call.payload, { verbose: true });
  assert.equal(call.options.confirm, false);
  assert.equal(call.config.pythonPath, "C:/found/python.exe");
  assert.deepEqual(call.config.pythonArgs, ["-3"]);
  assert.equal(call.config.workspacePath, "C:/data/ws");
  assert.equal(call.config.timeoutMs, 30000);
  assert.equal(state.runtimeDeps.wheelDir, join(extensionRoot, "media", "runtime"));
  const result = state.sent.at(-1).message;
  assert.equal(result.ok, true);
  assert.equal(result.id, "call-1");
});

test("backup commands wait longer than the CLI lock budget", async () => {
  reset();
  state.panels[0].webview.fire({ type: "call", id: "backup-1", command: "backup.create", payload: {} });
  await waitResult("backup-1");
  assert.equal(state.bridgeCalls.at(-1).config.timeoutMs, 630000);
});

test("run.start keeps confirmation, other commands cannot smuggle it", async () => {
  reset();
  state.panels[0].webview.fire({
    type: "call",
    id: "run-confirm",
    command: "run.start",
    payload: { project_id: "p", plan_id: "plan_1" },
    confirm: true,
  });
  await until(() => state.bridgeCalls.length === 1, "run.start call");
  assert.equal(state.bridgeCalls[0].options.confirm, true);

  state.panels[0].webview.fire({
    type: "call",
    id: "run-options",
    command: "run.start",
    payload: { project_id: "p", plan_id: "plan_2" },
    options: { confirm: true },
  });
  await until(() => state.bridgeCalls.length === 2, "run.start options call");
  assert.equal(state.bridgeCalls[1].options.confirm, true);

  state.panels[0].webview.fire({ type: "call", id: "doctor-confirm", command: "doctor", payload: {}, confirm: true });
  await until(() => state.bridgeCalls.length === 3, "doctor call");
  assert.equal(state.bridgeCalls[2].options.confirm, false);

  state.panels[0].webview.fire({ type: "call", id: "cancel-confirm", command: "run.cancel", payload: { run_id: "r" }, confirm: true });
  await until(() => state.bridgeCalls.length === 4, "run.cancel call");
  assert.equal(state.bridgeCalls[3].options.confirm, false);
});

test("pickDirectory returns the chosen folder and handles cancel", async () => {
  reset();
  state.openDialogResult = [{ fsPath: "D:/work/picked" }];
  state.panels[0].webview.fire({ type: "pickDirectory", id: "pick-1" });
  await waitResult("pick-1");
  assert.deepEqual(state.sent.at(-1).message, {
    type: "result",
    id: "pick-1",
    ok: true,
    data: "D:/work/picked",
  });

  state.openDialogResult = [];
  state.panels[0].webview.fire({ type: "pickDirectory", id: "pick-2" });
  await waitResult("pick-2");
  assert.equal(state.sent.at(-1).message.data, "");
});

test("panel writes and reads stay inside the allowlist", async () => {
  reset();
  const commands = [
    "task.update",
    "task.delete",
    "module.add",
    "delivery.save",
    "delivery.show",
    "plan.update",
    "run.cancel",
    "run.manual",
    "analysis.run",
    "issue.list",
    "issue.show",
    "report.opinion",
    "report.show",
    "report.list",
    "rules.list",
    "github.read",
    "link.import",
    "evidence.add",
  ];
  for (const command of commands) {
    state.panels[0].webview.fire({ type: "call", id: "allowed-" + command, command, payload: {} });
  }
  await until(() => state.bridgeCalls.length === commands.length, "allowed commands");
  assert.deepEqual(
    state.bridgeCalls.map((call) => call.command),
    commands,
  );
});

test("commands outside the panel allowlist never reach the bridge", async () => {
  reset();
  state.panels[0].webview.fire({ type: "call", id: "blocked", command: "project.delete", payload: {} });
  await waitResult("blocked", "blocked result");
  assert.equal(state.bridgeCalls.length, 0);
  const result = state.sent.at(-1).message;
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "COMMAND_NOT_ALLOWED");
});

test("an untrusted workspace is rejected inside execute, not only in the UI", async () => {
  reset();
  state.trusted = false;
  state.panels[0].webview.fire({ type: "call", id: "untrusted", command: "doctor", payload: {} });
  await waitResult("untrusted");
  assert.equal(state.bridgeCalls.length, 0);
  assert.equal(state.sent.at(-1).message.error.code, "UNTRUSTED_WORKSPACE");
});

test("missing Python is an explicit local error", async () => {
  reset();
  state.runtime = {
    ok: false,
    pythonPath: null,
    pythonArgs: [],
    version: null,
    packageVersion: null,
    candidates: [],
    missing: ["Python 3.11+", "aitest 包"],
    installHint: "请安装 Python 3.11+",
  };
  state.panels[0].webview.fire({ type: "call", id: "no-python", command: "doctor", payload: {} });
  await waitResult("no-python", "python error");
  const result = state.sent.at(-1).message;
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "PYTHON_NOT_FOUND");
  assert.equal(result.error.next_step, "请安装 Python 3.11+");
  assert.deepEqual(result.details.runtime.missing, ["Python 3.11+", "aitest 包"]);
  assert.equal(state.bridgeCalls.length, 0);
});

test("a rejected runtime promise is not cached forever", async () => {
  reset();
  state.runtimeReject = 1;
  state.panels[0].webview.fire({ type: "call", id: "runtime-boom", command: "doctor", payload: {} });
  await waitResult("runtime-boom");
  assert.equal(state.sent.at(-1).message.error.code, "HOST_CALL_FAILED");
  assert.equal(state.runtimeChecks.length >= 1, true);

  const before = state.runtimeChecks.length;
  state.panels[0].webview.fire({ type: "call", id: "runtime-retry", command: "doctor", payload: {} });
  await waitResult("runtime-retry");
  assert.equal(state.sent.at(-1).message.ok, true);
  assert.equal(state.runtimeChecks.length, before + 1, "第二次调用重新准备运行时");
});

test("runtime warnings update status only; real failures show one dialog", async () => {
  reset();
  state.holdBridge = true;
  state.panels[0].webview.fire({ type: "ready" });
  state.panels[0].webview.fire({ type: "call", id: "stages", command: "doctor", payload: {} });
  await until(() => typeof state.runtimeDeps?.onStage === "function", "runtime deps");
  state.runtimeDeps.onStage({ stage: "verify", ok: false, severity: "warning", code: "CACHED_RUNTIME_OUTDATED" });
  assert.deepEqual(state.errors, []);
  state.runtimeDeps.onStage({ stage: "install", ok: false, severity: "error", code: "INSTALL_FAILED", detail: "x" });
  assert.equal(state.errors.length, 1);
  await until(
    () => messagesFor(state.panels[0].webview.id).some((m) => m.type === "progress"),
    "runtime progress",
  );
  state.panels[0].webview.fire({ type: "cancel", command: "doctor", payload: {} });
  await waitResult("stages");
});

test("identical in-flight calls are deduplicated", async () => {
  reset();
  state.bridgeDelayMs = 40;
  state.panels[0].webview.fire({ type: "call", id: "dup-1", command: "project.list", payload: { limit: 5 } });
  state.panels[0].webview.fire({ type: "call", id: "dup-2", command: "project.list", payload: { limit: 5 } });
  await waitResult("dup-1");
  await waitResult("dup-2");
  const calls = state.bridgeCalls.filter((call) => call.command === "project.list");
  assert.equal(calls.length, 1);
  assert.equal(state.sent.filter((entry) => entry.message.id === "dup-1" && entry.message.type === "result").length, 1);
  assert.equal(state.sent.filter((entry) => entry.message.id === "dup-2" && entry.message.type === "result").length, 1);
});

test("cancel aborts the running child and returns CLI_CANCELLED", async () => {
  reset();
  state.holdBridge = true;
  state.panels[0].webview.fire({ type: "call", id: "cancel-me", command: "run.repair", payload: { run_id: "r" }, confirm: true });
  await until(() => state.bridgeCalls.length === 1, "repair call");
  state.panels[0].webview.fire({ type: "cancel", command: "run.repair", payload: { run_id: "r" }, confirm: true });
  await waitResult("cancel-me");
  assert.equal(state.sent.at(-1).message.error.code, "CLI_CANCELLED");
});

test("concurrent panel calls are capped", async () => {
  reset();
  state.bridgeDelayMs = 30;
  for (let index = 0; index < 6; index += 1) {
    state.panels[0].webview.fire({
      type: "call",
      id: "parallel-" + index,
      command: "doctor",
      payload: { index },
    });
  }
  for (let index = 0; index < 6; index += 1) {
    await waitResult("parallel-" + index);
  }
  assert.ok(state.maxActiveBridges <= 4, `并发不能超过 4，实际 ${state.maxActiveBridges}`);
});

test("configuration change republishes the context", async () => {
  reset();
  state.settings.workspacePath = "D:/other-ws";
  state.panels[0].webview.fire({ type: "ready" });
  state.configurationListener({ affectsConfiguration: (section) => section === "aitest" });
  await tick();
  const context = messagesFor(state.panels[0].webview.id).filter((message) => message.type === "context").at(-1);
  assert.equal(context.workspacePath, "D:/other-ws");
});

test("context is queued per webview until that webview is ready", async () => {
  reset();
  state.settings.workspacePath = "C:/data/ws";
  state.configurationListener({ affectsConfiguration: (section) => section === "aitest" });
  await tick();
  const before = messagesFor(state.panels[0].webview.id).filter((m) => m.type === "context").length;
  state.panels[0].webview.fire({ type: "ready" });
  await tick();
  const after = messagesFor(state.panels[0].webview.id).filter((m) => m.type === "context").length;
  assert.ok(after > before);
});

async function postContextFor(folders, projects) {
  reset();
  await tick();
  state.workspaceFolders = folders.map((fsPath) => ({ uri: { fsPath } }));
  state.projects = projects;
  state.settings.workspacePath = "C:/data/ws";
  state.panels[0].webview.fire({ type: "ready" });
  state.configurationListener({ affectsConfiguration: (section) => section === "aitest" });
  await until(
    () => messagesFor(state.panels[0].webview.id).some((message) => message.type === "context"),
    "binding context",
  );
  await tick();
  return messagesFor(state.panels[0].webview.id).filter((message) => message.type === "context").at(-1);
}

test("single workspace folder matching a project root binds projectId", async () => {
  const context = await postContextFor(
    ["C:/work/order-service"],
    [{ project_id: "proj_1", root: "C:\\work\\order-service" }],
  );
  assert.equal(context.projectId, "proj_1");
  assert.equal(context.workspaceFolderCount, 1);
  assert.equal(context.projectRoot, "C:/work/order-service");
});

test("single workspace folder without a match only prefills the path", async () => {
  const context = await postContextFor(["C:/work/new-service"], []);
  assert.equal(context.projectId, "");
  assert.equal(context.projectRoot, "C:/work/new-service");
});

test("multiple workspace folders are never guessed", async () => {
  const context = await postContextFor(
    ["C:/work/order-service", "C:/work/other"],
    [{ project_id: "proj_1", root: "C:/work/order-service" }],
  );
  assert.equal(context.projectId, "");
  assert.equal(context.projectRoot, "");
  assert.equal(context.workspaceFolderCount, 2);
});

test("a parent workspace folder does not bind a nested project", async () => {
  const context = await postContextFor(
    ["C:/work"],
    [{ project_id: "proj_1", root: "C:/work/order-service" }],
  );
  assert.equal(context.projectId, "");
  assert.equal(context.projectRoot, "C:/work");
});

test("a prepared portable runtime is visible to automatic binding", async () => {
  reset();
  await tick();
  state.runtime = { ...state.runtime, ok: false, pythonPath: null, missing: ["Python 3.11+"] };
  state.preparedPython = "C:/runtime/3.13.7/venv/Scripts/python.exe";
  state.projects = [{ project_id: "proj_1", root: "C:/work/order-service" }];
  state.workspaceFolders = [{ uri: { fsPath: "C:/work/order-service" } }];
  state.settings.workspacePath = "C:/data/ws";
  state.panels[0].webview.fire({ type: "ready" });
  state.configurationListener({ affectsConfiguration: (section) => section === "aitest" });
  await tick();
  const context = messagesFor(state.panels[0].webview.id).filter((message) => message.type === "context").at(-1);
  assert.equal(context.projectId, "proj_1");
});

test("workspace folder changes recompute the binding", async () => {
  reset();
  await tick();
  state.settings.workspacePath = "C:/data/ws";
  state.projects = [{ project_id: "proj_2", root: "C:/work/new" }];
  state.workspaceFolders = [{ uri: { fsPath: "C:/work/new" } }];
  state.panels[0].webview.fire({ type: "ready" });
  state.folderListener();
  await tick();
  const context = messagesFor(state.panels[0].webview.id).filter((message) => message.type === "context").at(-1);
  assert.equal(context.projectId, "proj_2");
});

async function evidenceFile(name) {
  const workspaceDir = join(fixture, "workspace");
  await mkdir(join(workspaceDir, "evidence"), { recursive: true });
  const path = join(workspaceDir, "evidence", name);
  await writeFile(path, "evidence text", "utf8");
  return { workspace: workspaceDir, path };
}

test("openPath opens a file inside the user data workspace", async () => {
  reset();
  const file = await evidenceFile("ok.log");
  state.settings.workspacePath = file.workspace;
  state.opened.length = 0;
  state.panels[0].webview.fire({ type: "openPath", id: "open-1", path: file.path });
  await waitResult("open-1");
  assert.deepEqual(state.opened, [file.path]);
  assert.equal(state.sent.at(-1).message.ok, true);
});

test("openPath rejects paths outside the workspace, UNC and executables", async () => {
  reset();
  const file = await evidenceFile("run.log");
  state.settings.workspacePath = file.workspace;
  const outside = join(fixture, "outside.txt");
  await writeFile(outside, "x", "utf8");
  const exe = join(file.workspace, "evidence", "tool.exe");
  await writeFile(exe, "x", "utf8");
  const cases = [
    { id: "open-outside", path: outside },
    { id: "open-unc", path: "\\\\server\\share\\evidence.log" },
    { id: "open-relative", path: "evidence\\run.log" },
    { id: "open-exe", path: exe },
  ];
  for (const entry of cases) {
    state.panels[0].webview.fire({ type: "openPath", id: entry.id, path: entry.path });
    await waitResult(entry.id);
    assert.equal(state.sent.at(-1).message.ok, false, entry.id);
    assert.equal(state.sent.at(-1).message.error.code, "INVALID_PATH", entry.id);
  }
  assert.deepEqual(state.opened, []);
});

test("openPath reports failure when the OS has no handler", async () => {
  reset();
  const file = await evidenceFile("no-handler.log");
  state.settings.workspacePath = file.workspace;
  state.openExternalResult = false;
  state.panels[0].webview.fire({ type: "openPath", id: "open-false", path: file.path });
  await waitResult("open-false");
  assert.equal(state.sent.at(-1).message.ok, false);
  assert.equal(state.sent.at(-1).message.error.code, "HOST_CALL_FAILED");
});

test("sidebar and editor panels keep separate handles", () => {
  reset();
  disposeWebviews();
  const panelsBefore = state.panels.length;
  const view = createView();
  state.views.push(view);
  state.viewProvider.provider.resolveWebviewView(view);
  state.commands.get("aitest.openPanel")();
  // 侧栏已存在时直接显示，不再创建第二个 webview。
  assert.equal(state.panels.length, panelsBefore);
  assert.equal(view.showCount, 1);

  view.dispose();
  state.commands.get("aitest.openPanel")();
  assert.equal(state.panels.length, panelsBefore + 1);
  const editor = lastEditor();
  assert.equal(editor.webview.kind, "editor");
});

test("a ready editor webview receives its own context", async () => {
  reset();
  const editor = lastEditor();
  editor.webview.fire({ type: "ready" });
  await until(() => messagesFor(editor.webview.id).some((m) => m.type === "context"), "editor context");
  assert.ok(messagesFor(editor.webview.id).some((m) => m.type === "context"));
});

test("panel-error text is truncated before it reaches the log", async () => {
  reset();
  const long = "x".repeat(5000);
  state.panels[state.panels.length - 1].webview.fire({ type: "panel-error", kind: "error", message: long });
  await tick();
  const logPath = join(tmpdir(), "aitest-extension.log");
  assert.equal(existsSync(logPath), true);
  const lines = (await readFile(logPath, "utf8")).trim().split(/\r?\n/);
  const last = lines.at(-1);
  assert.ok(last.length < long.length, "日志不应保留完整 5000 字符错误");
  assert.match(last, /已截断/);
});

test("openPanel 在旧 webview 已销毁时重建面板", () => {
  reset();
  const before = state.panels.length;
  state.revealThrows = true;
  try {
    assert.doesNotThrow(() => state.commands.get("aitest.openPanel")());
  } finally {
    state.revealThrows = false;
  }
  assert.equal(state.panels.length, before + 1);
  assert.equal(state.panels[state.panels.length - 1].webview.html.includes("AI 辅助测试"), true);
});
