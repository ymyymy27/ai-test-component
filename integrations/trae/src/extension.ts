/** 轻量 Trae 扩展：激活、打开面板、命令、读取设置、调用 CLI 桥。
 *
 * 这里不保存业务状态、不计算结论、不代签确认：
 * - 面板只发“要调用哪个 CLI 命令 + 参数”；
 * - 扩展读取 `aitest.pythonPath` / `aitest.workspacePath`，经 `runtime.ts` 发现解释器；
 * - 所有业务数据由 `bridge.ts` 调 `python -m aitest.cli` 取回并原样转给面板。
 */
import { appendFileSync, readFileSync, realpathSync, renameSync, statSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { tmpdir, homedir } from "node:os";
import { isAbsolute, join, relative, resolve } from "node:path";

import * as vscode from "vscode";

import { createBridge, type BridgeError, type BridgeResult } from "./bridge";
import {
  ensureRuntime,
  findPreparedPython,
  findPython,
  defaultRuntimeHome,
  type PreparedRuntime,
  type RuntimeCheck,
  type RuntimeStageResult,
} from "./runtime";

const VIEW_ID = "aitest.sidebar";
const PANEL_TITLE = "AI 辅助测试";
const EXT_LOG = join(tmpdir(), "aitest-extension.log");
const EXT_LOG_MAX_BYTES = 1024 * 1024;
const PANEL_ERROR_MAX_CHARS = 2000;
/** 面板一次最多派生多少个 CLI 进程；轮询/快照/doctor 不再无限并行。 */
const MAX_CONCURRENT_CALLS = 4;
let logWrites = 0;

function truncate(value: unknown, limit = PANEL_ERROR_MAX_CHARS): string {
  const text = typeof value === "string" ? value : String(value ?? "");
  return text.length <= limit ? text : `${text.slice(0, limit)}…（已截断）`;
}

function logExt(event: Record<string, unknown>): void {
  try {
    logWrites += 1;
    if (logWrites % 50 === 0) {
      try {
        if (statSync(EXT_LOG).size > EXT_LOG_MAX_BYTES) {
          renameSync(EXT_LOG, `${EXT_LOG}.1`);
        }
      } catch {
        // 文件不存在或无法轮转时继续写新日志。
      }
    }
    appendFileSync(
      EXT_LOG,
      JSON.stringify({ at: new Date().toISOString(), ...event }) + "\n",
      "utf8",
    );
  } catch {
    // 诊断日志不能影响业务调用。
  }
}

/** 面板可调用的命令白名单；新增命令先在这里显式放行。 */
const PANEL_COMMANDS: ReadonlySet<string> = new Set([
  "doctor",
  "project.add",
  "project.config",
  "project.list",
  "project.map",
  "project.snapshot",
  "project.map.refresh",
  "project.show",
  "project.update",
  "task.add",
  "task.list",
  "task.show",
  "task.suggest",
  "module.suggest",
  "module.add",
  "module.update",
  "task.update",
  "task.delete",
  "delivery.draft",
  "delivery.save",
  "delivery.show",
  "template.list",
  "template.apply",
  "plan.generate",
  "plan.show",
  "plan.update",
  "run.start",
  "run.quick",
  "run.status",
  "run.list",
  "run.cancel",
  "run.manual",
  "run.repair",
  "analysis.run",
  "evidence.list",
  "evidence.add",
  "rules.save",
  "rules.list",
  "rules.show",
  "rules.import",
  "rules.export",
  "rules.bind",
  "github.read",
  "link.import",
  "link.assert",
  "issue.add",
  "issue.list",
  "issue.show",
  "issue.update",
  "issue.close",
  "report.generate",
  "report.show",
  "report.list",
  "report.export",
  "report.opinion",
  "backup.create",
  "backup.restore",
]);

/** 只有用户显式确认的命令才接受 `--confirm`。 */
const CONFIRMABLE_COMMANDS: ReadonlySet<string> = new Set([
  "run.start",
  "run.quick",
  "run.repair",
]);

const COMMAND_TIMEOUT_MS: Readonly<Record<string, number>> = {
  "run.start": 60_000,
  "run.quick": 60_000,
  "run.repair": 630_000,
  "project.map.refresh": 210_000,
  "plan.generate": 210_000,
  "task.add": 210_000,
  "task.suggest": 60_000,
  "module.suggest": 60_000,
  "delivery.draft": 210_000,
  "template.list": 30_000,
  "template.apply": 120_000,
  "analysis.run": 120_000,
  "report.generate": 330_000,
  "report.export": 60_000,
  "rules.import": 30_000,
  "rules.export": 30_000,
  "github.read": 150_000,
  "link.import": 150_000,
  "project.config": 30_000,
  "backup.create": 630_000,
  "backup.restore": 630_000,
  doctor: 30_000,
};

interface WorkspaceBinding {
  readonly projectId: string;
  readonly projectRoot: string;
  readonly workspaceFolderCount: number;
}

interface Settings {
  readonly pythonPath: string;
  readonly workspacePath: string;
}

interface PanelHandle {
  readonly webview: vscode.Webview;
  readonly kind: "sidebar" | "editor";
  reveal(): void;
}

interface WebviewState {
  ready: boolean;
  queued: unknown[];
}

/** 侧栏与编辑器面板各自保存句柄与 ready/队列，互不覆盖。 */
let sidebarPanel: PanelHandle | null = null;
let editorPanel: PanelHandle | null = null;
const webviewStates = new Map<vscode.Webview, WebviewState>();
let runtimeCache: { key: string; value: Promise<PreparedRuntime> } | null = null;
/** 自动绑定工作区只做发现，不触发便携运行时下载。 */
let discoveryCache: { key: string; value: Promise<RuntimeCheck> } | null = null;
let extensionRoot = "";
let workspaceBinding: WorkspaceBinding = {
  projectId: "",
  projectRoot: "",
  workspaceFolderCount: 0,
};
let bindingInFlight: Promise<WorkspaceBinding> | null = null;
/** 正在执行的面板调用：用于取消与同请求去重。 */
const inFlightById = new Map<
  string,
  { key: string; controller: AbortController; promise: Promise<BridgeResult> }
>();
const inFlightByKey = new Map<string, AbortController>();
let runningCalls = 0;
const waitingCalls: (() => void)[] = [];
/** 当前运行时阶段订阅者；面板心跳据此重置超时。 */
const stageListeners = new Set<(stage: RuntimeStageResult) => void>();

function defaultWorkspacePath(): string {
  const appData = (process.env.APPDATA ?? "").trim();
  const base = appData || join(homedir(), ".local", "share");
  return join(base, "aitest", "workspaces", "default");
}

export function readSettings(): Settings {
  const configuration = vscode.workspace.getConfiguration("aitest");
  const python = configuration.get<unknown>("pythonPath", "");
  const workspace = configuration.get<unknown>("workspacePath", "");
  const configured = typeof workspace === "string" ? workspace.trim() : "";
  return {
    pythonPath: typeof python === "string" ? python.trim() : "",
    workspacePath: configured || defaultWorkspacePath(),
  };
}

function clearRuntimeCache(): void {
  runtimeCache = null;
  discoveryCache = null;
}

function runtimeFor(settings: Settings): Promise<PreparedRuntime> {
  const key = `${settings.pythonPath}\u0000${settings.workspacePath}`;
  if (runtimeCache === null || runtimeCache.key !== key) {
    let created: Promise<PreparedRuntime>;
    created = ensureRuntime(settings, {
      // 便携运行时用 VSIX 自带的 wheel 离线安装；不联网取包。
      ...(extensionRoot ? { wheelDir: join(extensionRoot, "media", "runtime") } : {}),
      log: logExt,
      onStage: (stage) => {
        vscode.window.setStatusBarMessage(
          `AI 测试运行时：${stage.stage}${stage.ok ? " 完成" : " 未就绪"}`,
          5000,
        );
        // 缓存过期/损坏只是重建原因，不当成错误对话框；真正失败才提示。
        if (!stage.ok && stage.severity !== "warning" && stage.severity !== "info") {
          void vscode.window.showErrorMessage(
            `AI 测试运行时准备失败：${stage.detail ?? stage.code ?? stage.stage}`,
          );
        }
        for (const listener of stageListeners) {
          listener(stage);
        }
      },
    }).catch((error: unknown) => {
      // 失败的 Promise 不能长期缓存：改设置/重载窗口之外也应有恢复路径。
      if (runtimeCache?.value === created) {
        runtimeCache = null;
      }
      throw error;
    });
    runtimeCache = { key, value: created };
  }
  return runtimeCache.value;
}

function discoveredRuntime(settings: Settings): Promise<RuntimeCheck> {
  const key = `${settings.pythonPath}\u0000${settings.workspacePath}`;
  if (discoveryCache === null || discoveryCache.key !== key) {
    discoveryCache = {
      key,
      value: (async () => {
        const check = await findPython(settings);
        if (check.ok || settings.pythonPath) {
          return check;
        }
        // 自动绑定也要能看到已准备的便携运行时，否则无系统 Python 的机器永远空绑定。
        const prepared = findPreparedPython(defaultRuntimeHome());
        if (prepared === null) {
          return check;
        }
        return {
          ...check,
          ok: true,
          pythonPath: prepared,
          version: null,
          packageVersion: null,
          missing: [],
        } satisfies RuntimeCheck;
      })(),
    };
  }
  return discoveryCache.value;
}

function workspaceRoots(): string[] {
  const folders = vscode.workspace.workspaceFolders;
  if (!Array.isArray(folders)) {
    return [];
  }
  const roots: string[] = [];
  for (const folder of folders) {
    const fsPath = folder?.uri?.fsPath;
    if (typeof fsPath === "string" && fsPath.trim()) {
      roots.push(fsPath);
    }
  }
  return roots;
}

function normalizeRoot(value: string): string {
  const unified = value.replace(/\\/g, "/").replace(/\/+$/, "");
  return process.platform === "win32" ? unified.toLowerCase() : unified;
}

function sameRoot(left: string, right: string): boolean {
  return normalizeRoot(left) === normalizeRoot(right);
}

/** 只读取 project.list 的根目录；缺少工作空间或解释器时安静返回空。 */
async function listProjectRoots(): Promise<{ project_id: string; root: string }[]> {
  const settings = readSettings();
  if (!settings.workspacePath) {
    return [];
  }
  try {
    const runtime = await discoveredRuntime(settings);
    if (!runtime.ok || runtime.pythonPath === null) {
      return [];
    }
    const bridge = createBridge({
      pythonPath: runtime.pythonPath,
      pythonArgs: runtime.pythonArgs,
      workspacePath: settings.workspacePath,
      timeoutMs: COMMAND_TIMEOUT_MS["project.list"],
    });
    const result = await bridge.call("project.list", {}, {});
    if (!result.ok || !isRecord(result.data)) {
      return [];
    }
    const projects = result.data["projects"];
    if (!Array.isArray(projects)) {
      return [];
    }
    const roots: { project_id: string; root: string }[] = [];
    for (const item of projects) {
      if (!isRecord(item)) {
        continue;
      }
      const projectId = typeof item["project_id"] === "string" ? item["project_id"] : "";
      const root = typeof item["root"] === "string" ? item["root"] : "";
      if (projectId && root) {
        roots.push({ project_id: projectId, root });
      }
    }
    return roots;
  } catch {
    return [];
  }
}

/** 单个工作区目录才自动绑定；多目录或父目录一律留给用户选择。 */
async function resolveWorkspaceBinding(): Promise<WorkspaceBinding> {
  const roots = workspaceRoots();
  if (roots.length !== 1) {
    return { projectId: "", projectRoot: "", workspaceFolderCount: roots.length };
  }
  const only = roots[0];
  const projects = await listProjectRoots();
  const match = projects.find((project) => sameRoot(project.root, only));
  return {
    projectId: match?.project_id ?? "",
    projectRoot: only,
    workspaceFolderCount: roots.length,
  };
}

function refreshWorkspaceBinding(): Promise<WorkspaceBinding> {
  if (bindingInFlight === null) {
    bindingInFlight = resolveWorkspaceBinding()
      .then((binding) => {
        workspaceBinding = binding;
        return binding;
      })
      .catch(() => workspaceBinding)
      .finally(() => {
        bindingInFlight = null;
      });
  }
  return bindingInFlight;
}

export function activate(context: vscode.ExtensionContext): void {
  extensionRoot = context.extensionUri.fsPath;
  // 不在激活时探测项目：等面板真正请求 context 或 runStatus 命令再算绑定。
  const provider: vscode.WebviewViewProvider = {
    resolveWebviewView(view: vscode.WebviewView): void {
      if (!vscode.workspace.isTrusted) {
        view.webview.html = trustWarningHtml();
        return;
      }
      view.webview.options = {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
      };
      const listener = attachWebview(context, view.webview);
      sidebarPanel = { webview: view.webview, kind: "sidebar", reveal: () => view.show?.(true) };
      view.onDidDispose(() => {
        listener.dispose();
        releaseWebview(view.webview);
      });
    },
  };

  if (typeof vscode.window.registerWebviewViewProvider === "function") {
    context.subscriptions.push(
      vscode.window.registerWebviewViewProvider(VIEW_ID, provider, {
        webviewOptions: { retainContextWhenHidden: true },
      }),
    );
  }

  context.subscriptions.push(
    vscode.commands.registerCommand("aitest.openPanel", () => openPanel(context)),
    vscode.commands.registerCommand("aitest.checkEnvironment", () => checkEnvironment(context)),
    vscode.commands.registerCommand("aitest.runStatus", () => showRunStatus(context)),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("aitest")) {
        clearRuntimeCache();
        void postContext();
      }
    }),
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      // 多根工作区增删后，项目绑定必须重算并推给面板。
      workspaceBinding = { projectId: "", projectRoot: "", workspaceFolderCount: 0 };
      void postContext();
    }),
  );
}

function openPanel(context: vscode.ExtensionContext): void {
  if (!vscode.workspace.isTrusted) {
    void vscode.window.showWarningMessage("请先通过编辑器确认工作区信任。");
    return;
  }
  // 侧栏已经存在时直接显示，不再另建编辑器面板：两个 webview 会争同一份上下文。
  if (sidebarPanel !== null) {
    try {
      sidebarPanel.reveal();
      return;
    } catch {
      removePanel(sidebarPanel);
    }
  }
  if (editorPanel !== null) {
    try {
      editorPanel.reveal();
      return;
    } catch {
      // 面板或侧栏 webview 已被关闭，句柄可能尚未收到 dispose 回调。
      // 丢弃旧句柄后重建，避免把 “Webview is disposed” 抛给命令调用方。
      removePanel(editorPanel);
    }
  }
  const created = vscode.window.createWebviewPanel(
    "aitestPanel",
    PANEL_TITLE,
    vscode.ViewColumn.One,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
    },
  );
  const listener = attachWebview(context, created.webview);
  editorPanel = {
    webview: created.webview,
    kind: "editor",
    reveal: () => created.reveal(vscode.ViewColumn.One),
  };
  created.onDidDispose(() => {
    listener.dispose();
    releaseWebview(created.webview);
  });
  context.subscriptions.push(created);
}

async function checkEnvironment(context: vscode.ExtensionContext): Promise<void> {
  openPanel(context);
  clearRuntimeCache();
  const result = await execute("doctor", {}, false);
  if (!result.ok) {
    void vscode.window.showErrorMessage(
      `环境检查失败：${result.error?.message ?? "未知错误"}`,
    );
    return;
  }
  const data = isRecord(result.data) ? result.data : {};
  const model = isRecord(data["model"]) ? data["model"] : {};
  const github = isRecord(data["github"]) ? data["github"] : {};
  const python = isRecord(data["python"]) ? data["python"] : {};
  void vscode.window.showInformationMessage(
    `环境检查：Python ${String(python["version"] ?? "未知")}；模型 ${
      model["ok"] ? "可用" : "不可用"
    }；GitHub ${github["ok"] ? "可用" : "不可用"}`,
  );
}

async function showRunStatus(context: vscode.ExtensionContext): Promise<void> {
  openPanel(context);
  const binding = await refreshWorkspaceBinding();
  if (!binding.projectId) {
    void vscode.window.showInformationMessage("尚未绑定项目，请先在面板选择项目目录。");
    return;
  }
  const runs = await execute("run.list", { project_id: binding.projectId }, false);
  if (!runs.ok || !isRecord(runs.data)) {
    void vscode.window.showErrorMessage(
      `读取运行列表失败：${runs.error?.message ?? "未知错误"}`,
    );
    return;
  }
  const items = runs.data["runs"];
  const latest = Array.isArray(items) && items.length > 0 ? items[0] : null;
  if (!isRecord(latest)) {
    void vscode.window.showInformationMessage("该项目还没有运行记录。");
    return;
  }
  void vscode.window.showInformationMessage(
    `最近运行 ${String(latest["run_id"] ?? "")}：${String(latest["status"] ?? "未知")}`,
  );
}

function removePanel(handle: PanelHandle): void {
  if (sidebarPanel === handle) {
    sidebarPanel = null;
  }
  if (editorPanel === handle) {
    editorPanel = null;
  }
  webviewStates.delete(handle.webview);
}

function releaseWebview(webview: vscode.Webview): void {
  webviewStates.delete(webview);
  if (sidebarPanel?.webview === webview) {
    sidebarPanel = null;
  }
  if (editorPanel?.webview === webview) {
    editorPanel = null;
  }
}

function stateFor(webview: vscode.Webview): WebviewState {
  let state = webviewStates.get(webview);
  if (!state) {
    state = { ready: false, queued: [] };
    webviewStates.set(webview, state);
  }
  return state;
}

function activeWebviews(): vscode.Webview[] {
  const targets: vscode.Webview[] = [];
  if (sidebarPanel) {
    targets.push(sidebarPanel.webview);
  }
  if (editorPanel && !targets.includes(editorPanel.webview)) {
    targets.push(editorPanel.webview);
  }
  return targets;
}

function sendToWebview(webview: vscode.Webview, message: unknown): void {
  const state = stateFor(webview);
  if (!state.ready) {
    state.queued.push(message);
    return;
  }
  void Promise.resolve(webview.postMessage(message)).catch(() => {
    // webview 已销毁时 postMessage 会拒绝；只丢弃这一个句柄。
    releaseWebview(webview);
  });
}

async function postContext(): Promise<void> {
  const settings = readSettings();
  const binding = await refreshWorkspaceBinding();
  for (const webview of activeWebviews()) {
    sendToWebview(webview, {
      type: "context",
      pythonPath: settings.pythonPath,
      workspacePath: settings.workspacePath,
      projectId: binding.projectId,
      projectRoot: binding.projectRoot,
      workspaceFolderCount: binding.workspaceFolderCount,
    });
  }
}

function attachWebview(
  context: vscode.ExtensionContext,
  webview: vscode.Webview,
): vscode.Disposable {
  const nonce = randomBytes(24).toString("hex");
  // 先注册消息监听再赋 HTML：webview 可能在 html 赋值后立刻发 ready。
  const disposable = webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(message, webview);
  });
  webview.html = renderPanelHtml(context, nonce);
  return disposable;
}

/** 加载共享面板构建产物，并把脚本与样式内联进宿主 HTML。 */
function renderPanelHtml(
  context: vscode.ExtensionContext,
  nonce: string,
): string {
  try {
    const panelRoot = vscode.Uri.joinPath(context.extensionUri, "media", "panel");
    const template = readFileSync(vscode.Uri.joinPath(panelRoot, "index.html").fsPath, "utf8");
    // 扩展构建产出单文件 panel.js / panel.css。Trae 的 webview 资源 URI 与
    // cspSource 可能不同源，内联可避免 CSS/脚本被 CSP 或 URI 改写拦掉。
    const script = readFileSync(vscode.Uri.joinPath(panelRoot, "panel.js").fsPath, "utf8")
      .replace(/<\/script/gi, "<\\/script")
      .split("<!--")
      .join("<\\u0021--");
    const style = readFileSync(vscode.Uri.joinPath(panelRoot, "panel.css").fsPath, "utf8");
    const csp = [
      "default-src 'none'",
      `script-src 'nonce-${nonce}'`,
      "style-src 'unsafe-inline'",
      "img-src data:",
      "font-src data:",
      "connect-src 'none'",
    ].join("; ");
    const html = template
      .replace(/<script\b[^>]*\bsrc="[^"]*"[^>]*>\s*<\/script>/gi, "")
      .replace(/<link\b[^>]*\bhref="[^"]*\.css"[^>]*>/gi, "")
      .replace(
        /<head>/i,
        () => `<head>
    <meta http-equiv="Content-Security-Policy" content="${csp}">
    <style nonce="${nonce}">${style}</style>
    <script nonce="${nonce}">${BRIDGE_SCRIPT}</script>`,
      )
      .replace(
        "</body>",
        () => `<script nonce="${nonce}">${script}</script>\n  </body>`,
      );
    return html;
  } catch {
    return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none';">
      <title>${PANEL_TITLE}</title></head><body>
      <p>面板资源缺失：media/panel/panel.js 未随扩展安装。请先运行 npm run build。</p></body></html>`;
  }
}

/** 注入到 webview 的桥：只转发调用与状态，不解释业务结果。 */
const BRIDGE_SCRIPT = `(function () {
  var api = acquireVsCodeApi();
  var pending = new Map();
  var seq = 0;
  var context = null;
  window.addEventListener("message", function (event) {
    var message = event.data;
    if (!message) return;
    if (message.type === "context") {
      context = message;
      return;
    }
    if (message.type === "progress") {
      var active = pending.get(message.id);
      if (!active) return;
      if (typeof active.onProgress === "function") {
        try { active.onProgress(message); } catch (ignored) {}
      }
      return;
    }
    if (message.type !== "result" || !message.id) return;
    var entry = pending.get(message.id);
    if (!entry) return;
    pending.delete(message.id);
    if (message.ok) {
      entry.resolve({ ok: true, data: message.data === undefined ? null : message.data });
    } else {
      entry.resolve({
        ok: false,
        error: message.error || { code: "INTERNAL", message: "未知错误" }
      });
    }
  });
  window.aitestHost = {
    call: function (command, payload, options) {
      var id = "host-" + (++seq);
      var opts = options || {};
      return new Promise(function (resolve) {
        pending.set(id, {
          resolve: resolve,
          onProgress: typeof opts.onProgress === "function" ? opts.onProgress : null
        });
        api.postMessage({
          type: "call",
          id: id,
          command: command,
          payload: payload || {},
          options: { confirm: opts.confirm === true }
        });
      });
    },
    cancel: function (command, payload, options) {
      var opts = options || {};
      api.postMessage({
        type: "cancel",
        command: command,
        payload: payload || {},
        options: { confirm: opts.confirm === true }
      });
    },
    openPath: function (path) {
      var id = "open-" + (++seq);
      return new Promise(function (resolve) {
        pending.set(id, { resolve: resolve });
        api.postMessage({ type: "openPath", id: id, path: path });
      });
    },
    pickDirectory: function () {
      var id = "pick-" + (++seq);
      return new Promise(function (resolve) {
        pending.set(id, { resolve: resolve });
        api.postMessage({ type: "pickDirectory", id: id });
      });
    },
    getState: function () { return api.getState(); },
    setState: function (state) { api.setState(state); },
    getContext: function () { return context; }
  };
  function reportPanelError(kind, detail) {
    try {
      var text = detail && detail.message ? detail.message : detail;
      api.postMessage({ type: "panel-error", kind: kind, message: String(text || "") });
    } catch (ignored) {}
  }
  window.addEventListener("error", function (event) {
    reportPanelError("error", event.error || event.message);
  });
  window.addEventListener("unhandledrejection", function (event) {
    reportPanelError("rejection", event.reason);
  });
  api.postMessage({ type: "ready" });
})();`;

function trustWarningHtml(): string {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none';">
    <title>${PANEL_TITLE}</title></head><body>
    <p>请先通过编辑器确认工作区信任，再打开 AI 测试面板。</p></body></html>`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 可执行/脚本类文件不允许用系统默认程序直接打开。 */
const BLOCKED_OPEN_EXTENSIONS: ReadonlySet<string> = new Set([
  ".exe",
  ".bat",
  ".cmd",
  ".com",
  ".scr",
  ".msi",
  ".ps1",
  ".vbs",
  ".vbe",
  ".js",
  ".jse",
  ".wsf",
  ".wsh",
  ".jar",
  ".lnk",
  ".reg",
  ".hta",
]);

interface OpenPathDenied {
  readonly ok: false;
  readonly code: string;
  readonly message: string;
  readonly nextStep: string;
}

/**
 * 证据路径只允许落在用户数据工作空间内，且必须是普通文件。
 * 拒绝 UNC、相对路径、越界路径与可执行文件，避免把任意宿主路径交给系统打开。
 */
function resolveOpenablePath(
  raw: string,
  workspacePath: string,
): { ok: true; path: string } | OpenPathDenied {
  const text = raw.trim();
  if (!text) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "没有提供要打开的文件路径",
      nextStep: "刷新证据列表后重试",
    };
  }
  if (/^[\\/]{2}/.test(text) || /^\\\\[.?]\\/.test(text)) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "不允许打开网络共享（UNC）路径",
      nextStep: "把证据导出到工作空间后再打开",
    };
  }
  if (!isAbsolute(text)) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "只允许打开工作空间内的绝对路径",
      nextStep: "刷新证据列表后重试",
    };
  }
  const workspace = (workspacePath ?? "").trim();
  if (!workspace) {
    return {
      ok: false,
      code: "WORKSPACE_NOT_CONFIGURED",
      message: "尚未设置 aitest.workspacePath，无法校验证据路径",
      nextStep: "在设置中填写用户数据工作空间",
    };
  }
  let target: string;
  try {
    target = realpathSync(resolve(text));
  } catch {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "文件不存在或无法读取",
      nextStep: "刷新证据列表后重试",
    };
  }
  let root: string;
  try {
    root = realpathSync(resolve(workspace));
  } catch {
    root = resolve(workspace);
  }
  const rel = relative(root, target);
  const outside =
    rel === "" ||
    rel.startsWith("..") ||
    isAbsolute(rel) ||
    (process.platform === "win32" && relative(root.toLowerCase(), target.toLowerCase()).startsWith(".."));
  if (outside) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "证据路径不在用户数据工作空间内",
      nextStep: "只打开工作空间内的证据或报告",
    };
  }
  let stat;
  try {
    stat = statSync(target);
  } catch {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "文件不存在或无法读取",
      nextStep: "刷新证据列表后重试",
    };
  }
  if (!stat.isFile()) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "只能打开文件，不能打开目录",
      nextStep: "选择具体证据文件",
    };
  }
  const dot = target.lastIndexOf(".");
  const extension = dot >= 0 ? target.slice(dot).toLowerCase() : "";
  if (BLOCKED_OPEN_EXTENSIONS.has(extension)) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "不允许用系统程序直接打开可执行或脚本文件",
      nextStep: "改用文本方式查看证据内容",
    };
  }
  return { ok: true, path: target };
}

/** 稳定的请求键：去重与取消都按“命令 + 参数 + 是否确认”匹配。 */
function stableStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

function callKey(
  command: string,
  payload: Record<string, unknown>,
  confirm: boolean,
): string {
  return `${command}\u0000${confirm ? "1" : "0"}\u0000${stableStringify(payload)}`;
}

async function withCallSlot<T>(work: () => Promise<T>): Promise<T> {
  if (runningCalls >= MAX_CONCURRENT_CALLS) {
    await new Promise<void>((resolveWaiter) => waitingCalls.push(resolveWaiter));
  }
  runningCalls += 1;
  try {
    return await work();
  } finally {
    runningCalls -= 1;
    waitingCalls.shift()?.();
  }
}

function postResult(webview: vscode.Webview, id: string, result: BridgeResult): void {
  void Promise.resolve(webview.postMessage({ type: "result", id, ...result })).catch((error) => {
    logExt({
      phase: "post-failed",
      message: error instanceof Error ? error.message : String(error),
    });
  });
}

async function handleMessage(message: unknown, webview: vscode.Webview): Promise<void> {
  if (!isRecord(message)) {
    return;
  }
  if (message["type"] === "panel-error") {
    logExt({
      phase: "panel-error",
      kind: message["kind"],
      message: truncate(message["message"]),
    });
    return;
  }
  if (message["type"] === "ready") {
    logExt({ phase: "ready" });
    const state = stateFor(webview);
    state.ready = true;
    // 先冲刷 ready 之前排队的旧消息，再发最新上下文，保证面板最后看到的是当前状态。
    for (const item of state.queued) {
      await webview.postMessage(item);
    }
    state.queued = [];
    await postContext();
    return;
  }
  if (message["type"] === "pickDirectory") {
    const id = typeof message["id"] === "string" ? message["id"] : "";
    if (!id) {
      return;
    }
    try {
      const picked = await vscode.window.showOpenDialog({
        canSelectFiles: false,
        canSelectFolders: true,
        canSelectMany: false,
        openLabel: "选择项目目录",
      });
      await webview.postMessage({
        type: "result",
        id,
        ok: true,
        data: picked?.[0]?.fsPath ?? "",
      });
    } catch {
      await webview.postMessage({
        type: "result",
        id,
        ok: false,
        error: {
          code: "HOST_CALL_FAILED",
          message: "无法打开目录选择器",
          next_step: "手动输入项目绝对路径",
        },
      });
    }
    return;
  }
  if (message["type"] === "openPath") {
    const id = typeof message["id"] === "string" ? message["id"] : "";
    const raw = typeof message["path"] === "string" ? message["path"] : "";
    if (!id) {
      return;
    }
    const checked = resolveOpenablePath(raw, readSettings().workspacePath);
    if (!checked.ok) {
      await webview.postMessage({
        type: "result",
        id,
        ok: false,
        error: {
          code: checked.code,
          message: checked.message,
          next_step: checked.nextStep,
        },
      });
      return;
    }
    try {
      const opened = await vscode.env.openExternal(vscode.Uri.file(checked.path));
      if (opened === false) {
        // 系统没有关联程序时 openExternal 返回 false，不能当成成功。
        await webview.postMessage({
          type: "result",
          id,
          ok: false,
          error: {
            code: "HOST_CALL_FAILED",
            message: "系统没有可用于打开该文件的程序",
            next_step: "复制引用后在资源管理器中打开",
          },
        });
        return;
      }
      await webview.postMessage({ type: "result", id, ok: true, data: true });
    } catch {
      await webview.postMessage({
        type: "result",
        id,
        ok: false,
        error: { code: "HOST_CALL_FAILED", message: "无法用系统程序打开该文件", next_step: "复制引用后在资源管理器中打开" },
      });
    }
    return;
  }
  if (message["type"] === "cancel") {
    const command = typeof message["command"] === "string" ? message["command"] : "";
    const payload = isRecord(message["payload"]) ? message["payload"] : {};
    const options = isRecord(message["options"]) ? message["options"] : {};
    const confirm = options["confirm"] === true || message["confirm"] === true;
    const controller = inFlightByKey.get(callKey(command, payload, confirm));
    if (controller) {
      controller.abort();
      logExt({ phase: "cancel", command });
    }
    return;
  }
  if (message["type"] !== "call") {
    return;
  }
  const id = typeof message["id"] === "string" ? message["id"] : "";
  const command = typeof message["command"] === "string" ? message["command"] : "";
  const payload = isRecord(message["payload"]) ? message["payload"] : {};
  const options = isRecord(message["options"]) ? message["options"] : {};
  const confirm = options["confirm"] === true || message["confirm"] === true;
  if (!id) {
    return;
  }
  logExt({ phase: "call", id, command, keys: Object.keys(payload), confirm });

  const key = callKey(command, payload, confirm);
  const existing = inFlightByKey.get(key);
  let promise: Promise<BridgeResult>;
  if (existing) {
    // 同一请求仍在执行：复用结果，避免面板超时后重试造成重复安装/重复建任务。
    const prior = [...inFlightById.values()].find((entry) => entry.controller === existing);
    promise = prior?.promise ?? execute(command, payload, confirm, existing.signal);
    logExt({ phase: "dedupe", command });
  } else {
    const controller = new AbortController();
    inFlightByKey.set(key, controller);
    const heartbeat = setInterval(() => {
      sendToWebview(webview, { type: "progress", id, phase: "running" });
    }, 10_000);
    const onStage = (stage: RuntimeStageResult): void => {
      sendToWebview(webview, {
        type: "progress",
        id,
        phase: "runtime",
        stage: stage.stage,
        ok: stage.ok,
      });
    };
    stageListeners.add(onStage);
    promise = withCallSlot(() => execute(command, payload, confirm, controller.signal)).finally(() => {
      clearInterval(heartbeat);
      stageListeners.delete(onStage);
      inFlightById.delete(id);
      if (inFlightByKey.get(key) === controller) {
        inFlightByKey.delete(key);
      }
    });
    inFlightById.set(id, { key, controller, promise });
  }

  let result: BridgeResult;
  try {
    result = await promise;
  } catch (error) {
    logExt({
      phase: "throw",
      command,
      message: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : "",
    });
    result = localError(
      "HOST_CALL_FAILED",
      error instanceof Error ? error.message : "扩展调用失败",
      "重新打开面板后再试；若仍失败请查看扩展日志",
    );
  }
  logExt({ phase: "result", command, ok: result.ok, code: result.ok ? "" : (result.error?.code ?? "") });
  postResult(webview, id, result);
}

async function execute(
  command: string,
  payload: Record<string, unknown>,
  confirmRequested: boolean,
  signal?: AbortSignal,
): Promise<BridgeResult> {
  if (!PANEL_COMMANDS.has(command)) {
    return localError(
      "COMMAND_NOT_ALLOWED",
      `面板不能调用命令：${command || "(空)"}`,
      "只允许面板列出的命令；如需新命令，先在扩展中放行",
    );
  }
  if (!vscode.workspace.isTrusted) {
    // 信任检查不能只在 UI 入口做：execute 是唯一能派生 CLI 的路径。
    return localError(
      "UNTRUSTED_WORKSPACE",
      "当前工作区未确认信任，不能执行测试命令",
      "先在编辑器中选择“信任此工作区”",
    );
  }
  const settings = readSettings();
  if (!settings.workspacePath) {
    return localError(
      "WORKSPACE_NOT_CONFIGURED",
      "尚未设置 aitest.workspacePath（用户数据工作空间）",
      "在设置中填写工作空间目录；不要指向源码或安装目录",
    );
  }
  let runtime: PreparedRuntime;
  try {
    runtime = await runtimeFor(settings);
  } catch (error) {
    // 运行时准备异常只返回一个结构化错误，避免阶段回调再弹一次。
    logExt({
      phase: "runtime-throw",
      message: error instanceof Error ? error.message : String(error),
    });
    return localError(
      "HOST_CALL_FAILED",
      `准备 Python 运行时失败：${error instanceof Error ? error.message : "未知错误"}`,
      "检查网络/磁盘后重试，或设置 aitest.pythonPath",
    );
  }
  if (!runtime.ok || runtime.pythonPath === null) {
    const failure = runtime.failure;
    const stageNote = failure
      ? `；自动准备在 ${failure.stage} 阶段失败（${failure.code}：${failure.detail}）`
      : "";
    return localError(
      "PYTHON_NOT_FOUND",
      `未找到可用的 Python 3.11+ 环境（缺项：${runtime.missing.join("、")}）${stageNote}`,
      runtime.installHint,
      { runtime },
    );
  }
  // 运行时已可用后，工作区绑定应能看到便携解释器；清掉旧的“没有解释器”发现结果。
  discoveryCache = null;
  const bridge = createBridge({
    pythonPath: runtime.pythonPath,
    pythonArgs: runtime.pythonArgs,
    workspacePath: settings.workspacePath,
    timeoutMs: COMMAND_TIMEOUT_MS[command],
  });
  const confirm = confirmRequested && CONFIRMABLE_COMMANDS.has(command);
  return bridge.call(command, payload, { confirm, ...(signal ? { signal } : {}) });
}

function localError(
  code: string,
  message: string,
  nextStep?: string,
  details?: unknown,
): BridgeResult {
  const error: BridgeError = {
    code,
    message,
    ...(nextStep ? { next_step: nextStep } : {}),
  };
  return { ok: false, error, ...(details === undefined ? {} : { details }) };
}

export function deactivate(): void {
  // 关闭面板或扩展不取消业务运行；运行状态由 CLI 的 run.json 保存。
  webviewStates.clear();
  sidebarPanel = null;
  editorPanel = null;
  for (const controller of inFlightByKey.values()) {
    controller.abort();
  }
  inFlightByKey.clear();
  inFlightById.clear();
}
