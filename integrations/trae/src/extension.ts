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
  "report.generate",
  "report.export",
]);

/** 只有用户显式确认的命令才接受 `--confirm`。 */
const CONFIRMABLE_COMMANDS: ReadonlySet<string> = new Set(["run.start"]);

const COMMAND_TIMEOUT_MS: Readonly<Record<string, number>> = {
  "run.start": 60_000,
  "report.generate": 60_000,
  "report.export": 60_000,
  doctor: 30_000,
};

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

export function activate(context: vscode.ExtensionContext): void {
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
        postContext();
      }
    }),
  );
}

function openPanel(context: vscode.ExtensionContext): void {
  if (!vscode.workspace.isTrusted) {
    void vscode.window.showWarningMessage("请先通过编辑器确认工作区信任。");
    return;
  }
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

function postContext(): void {
  const settings = readSettings();
  sendToPanel({
    type: "context",
    pythonPath: settings.pythonPath,
    workspacePath: settings.workspacePath,
  });
}

function attachWebview(
  context: vscode.ExtensionContext,
  webview: vscode.Webview,
): vscode.Disposable {
  const nonce = randomBytes(24).toString("hex");
  const script = webview.asWebviewUri(
    vscode.Uri.joinPath(context.extensionUri, "media", "panel.js"),
  );
  webview.html = renderPanelHtml(context, webview, nonce, script.toString());
  return webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(message, webview);
  });
}

function renderPanelHtml(
  context: vscode.ExtensionContext,
  webview: vscode.Webview,
  nonce: string,
  scriptUri: string,
): string {
  try {
    const template = readFileSync(
      vscode.Uri.joinPath(context.extensionUri, "media", "panel.html").fsPath,
      "utf8",
    );
    return template
      .replaceAll("{{cspSource}}", webview.cspSource)
      .replaceAll("{{nonce}}", nonce)
      .replaceAll("{{scriptUri}}", scriptUri);
  } catch {
    return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none';">
      <title>${PANEL_TITLE}</title></head><body>
      <p>面板资源缺失：media/panel.html 未随扩展安装。</p></body></html>`;
  }
}

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
    postContext();
    for (const item of queued) {
      await webview.postMessage(item);
    }
    queued = [];
    return;
  }
  if (message["type"] !== "call") {
    return;
  }
  const id = typeof message["id"] === "string" ? message["id"] : "";
  const command = typeof message["command"] === "string" ? message["command"] : "";
  const payload = isRecord(message["payload"]) ? message["payload"] : {};
  const result = await execute(command, payload, message["confirm"] === true);
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
