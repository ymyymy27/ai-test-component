/** 轻量 Trae 扩展：激活、打开面板、命令、读取设置、调用 CLI 桥。
 *
 * 这里不保存业务状态、不计算结论、不代签确认：
 * - 面板只发“要调用哪个 CLI 命令 + 参数”；
 * - 扩展读取 `aitest.pythonPath` / `aitest.workspacePath`，经 `runtime.ts` 发现解释器；
 * - 所有业务数据由 `bridge.ts` 调 `python -m aitest.cli` 取回并原样转给面板。
 */
import { readFileSync } from "node:fs";
import { randomBytes } from "node:crypto";

import * as vscode from "vscode";

import { createBridge, type BridgeError, type BridgeResult } from "./bridge";
import { findPython, type RuntimeCheck } from "./runtime";

const VIEW_ID = "aitest.sidebar";
const PANEL_TITLE = "AI 辅助测试";

/** 面板可调用的命令白名单；新增命令先在这里显式放行。 */
const PANEL_COMMANDS: ReadonlySet<string> = new Set([
  "doctor",
  "project.add",
  "project.config",
  "project.list",
  "project.show",
  "task.add",
  "task.list",
  "task.show",
  "task.suggest",
  "plan.generate",
  "plan.show",
  "run.start",
  "run.status",
  "run.list",
  "run.cancel",
  "analysis.run",
  "evidence.list",
  "issue.list",
  "issue.update",
  "issue.close",
  "report.generate",
  "report.export",
  "report.opinion",
]);

/** 只有用户显式确认的命令才接受 `--confirm`。 */
const CONFIRMABLE_COMMANDS: ReadonlySet<string> = new Set(["run.start"]);

const COMMAND_TIMEOUT_MS: Readonly<Record<string, number>> = {
  "run.start": 60_000,
  "plan.generate": 120_000,
  "task.add": 120_000,
  "task.suggest": 60_000,
  "analysis.run": 120_000,
  "report.generate": 120_000,
  "report.export": 60_000,
  "project.config": 30_000,
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
  reveal(): void;
}

let panel: PanelHandle | null = null;
let panelReady = false;
let queued: unknown[] = [];
let runtimeCache: { key: string; value: Promise<RuntimeCheck> } | null = null;
let workspaceBinding: WorkspaceBinding = {
  projectId: "",
  projectRoot: "",
  workspaceFolderCount: 0,
};
let bindingInFlight: Promise<WorkspaceBinding> | null = null;

export function readSettings(): Settings {
  const configuration = vscode.workspace.getConfiguration("aitest");
  const python = configuration.get<unknown>("pythonPath", "");
  const workspace = configuration.get<unknown>("workspacePath", "");
  return {
    pythonPath: typeof python === "string" ? python.trim() : "",
    workspacePath: typeof workspace === "string" ? workspace.trim() : "",
  };
}

function clearRuntimeCache(): void {
  runtimeCache = null;
}

function runtimeFor(settings: Settings): Promise<RuntimeCheck> {
  const key = `${settings.pythonPath}\u0000${settings.workspacePath}`;
  if (runtimeCache === null || runtimeCache.key !== key) {
    runtimeCache = { key, value: findPython(settings) };
  }
  return runtimeCache.value;
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
    const runtime = await runtimeFor(settings);
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
  void refreshWorkspaceBinding();
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
      panel = { webview: view.webview, reveal: () => view.show?.(true) };
      view.onDidDispose(() => {
        listener.dispose();
        releasePanel(view.webview);
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
  );
}

function openPanel(context: vscode.ExtensionContext): void {
  if (!vscode.workspace.isTrusted) {
    void vscode.window.showWarningMessage("请先通过编辑器确认工作区信任。");
    return;
  }
  void refreshWorkspaceBinding();
  if (panel !== null) {
    panel.reveal();
    return;
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
  panel = {
    webview: created.webview,
    reveal: () => created.reveal(vscode.ViewColumn.One),
  };
  created.onDidDispose(() => {
    listener.dispose();
    releasePanel(created.webview);
  });
  context.subscriptions.push(created);
}

function checkEnvironment(context: vscode.ExtensionContext): void {
  openPanel(context);
  clearRuntimeCache();
  sendToPanel({ type: "command", command: "doctor" });
}

function showRunStatus(context: vscode.ExtensionContext): void {
  openPanel(context);
  sendToPanel({ type: "command", command: "runStatus" });
}

function releasePanel(webview: vscode.Webview): void {
  if (panel?.webview === webview) {
    panel = null;
    panelReady = false;
    queued = [];
  }
}

function sendToPanel(message: unknown): void {
  if (panel === null) {
    return;
  }
  if (!panelReady) {
    queued.push(message);
    return;
  }
  void panel.webview.postMessage(message);
}

async function postContext(): Promise<void> {
  const settings = readSettings();
  const binding = await refreshWorkspaceBinding();
  sendToPanel({
    type: "context",
    pythonPath: settings.pythonPath,
    workspacePath: settings.workspacePath,
    projectId: binding.projectId,
    projectRoot: binding.projectRoot,
    workspaceFolderCount: binding.workspaceFolderCount,
  });
}

function attachWebview(
  context: vscode.ExtensionContext,
  webview: vscode.Webview,
): vscode.Disposable {
  const nonce = randomBytes(24).toString("hex");
  webview.html = renderPanelHtml(context, webview, nonce);
  return webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(message, webview);
  });
}

/** 加载共享面板构建产物，把资源地址改写为宿主 URI，并先注入 bridge。 */
function renderPanelHtml(
  context: vscode.ExtensionContext,
  webview: vscode.Webview,
  nonce: string,
): string {
  try {
    const panelRoot = vscode.Uri.joinPath(context.extensionUri, "media", "panel");
    const template = readFileSync(vscode.Uri.joinPath(panelRoot, "index.html").fsPath, "utf8");
    const base = webview.asWebviewUri(panelRoot).toString().replace(/\/+$/, "");
    const csp = [
      "default-src 'none'",
      `script-src 'nonce-${nonce}' ${webview.cspSource}`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `img-src ${webview.cspSource} data:`,
      `font-src ${webview.cspSource}`,
      "connect-src 'none'",
    ].join("; ");
    const html = template
      .replace(/(?:\.\/|\/)assets\//g, `${base}/assets/`)
      .replace(/<script /g, `<script nonce="${nonce}" `)
      .replace(/ crossorigin/g, "");
    return html.replace(
      /<head>/i,
      `<head>
    <meta http-equiv="Content-Security-Policy" content="${csp}">
    <script nonce="${nonce}">${BRIDGE_SCRIPT}</script>`,
    );
  } catch {
    return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none';">
      <title>${PANEL_TITLE}</title></head><body>
      <p>面板资源缺失：media/panel/index.html 未随扩展安装。请先运行 npm run build。</p></body></html>`;
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
      return new Promise(function (resolve) {
        pending.set(id, { resolve: resolve });
        api.postMessage({
          type: "call",
          id: id,
          command: command,
          payload: payload || {},
          options: options || {}
        });
      });
    },
    openPath: function (path) {
      var id = "open-" + (++seq);
      return new Promise(function (resolve) {
        pending.set(id, { resolve: resolve });
        api.postMessage({ type: "openPath", id: id, path: path });
      });
    },
    getState: function () { return api.getState(); },
    setState: function (state) { api.setState(state); },
    getContext: function () { return context; }
  };
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

async function handleMessage(message: unknown, webview: vscode.Webview): Promise<void> {
  if (!isRecord(message)) {
    return;
  }
  if (message["type"] === "ready") {
    panelReady = true;
    await postContext();
    for (const item of queued) {
      await webview.postMessage(item);
    }
    queued = [];
    return;
  }
  if (message["type"] === "openPath") {
    const id = typeof message["id"] === "string" ? message["id"] : "";
    const path = typeof message["path"] === "string" ? message["path"].trim() : "";
    if (!id) {
      return;
    }
    if (!path) {
      await webview.postMessage({
        type: "result",
        id,
        ok: false,
        error: { code: "INVALID_PATH", message: "没有提供要打开的文件路径", next_step: "刷新证据列表后重试" },
      });
      return;
    }
    try {
      await vscode.env.openExternal(vscode.Uri.file(path));
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
  if (message["type"] !== "call") {
    return;
  }
  const id = typeof message["id"] === "string" ? message["id"] : "";
  const command = typeof message["command"] === "string" ? message["command"] : "";
  const payload = isRecord(message["payload"]) ? message["payload"] : {};
  const options = isRecord(message["options"]) ? message["options"] : {};
  const confirm = options["confirm"] === true || message["confirm"] === true;
  let result: BridgeResult;
  try {
    result = await execute(command, payload, confirm);
  } catch (error) {
    result = localError(
      "HOST_CALL_FAILED",
      error instanceof Error ? error.message : "扩展调用失败",
      "重新打开面板后再试；若仍失败请查看扩展日志",
    );
  }
  await webview.postMessage({ type: "result", id, ...result });
}

async function execute(
  command: string,
  payload: Record<string, unknown>,
  confirmRequested: boolean,
): Promise<BridgeResult> {
  if (!PANEL_COMMANDS.has(command)) {
    return localError(
      "COMMAND_NOT_ALLOWED",
      `面板不能调用命令：${command || "(空)"}`,
      "只允许面板列出的命令；如需新命令，先在扩展中放行",
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
  const runtime = await runtimeFor(settings);
  if (!runtime.ok || runtime.pythonPath === null) {
    return localError(
      "PYTHON_NOT_FOUND",
      `未找到可用的 Python 3.11+ 环境（缺项：${runtime.missing.join("、")}）`,
      runtime.installHint,
      { runtime },
    );
  }
  const bridge = createBridge({
    pythonPath: runtime.pythonPath,
    pythonArgs: runtime.pythonArgs,
    workspacePath: settings.workspacePath,
    timeoutMs: COMMAND_TIMEOUT_MS[command],
  });
  const confirm = confirmRequested && CONFIRMABLE_COMMANDS.has(command);
  return bridge.call(command, payload, { confirm });
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
  queued = [];
  panel = null;
  panelReady = false;
}
