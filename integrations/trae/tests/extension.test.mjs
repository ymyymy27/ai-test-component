import { test, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

// 显式 VS Code API / bridge / runtime 夹具：证明扩展接线，不启动真实 Trae。
const root = resolve("../..");
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
  projects: [],
  runtime: { ok: true, pythonPath: "fake-python", pythonArgs: [], version: "3.12.1",
    packageVersion: "0.1.0", candidates: [], missing: [], installHint: "fixture hint" },
  runtimeChecks: [],
  commands: new Map(),
  sent: [],
  panels: [],
  panelOptions: null,
  bridgeCalls: [],
  bridgeConfigs: [],
  responses: [],
  revealed: 0,
  opened: [],
};

function makePanel() {
  const webview = {
    html: "",
    cspSource: "vscode-resource:",
    options: {},
    asWebviewUri: (uri) => ({ toString: () => "webview:" + uri.fsPath }),
    onDidReceiveMessage: (cb) => {
      state.receive = cb;
      return { dispose() { state.receiveDisposed = true; } };
    },
    postMessage: (message) => {
      state.sent.push(message);
      return Promise.resolve(true);
    },
  };
  return {
    webview,
    reveal() { state.revealed += 1; },
    onDidDispose(cb) { state.panelDispose = cb; },
    dispose() { state.disposed = true; },
  };
}

export const workspace = {
  isTrusted: true,
  get workspaceFolders() { return state.workspaceFolders; },
  getConfiguration: () => ({
    get: (key, fallback) => state.settings[key] ?? fallback,
  }),
  onDidChangeConfiguration: (cb) => {
    state.configurationListener = cb;
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
  showWarningMessage: (text) => { state.warning = text; },
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
    return true;
  },
};

export function createBridge(config) {
  state.bridgeConfigs.push(config);
  return {
    async call(command, payload, options) {
      state.bridgeCalls.push({ command, payload, options, config });
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
  return state.runtime;
}

export async function findPython(settings) {
  state.discoveryChecks = (state.discoveryChecks ?? 0) + 1;
  state.discoverySettings = settings;
  return state.runtime;
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

const { state } = await import(pathToFileURL(shim).href);
const { activate } = await import(pathToFileURL(join(fixture, "extension.mjs")).href);

activate({ subscriptions: [], extensionUri: { fsPath: extensionRoot } });

const tick = () => new Promise((resolve) => setTimeout(resolve, 5));

async function until(predicate, label) {
  for (let index = 0; index < 200; index += 1) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("未等到：" + label);
}

after(async () => {
  assert.ok(fixture.startsWith(join(base, "extension-unit-")));
  await rm(fixture, { recursive: true, force: true });
});

test("activation registers only the three commands and the sidebar view", () => {
  assert.deepEqual([...state.commands.keys()].sort(), [
    "aitest.checkEnvironment",
    "aitest.openPanel",
    "aitest.runStatus",
  ]);
  assert.equal(state.viewProvider.id, "aitest.sidebar");
  assert.equal(state.viewProvider.options.webviewOptions.retainContextWhenHidden, true);
});

test("openPanel loads the shared panel build with CSP, nonce and injected bridge", () => {
  state.commands.get("aitest.openPanel")();
  assert.equal(state.panels.length, 1);
  const webview = state.panels[0].webview;
  assert.equal(state.panelOptions.enableScripts, true);
  assert.equal(state.panelOptions.localResourceRoots.length, 1);
  assert.equal(webview.html.includes("AI 辅助测试"), true);
  assert.equal(webview.html.includes("{{"), false);
  assert.match(webview.html, /nonce-[a-f0-9]{48}/);
  assert.equal(webview.html.includes("Content-Security-Policy"), true);
  assert.equal(webview.html.includes("window.aitestHost"), true);
  assert.equal(webview.html.includes("acquireVsCodeApi"), true);
  assert.equal(webview.html.includes("getContext"), true);
  assert.equal(webview.html.includes(" crossorigin"), false);
  assert.match(webview.html, /webview:.*media.*panel.*assets.*\.js\?v=[a-f0-9]{12}/);
  assert.match(webview.html, /webview:.*media.*panel.*assets.*\.css\?v=[a-f0-9]{12}/);
  assert.equal(webview.html.includes("panel.html"), false);
  assert.equal(webview.html.includes("pipe"), false);
});

test("ready publishes settings context and flushes queued panel commands", async () => {
  state.sent.length = 0;
  state.commands.get("aitest.checkEnvironment")();
  assert.equal(state.revealed, 1);
  assert.equal(state.sent.length, 0);
  state.receive({ type: "ready" });
  await tick();
  const context = state.sent.find((message) => message.type === "context");
  assert.equal(context.workspacePath, "");
  assert.equal(context.pythonPath, "");
  assert.equal(state.sent.some((message) => message.type === "command" && message.command === "doctor"), true);
});

test("panel call dispatches through the bridge with discovered runtime and settings", async () => {
  state.settings.workspacePath = "C:/data/ws";
  state.settings.pythonPath = "C:/configured/python.exe";
  state.runtime = { ...state.runtime, pythonPath: "C:/found/python.exe", pythonArgs: ["-3"] };
  state.bridgeCalls.length = 0;
  state.receive({ type: "call", id: "call-1", command: "doctor", payload: { verbose: true } });
  await until(() => state.sent.some((message) => message.type === "result" && message.id === "call-1"), "doctor result");
  const call = state.bridgeCalls.at(-1);
  assert.equal(call.command, "doctor");
  assert.deepEqual(call.payload, { verbose: true });
  assert.equal(call.options.confirm, false);
  assert.equal(call.config.pythonPath, "C:/found/python.exe");
  assert.deepEqual(call.config.pythonArgs, ["-3"]);
  assert.equal(call.config.workspacePath, "C:/data/ws");
  assert.equal(call.config.timeoutMs, 30000);
  // 便携运行时只从 VSIX 自带的 media/runtime 取 wheel。
  assert.equal(state.runtimeDeps.wheelDir, join(extensionRoot, "media", "runtime"));
  const result = state.sent.at(-1);
  assert.equal(result.ok, true);
  assert.equal(result.id, "call-1");
});

test("run.start keeps confirmation, other commands cannot smuggle it", async () => {
  state.bridgeCalls.length = 0;
  state.receive({
    type: "call",
    id: "run-confirm",
    command: "run.start",
    payload: { project_id: "p", plan_id: "plan_1" },
    confirm: true,
  });
  await until(() => state.bridgeCalls.length === 1, "run.start call");
  assert.equal(state.bridgeCalls[0].options.confirm, true);

  state.receive({
    type: "call",
    id: "run-options",
    command: "run.start",
    payload: { project_id: "p", plan_id: "plan_2" },
    options: { confirm: true },
  });
  await until(() => state.bridgeCalls.length === 2, "run.start options call");
  assert.equal(state.bridgeCalls[1].options.confirm, true);

  state.receive({ type: "call", id: "doctor-confirm", command: "doctor", payload: {}, confirm: true });
  await until(() => state.bridgeCalls.length === 3, "doctor call");
  assert.equal(state.bridgeCalls[2].options.confirm, false);

  state.receive({ type: "call", id: "cancel-confirm", command: "run.cancel", payload: { run_id: "r" }, confirm: true });
  await until(() => state.bridgeCalls.length === 4, "run.cancel call");
  assert.equal(state.bridgeCalls[3].options.confirm, false);
});

test("run.cancel, analysis.run and issue.list stay inside the allowlist", async () => {
  state.bridgeCalls.length = 0;
  for (const command of ["run.cancel", "analysis.run", "issue.list", "report.opinion"]) {
    state.receive({ type: "call", id: "allowed-" + command, command, payload: {} });
  }
  await until(() => state.bridgeCalls.length === 4, "allowed commands");
  assert.deepEqual(
    state.bridgeCalls.map((call) => call.command),
    ["run.cancel", "analysis.run", "issue.list", "report.opinion"],
  );
});

test("commands outside the panel allowlist never reach the bridge", async () => {
  state.bridgeCalls.length = 0;
  state.receive({ type: "call", id: "blocked", command: "backup.create", payload: {} });
  await until(() => state.sent.some((message) => message.type === "result" && message.id === "blocked"), "blocked result");
  assert.equal(state.bridgeCalls.length, 0);
  const result = state.sent.at(-1);
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "COMMAND_NOT_ALLOWED");
});

test("missing workspace and missing Python are explicit local errors", async () => {
  state.settings.workspacePath = "";
  state.bridgeCalls.length = 0;
  state.receive({ type: "call", id: "no-workspace", command: "doctor", payload: {} });
  await until(() => state.sent.some((message) => message.type === "result" && message.id === "no-workspace"), "workspace error");
  assert.equal(state.bridgeCalls.length, 0);
  assert.equal(state.sent.at(-1).error.code, "WORKSPACE_NOT_CONFIGURED");

  state.settings.workspacePath = "C:/data/ws";
  state.settings.pythonPath = "C:/missing/python.exe";
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
  state.receive({ type: "call", id: "no-python", command: "doctor", payload: {} });
  await until(() => state.sent.some((message) => message.type === "result" && message.id === "no-python"), "python error");
  const result = state.sent.at(-1);
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "PYTHON_NOT_FOUND");
  assert.equal(result.error.next_step, "请安装 Python 3.11+");
  assert.deepEqual(result.details.runtime.missing, ["Python 3.11+", "aitest 包"]);
  assert.equal(state.bridgeCalls.length, 0);
});

test("configuration change republishes the context", async () => {
  state.sent.length = 0;
  state.settings.workspacePath = "D:/other-ws";
  state.configurationListener({ affectsConfiguration: (section) => section === "aitest" });
  await tick();
  const context = state.sent.find((message) => message.type === "context");
  assert.equal(context.workspacePath, "D:/other-ws");
});

async function postContextFor(folders, projects) {
  state.workspaceFolders = folders.map((fsPath) => ({ uri: { fsPath } }));
  state.projects = projects;
  state.settings.workspacePath = "C:/data/ws";
  state.runtime = { ...state.runtime, ok: true, pythonPath: "fake-python", pythonArgs: [] };
  state.sent.length = 0;
  state.commands.get("aitest.openPanel")();
  state.receive({ type: "ready" });
  await until(
    () => state.sent.some((message) => message.type === "context"),
    "workspace context",
  );
  return state.sent.filter((message) => message.type === "context").at(-1);
}

test("single workspace folder matching a project root binds projectId", async () => {
  const context = await postContextFor(
    ["C:/work/order-service"],
    [{ project_id: "proj_1", root: "C:\\work\\order-service" }],
  );
  assert.equal(context.projectId, "proj_1");
  assert.equal(context.workspaceFolderCount, 1);
  assert.equal(context.projectRoot, "C:/work/order-service");
  state.workspaceFolders = [];
  state.projects = [];
  await tick();
});

test("single workspace folder without a match only prefills the path", async () => {
  const context = await postContextFor(["C:/work/new-service"], []);
  assert.equal(context.projectId, "");
  assert.equal(context.projectRoot, "C:/work/new-service");
  state.workspaceFolders = [];
  await tick();
});

test("multiple workspace folders are never guessed", async () => {
  const context = await postContextFor(
    ["C:/work/order-service", "C:/work/other"],
    [{ project_id: "proj_1", root: "C:/work/order-service" }],
  );
  assert.equal(context.projectId, "");
  assert.equal(context.projectRoot, "");
  assert.equal(context.workspaceFolderCount, 2);
  state.workspaceFolders = [];
  state.projects = [];
  await tick();
});

test("a parent workspace folder does not bind a nested project", async () => {
  const context = await postContextFor(
    ["C:/work"],
    [{ project_id: "proj_1", root: "C:/work/order-service" }],
  );
  assert.equal(context.projectId, "");
  assert.equal(context.projectRoot, "C:/work");
  state.workspaceFolders = [];
  state.projects = [];
  await tick();
});

test("openPath asks the editor to open the evidence file", async () => {
  state.opened.length = 0;
  state.sent.length = 0;
  state.receive({ type: "openPath", id: "open-1", path: "C:/data/ws/evidence/run/error.log" });
  await until(() => state.sent.some((message) => message.id === "open-1"), "open result");
  assert.deepEqual(state.opened, ["C:/data/ws/evidence/run/error.log"]);
  const result = state.sent.at(-1);
  assert.equal(result.ok, true);
});
