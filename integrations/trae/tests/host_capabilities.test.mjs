import { test, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import { build } from "esbuild";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const extensionRoot = join(root, "integrations", "trae");
const base = join(root, ".runtime/tmp");
await mkdir(base, { recursive: true });
const fixture = await mkdtemp(join(base, "host-cap-unit-"));
const outBundle = join(fixture, "host_capabilities.mjs");

await build({
  entryPoints: [join(extensionRoot, "src", "host_capabilities.ts")],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: outBundle,
});

const { createHostCapabilities, resolveSourceFile } = await import(pathToFileURL(outBundle).href);

const projectDir = join(fixture, "sample-project");
const outsideDir = join(fixture, "outside-dir");
await mkdir(join(projectDir, "src"), { recursive: true });
await mkdir(outsideDir, { recursive: true });
const appPy = join(projectDir, "src", "app.py");
const secretOutside = join(outsideDir, "secret.txt");
await writeFile(appPy, "def login():\n    return True\n", "utf8");
await writeFile(secretOutside, "secret", "utf8");

after(async () => {
  await rm(fixture, { recursive: true, force: true });
});

test("resolveSourceFile accepts project files and blocks path traversal", () => {
  const relOk = resolveSourceFile(projectDir, "src/app.py");
  assert.equal(relOk.ok, true);
  assert.equal(relOk.relPath, "src/app.py");

  const absOk = resolveSourceFile(projectDir, appPy);
  assert.equal(absOk.ok, true);

  const traversal = resolveSourceFile(projectDir, "../outside-dir/secret.txt");
  assert.equal(traversal.ok, false);
  assert.equal(traversal.code, "PERMISSION_DENIED");

  const missing = resolveSourceFile(projectDir, "src/missing.py");
  assert.equal(missing.ok, false);
  assert.equal(missing.code, "NOT_FOUND");

  const unbound = resolveSourceFile("", "src/app.py");
  assert.equal(unbound.ok, false);
  assert.equal(unbound.code, "PROJECT_UNBOUND");
});

test("HostCapabilities gracefully degrades when host provides minimal APIs", async () => {
  const minimalHost = {
    Uri: { file: (fsPath) => ({ fsPath }) },
  };
  const caps = createHostCapabilities(minimalHost);
  const flags = caps.detectCapabilities();
  assert.deepEqual(flags, {
    git: false,
    fileWatcher: false,
    documentSymbols: false,
    openSource: false,
    diagnostics: false,
    secretStorage: false,
    statusBar: false,
    outputChannel: false,
  });
  assert.equal(await caps.readGitSummary(projectDir), null);
  assert.deepEqual(await caps.readDocumentSymbols(projectDir, "src/app.py"), []);
  const jump = await caps.jumpToSource(projectDir, "src/app.py", 2, 5);
  assert.equal(jump.ok, false);
  assert.equal(jump.code, "HOST_UNSUPPORTED");

  const diag = caps.publishDiagnostics(projectDir, [{ file: "src/app.py", line: 1, message: "err" }]);
  assert.equal(diag.count, 0);
  assert.equal(diag.unsupported, true);
  assert.equal(caps.updateStatusBar("OK"), false);
  assert.equal(caps.appendOutput("hello"), false);
  assert.equal(await caps.storeSecret("k", "v"), false);
  assert.equal(await caps.getSecret("k"), undefined);

  const watcher = caps.watchWorkspace(projectDir, () => {});
  watcher.dispose();
  caps.dispose();
});

test("HostCapabilities utilizes full host APIs for git, symbols, jump, watcher, diagnostics, and secrets", async () => {
  const shown = [];
  const diagSets = [];
  let diagCleared = 0;
  let diagDisposed = false;
  let statusBarText = "";
  let statusBarDisposed = false;
  const outputs = [];
  let outputDisposed = false;
  const secretsMap = new Map();

  let watchListeners = null;
  let watcherDisposed = false;

  const fullHost = {
    Uri: { file: (fsPath) => ({ fsPath }) },
    RelativePattern: class RelativePattern {
      constructor(baseDir, pattern) {
        this.baseDir = baseDir;
        this.pattern = pattern;
      }
    },
    Range: class Range {
      constructor(sl, sc, el, ec) {
        this.start = { line: sl, character: sc };
        this.end = { line: el, character: ec };
      }
    },
    DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2 },
    Diagnostic: class Diagnostic {
      constructor(range, message, severity) {
        this.range = range;
        this.message = message;
        this.severity = severity;
      }
    },
    StatusBarAlignment: { Left: 1, Right: 2 },
    workspace: {
      openTextDocument: async (uri) => ({ uri }),
      createFileSystemWatcher: () => {
        watchListeners = { change: null, create: null, delete: null };
        return {
          onDidChange: (cb) => {
            watchListeners.change = cb;
            return { dispose() {} };
          },
          onDidCreate: (cb) => {
            watchListeners.create = cb;
            return { dispose() {} };
          },
          onDidDelete: (cb) => {
            watchListeners.delete = cb;
            return { dispose() {} };
          },
          dispose: () => {
            watcherDisposed = true;
          },
        };
      },
    },
    window: {
      showTextDocument: async (doc, opts) => {
        shown.push({ doc, opts });
        return doc;
      },
      createStatusBarItem: () => ({
        text: "",
        tooltip: "",
        command: "",
        show() {
          statusBarText = this.text;
        },
        hide() {},
        dispose() {
          statusBarDisposed = true;
        },
      }),
      createOutputChannel: () => ({
        appendLine: (line) => outputs.push(line),
        dispose: () => {
          outputDisposed = true;
        },
      }),
    },
    commands: {
      executeCommand: async (cmd) => {
        if (cmd === "vscode.executeDocumentSymbolProvider") {
          return [
            {
              name: "AuthService",
              detail: "class",
              kind: 5,
              range: { start: { line: 0 }, end: { line: 10 } },
              children: [
                {
                  name: "login",
                  detail: "method",
                  kind: 6,
                  range: { start: { line: 2 }, end: { line: 5 } },
                },
              ],
            },
          ];
        }
        return [];
      },
    },
    languages: {
      createDiagnosticCollection: () => ({
        set: (uri, list) => diagSets.push({ uri, list }),
        clear: () => {
          diagCleared += 1;
        },
        dispose: () => {
          diagDisposed = true;
        },
      }),
    },
    extensions: {
      getExtension: (id) =>
        id === "vscode.git"
          ? {
              isActive: true,
              exports: {
                getAPI: () => ({
                  repositories: [
                    {
                      rootUri: { fsPath: projectDir },
                      state: {
                        HEAD: { name: "feat/demo", commit: "abc1234", ahead: 2, behind: 1 },
                        workingTreeChanges: [{}],
                        indexChanges: [],
                        untrackedChanges: [{}],
                      },
                    },
                  ],
                }),
              },
            }
          : undefined,
    },
  };

  const context = {
    secrets: {
      get: async (k) => secretsMap.get(k),
      store: async (k, v) => {
        secretsMap.set(k, v);
      },
      delete: async (k) => {
        secretsMap.delete(k);
      },
    },
  };

  const caps = createHostCapabilities(fullHost, context);
  assert.deepEqual(caps.detectCapabilities(), {
    git: true,
    fileWatcher: true,
    documentSymbols: true,
    openSource: true,
    diagnostics: true,
    secretStorage: true,
    statusBar: true,
    outputChannel: true,
  });

  const git = await caps.readGitSummary(projectDir);
  assert.deepEqual(git, {
    available: true,
    branch: "feat/demo",
    head: "abc1234",
    ahead: 2,
    behind: 1,
    dirtyCount: 2,
  });

  const symbols = await caps.readDocumentSymbols(projectDir, "src/app.py");
  assert.equal(symbols.length, 2);
  assert.equal(symbols[0].startLine, 1);
  assert.equal(symbols[1].name, "login");
  assert.equal(symbols[1].startLine, 3);

  const jumped = await caps.jumpToSource(projectDir, "src/app.py", 2, 5);
  assert.equal(jumped.ok, true);
  assert.equal(shown.length, 1);
  assert.equal(shown[0].opts.selection.start.line, 1);
  assert.equal(shown[0].opts.selection.start.character, 4);

  const dirtyBatches = [];
  caps.watchWorkspace(projectDir, (paths) => dirtyBatches.push(paths), 20);
  watchListeners.change({ fsPath: join(projectDir, ".git", "index") });
  watchListeners.create({ fsPath: join(projectDir, "src", "app.py") });
  watchListeners.delete({ fsPath: secretOutside });
  await new Promise((r) => setTimeout(r, 60));
  assert.deepEqual(dirtyBatches, [["src/app.py"]]);

  const diagRes = caps.publishDiagnostics(projectDir, [
    { file: "src/app.py", line: 2, message: "接口断言失败", severity: "error", code: "CASE_1" },
    { file: "../outside-dir/secret.txt", line: 1, message: "越界应丢弃" },
  ]);
  assert.equal(diagRes.count, 1);
  assert.equal(diagSets.length, 1);
  assert.equal(diagSets[0].list[0].range.start.line, 1);
  caps.clearDiagnostics();
  assert.ok(diagCleared >= 2);

  assert.equal(await caps.storeSecret("api_token", "sk-secret"), true);
  assert.equal(await caps.getSecret("api_token"), "sk-secret");
  assert.equal(await caps.deleteSecret("api_token"), true);
  assert.equal(await caps.getSecret("api_token"), undefined);

  assert.equal(caps.updateStatusBar("AI 测试: 就绪"), true);
  assert.equal(statusBarText, "AI 测试: 就绪");
  assert.equal(caps.appendOutput("run started"), true);
  assert.deepEqual(outputs, ["run started"]);

  caps.dispose();
  assert.equal(watcherDisposed, true);
  assert.equal(diagDisposed, true);
  assert.equal(statusBarDisposed, true);
  assert.equal(outputDisposed, true);
});

test("webview_html BRIDGE_SCRIPT exposes openSource, diagnostics and onWorkspaceDirty", async () => {
  const cjsBundle = join(fixture, "webview_html.cjs");
  await build({
    entryPoints: [join(extensionRoot, "src", "webview_html.ts")],
    bundle: true,
    platform: "node",
    format: "cjs",
    external: ["vscode"],
    outfile: cjsBundle,
  });
  const modDir = join(fixture, "mock_vs");
  await mkdir(modDir, { recursive: true });
  const req = createRequire(import.meta.url);
  const srcText = await readFile(cjsBundle, "utf8");
  const wrapped = srcText.replace('require("vscode")', "{}");
  const modExports = {};
  const runner = new Function("exports", "require", "module", "__filename", "__dirname", wrapped);
  const mod = { exports: modExports };
  runner(modExports, req, mod, cjsBundle, fixture);
  const bridgeCode = mod.exports.BRIDGE_SCRIPT;
  assert.equal(typeof bridgeCode, "string");

  const posted = [];
  const listeners = [];
  const fakeWindow = {
    __AITEST_INITIAL_CONTEXT__: { themeKind: "dark", capabilities: { openSource: true } },
    addEventListener: (name, cb) => {
      if (name === "message") listeners.push(cb);
    },
  };
  const evalBridge = new Function("window", "acquireVsCodeApi", bridgeCode);
  evalBridge(fakeWindow, () => ({
    postMessage: (m) => posted.push(m),
    getState: () => ({}),
    setState: () => {},
  }));

  const host = fakeWindow.aitestHost;
  assert.equal(typeof host.openSource, "function");
  assert.equal(typeof host.publishDiagnostics, "function");
  assert.equal(typeof host.clearDiagnostics, "function");
  assert.equal(typeof host.onWorkspaceDirty, "function");

  const dirtyEvents = [];
  const unsubscribe = host.onWorkspaceDirty((evt) => dirtyEvents.push(evt));
  for (const fn of listeners) {
    fn({ data: { type: "workspaceDirty", projectRoot: "E:/repo", changedPaths: ["src/a.py"] } });
  }
  assert.deepEqual(dirtyEvents, [{ projectRoot: "E:/repo", changedPaths: ["src/a.py"] }]);
  unsubscribe();
  for (const fn of listeners) {
    fn({ data: { type: "workspaceDirty", projectRoot: "E:/repo", changedPaths: ["src/b.py"] } });
  }
  assert.equal(dirtyEvents.length, 1);

  const openPromise = host.openSource("src/app.py", 12, 3);
  const sentOpen = posted.find((m) => m.type === "openSource");
  assert.equal(sentOpen.path, "src/app.py");
  assert.equal(sentOpen.line, 12);
  for (const fn of listeners) {
    fn({ data: { type: "result", id: sentOpen.id, ok: true, data: { path: "src/app.py", line: 12 } } });
  }
  const resolvedOpen = await openPromise;
  assert.equal(resolvedOpen.ok, true);
  assert.equal(resolvedOpen.data.line, 12);
});
