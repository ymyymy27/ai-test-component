/** 轻量 Trae 扩展：激活、打开面板、命令、读取设置、调用 CLI 桥。
 *
 * 这里不保存业务状态、不计算结论、不代签确认：
 * - 面板只发“要调用哪个 CLI 命令 + 参数”；
 * - 扩展读取 `aitest.pythonPath` / `aitest.workspacePath`，经 `runtime.ts` 发现解释器；
 * - 所有业务数据由 `bridge.ts` 调 `python -m aitest.cli` 取回并原样转给面板。
 */
import { randomBytes } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";

import * as vscode from "vscode";

import { createBridge, type BridgeError, type BridgeResult } from "./bridge";
import {
  COMMAND_TIMEOUT_MS,
  CONFIRMABLE_COMMANDS,
  PANEL_COMMANDS,
  callKey,
  isRecord,
  resolveOpenablePath,
} from "./ext_commands";
import { PANEL_TITLE, logExt, truncate } from "./ext_log";
import {
  createHostCapabilities,
  type DiagnosticInput,
  type HostApiLike,
  type HostCapabilitiesController,
  type HostGitSummary,
} from "./host_capabilities";
import {
  ensureRuntime,
  findPreparedPython,
  findPython,
  defaultRuntimeHome,
  type PreparedRuntime,
  type RuntimeCheck,
  type RuntimeStageResult,
} from "./runtime";
import { renderPanelHtml, trustWarningHtml } from "./webview_html";

const VIEW_ID = "aitest.sidebar";
/** 单次并发总上限（与扩展测试契约 4 个槽位一致），保证按 FIFO 派发。 */
const MAX_CONCURRENT_CALLS = 4;
const ROOTS_CACHE_TTL_MS = 2_000;

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
interface InFlightEntry {
  readonly key: string;
  readonly controller: AbortController;
  promise: Promise<BridgeResult>;
  readonly subscribers: Map<string, vscode.Webview>;
}

/** 正在执行的面板调用：用于取消、同请求去重与进度广播。 */
const inFlightByKey = new Map<string, InFlightEntry>();
let runningCalls = 0;
const waitingCalls: (() => void)[] = [];
let rootsCache: { key: string; expiresAt: number; value: { project_id: string; root: string }[] } | null = null;
let rootsInFlight: { key: string; promise: Promise<{ project_id: string; root: string }[]> } | null = null;
/** 当前运行时阶段订阅者；面板心跳据此重置超时。 */
const stageListeners = new Set<(stage: RuntimeStageResult) => void>();
let hostCapabilities: HostCapabilitiesController | null = null;
let watchedProjectRoot = "";
let latestHostGit: HostGitSummary | null = null;
let latestDiagCount = 0;
let activeStatusNote = "";

function refreshStatusBar(stateLabel?: string): void {
  if (stateLabel !== undefined) {
    activeStatusNote = stateLabel;
  }
  const parts: string[] = ["$(beaker) AI 测试"];
  if (latestHostGit?.branch) {
    parts.push(`${latestHostGit.branch}${latestHostGit.dirtyCount > 0 ? "*" : ""}`);
  }
  if (activeStatusNote) {
    parts.push(activeStatusNote);
  }
  if (latestDiagCount > 0) {
    parts.push(`问题红线 ${latestDiagCount}`);
  }
  const tooltip = [
    latestHostGit?.branch ? `分支：${latestHostGit.branch}` : "",
    latestHostGit ? `未提交改动：${latestHostGit.dirtyCount}` : "",
    activeStatusNote ? `状态：${activeStatusNote}` : "",
    latestDiagCount > 0 ? `问题红线：${latestDiagCount} 条` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  getHostCapabilities().updateStatusBar(parts.join(" · "), tooltip || "打开 AI 测试面板");
}

function getHostCapabilities(context?: vscode.ExtensionContext): HostCapabilitiesController {
  if (hostCapabilities === null) {
    hostCapabilities = createHostCapabilities(vscode as unknown as HostApiLike, context);
  }
  return hostCapabilities;
}

function resolveSourceRoot(): string {
  return workspaceBinding.projectRoot || workspaceRoots()[0] || "";
}

function syncWorkspaceWatcher(projectRoot: string): void {
  if (projectRoot === watchedProjectRoot) {
    return;
  }
  watchedProjectRoot = projectRoot;
  getHostCapabilities().watchWorkspace(projectRoot, (changedPaths) => {
    for (const webview of activeWebviews()) {
      sendToWebview(webview, {
        type: "workspaceDirty",
        projectRoot,
        changedPaths: changedPaths.slice(0, 50),
      });
    }
  });
}

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
  rootsCache = null;
  rootsInFlight = null;
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

/** 只读取 project.list 的根目录；内置 2 秒缓存与并发去重，缺少工作空间或解释器时安静返回空。 */
async function listProjectRoots(): Promise<{ project_id: string; root: string }[]> {
  const settings = readSettings();
  if (!settings.workspacePath) {
    return [];
  }
  const cacheKey = `${settings.pythonPath}\u0000${settings.workspacePath}`;
  if (rootsCache && rootsCache.key === cacheKey && Date.now() < rootsCache.expiresAt) {
    return rootsCache.value;
  }
  if (rootsInFlight && rootsInFlight.key === cacheKey) {
    return rootsInFlight.promise;
  }
  const loader = (async (): Promise<{ project_id: string; root: string }[]> => {
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
      rootsCache = { key: cacheKey, expiresAt: Date.now() + ROOTS_CACHE_TTL_MS, value: roots };
      return roots;
    } catch {
      return [];
    } finally {
      if (rootsInFlight?.key === cacheKey) {
        rootsInFlight = null;
      }
    }
  })();
  rootsInFlight = { key: cacheKey, promise: loader };
  return loader;
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
  hostCapabilities?.dispose();
  hostCapabilities = null;
  watchedProjectRoot = "";
  const caps = getHostCapabilities(context);
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

  const optionalSubscriptions: vscode.Disposable[] = [];
  const winWithTheme = vscode.window as unknown as {
    onDidChangeActiveColorTheme?: (cb: () => void) => vscode.Disposable;
  };
  if (typeof winWithTheme.onDidChangeActiveColorTheme === "function") {
    optionalSubscriptions.push(
      winWithTheme.onDidChangeActiveColorTheme(() => {
        postTheme();
      }),
    );
  }
  const wsWithTrust = vscode.workspace as unknown as {
    onDidGrantWorkspaceTrust?: (cb: () => void) => vscode.Disposable;
  };
  if (typeof wsWithTrust.onDidGrantWorkspaceTrust === "function") {
    optionalSubscriptions.push(
      wsWithTrust.onDidGrantWorkspaceTrust(() => {
        void postContext();
      }),
    );
  }

  context.subscriptions.push(
    {
      dispose: () => {
        caps.dispose();
        if (hostCapabilities === caps) {
          hostCapabilities = null;
          watchedProjectRoot = "";
        }
      },
    },
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
      // 多根工作区增删后，项目绑定与根目录缓存必须立即重算并推给面板。
      rootsCache = null;
      rootsInFlight = null;
      workspaceBinding = { projectId: "", projectRoot: "", workspaceFolderCount: 0 };
      void postContext();
    }),
    ...optionalSubscriptions,
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
    const index = context.subscriptions.indexOf(created);
    if (index >= 0) {
      context.subscriptions.splice(index, 1);
    }
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

function currentThemeKind(): "dark" | "light" {
  const kind = (
    vscode.window as unknown as { activeColorTheme?: { kind?: number } }
  )?.activeColorTheme?.kind;
  return kind === 2 || kind === 3 ? "dark" : "light";
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

async function safePost(webview: vscode.Webview, message: unknown): Promise<void> {
  try {
    await webview.postMessage(message);
  } catch {
    releaseWebview(webview);
  }
}

function postTheme(): void {
  const theme = currentThemeKind();
  for (const webview of activeWebviews()) {
    sendToWebview(webview, { type: "theme", theme });
  }
}

async function postContext(): Promise<void> {
  const settings = readSettings();
  const binding = await refreshWorkspaceBinding();
  const themeKind = currentThemeKind();
  const caps = getHostCapabilities();
  const capabilities = caps.detectCapabilities();
  const hostGit = binding.projectRoot ? await caps.readGitSummary(binding.projectRoot) : null;
  latestHostGit = hostGit;
  syncWorkspaceWatcher(binding.projectRoot);
  refreshStatusBar();
  for (const webview of activeWebviews()) {
    sendToWebview(webview, {
      type: "context",
      pythonPath: settings.pythonPath,
      workspacePath: settings.workspacePath,
      projectId: binding.projectId,
      projectRoot: binding.projectRoot,
      workspaceFolderCount: binding.workspaceFolderCount,
      themeKind,
      capabilities: { ...capabilities },
      hostGit: hostGit ? { ...hostGit } : null,
    });
  }
}

function attachWebview(
  context: vscode.ExtensionContext,
  webview: vscode.Webview,
): vscode.Disposable {
  const nonce = randomBytes(24).toString("hex");
  const settings = readSettings();
  const initialContext = {
    pythonPath: settings.pythonPath,
    workspacePath: settings.workspacePath,
    projectId: workspaceBinding.projectId,
    projectRoot: workspaceBinding.projectRoot,
    workspaceFolderCount: workspaceBinding.workspaceFolderCount,
    themeKind: currentThemeKind(),
    capabilities: { ...getHostCapabilities(context).detectCapabilities() },
    hostGit: null,
  };
  // 先注册消息监听再赋 HTML：webview 可能在 html 赋值后立刻发 ready。
  const disposable = webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(message, webview);
  });
  webview.html = renderPanelHtml(context.extensionUri, nonce, initialContext);
  return disposable;
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
      await safePost(webview, item);
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
      await safePost(webview, {
        type: "result",
        id,
        ok: true,
        data: picked?.[0]?.fsPath ?? "",
      });
    } catch {
      await safePost(webview, {
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
      await safePost(webview, {
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
        await safePost(webview, {
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
      await safePost(webview, { type: "result", id, ok: true, data: true });
    } catch {
      await safePost(webview, {
        type: "result",
        id,
        ok: false,
        error: { code: "HOST_CALL_FAILED", message: "无法用系统程序打开该文件", next_step: "复制引用后在资源管理器中打开" },
      });
    }
    return;
  }
  if (message["type"] === "openSource") {
    const id = typeof message["id"] === "string" ? message["id"] : "";
    const filePath =
      typeof message["path"] === "string"
        ? message["path"]
        : typeof message["file"] === "string"
          ? message["file"]
          : "";
    const line = typeof message["line"] === "number" ? message["line"] : 1;
    const column = typeof message["column"] === "number" ? message["column"] : 1;
    if (!workspaceBinding.projectRoot && workspaceRoots().length === 1) {
      await refreshWorkspaceBinding();
    }
    const result = await getHostCapabilities().jumpToSource(
      resolveSourceRoot(),
      filePath,
      line,
      column,
    );
    if (id) {
      if (result.ok) {
        await safePost(webview, { type: "result", id, ok: true, data: result });
      } else {
        await safePost(webview, {
          type: "result",
          id,
          ok: false,
          error: {
            code: result.code,
            message: result.message,
            next_step: result.nextStep,
          },
        });
      }
    }
    return;
  }
  if (message["type"] === "publishDiagnostics") {
    const id = typeof message["id"] === "string" ? message["id"] : "";
    const rawItems = Array.isArray(message["items"]) ? message["items"] : [];
    const items: DiagnosticInput[] = rawItems
      .filter((item): item is Record<string, unknown> => isRecord(item) && typeof item["file"] === "string" && typeof item["message"] === "string")
      .map((item) => ({
        file: String(item["file"]),
        message: String(item["message"]),
        ...(typeof item["line"] === "number" ? { line: item["line"] } : {}),
        ...(typeof item["endLine"] === "number" ? { endLine: item["endLine"] } : {}),
        ...(item["severity"] === "error" || item["severity"] === "warning" || item["severity"] === "info"
          ? { severity: item["severity"] }
          : {}),
        ...(typeof item["code"] === "string" ? { code: item["code"] } : {}),
      }));
    const published = getHostCapabilities().publishDiagnostics(resolveSourceRoot(), items);
    latestDiagCount = published.count;
    refreshStatusBar();
    if (id) {
      await safePost(webview, { type: "result", id, ok: true, data: published });
    }
    return;
  }
  if (message["type"] === "clearDiagnostics") {
    const id = typeof message["id"] === "string" ? message["id"] : "";
    getHostCapabilities().clearDiagnostics();
    latestDiagCount = 0;
    refreshStatusBar();
    if (id) {
      await safePost(webview, { type: "result", id, ok: true, data: true });
    }
    return;
  }
  if (message["type"] === "cancel") {
    const command = typeof message["command"] === "string" ? message["command"] : "";
    const payload = isRecord(message["payload"]) ? message["payload"] : {};
    const options = isRecord(message["options"]) ? message["options"] : {};
    const confirm = options["confirm"] === true || message["confirm"] === true;
    const entry = inFlightByKey.get(callKey(command, payload, confirm));
    if (entry) {
      entry.controller.abort();
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
    // 同一请求仍在执行：复用结果并同步接收进度心跳，避免面板超时后重试造成重复执行。
    existing.subscribers.set(id, webview);
    promise = existing.promise.finally(() => {
      existing.subscribers.delete(id);
    });
    logExt({ phase: "dedupe", command });
  } else {
    const controller = new AbortController();
    const subscribers = new Map<string, vscode.Webview>([[id, webview]]);
    const entry: InFlightEntry = {
      key,
      controller,
      promise: Promise.resolve({ ok: true, data: null }),
      subscribers,
    };
    inFlightByKey.set(key, entry);
    promise = withCallSlot(async () => {
      // 仅当真正获得并发执行槽位开始运行 CLI 时，才启动进度心跳与阶段广播。
      const heartbeat = setInterval(() => {
        for (const [subId, subView] of entry.subscribers) {
          sendToWebview(subView, { type: "progress", id: subId, phase: "running" });
        }
      }, 10_000);
      const onStage = (stage: RuntimeStageResult): void => {
        for (const [subId, subView] of entry.subscribers) {
          sendToWebview(subView, {
            type: "progress",
            id: subId,
            phase: "runtime",
            stage: stage.stage,
            ok: stage.ok,
          });
        }
      };
      const onCliProgress = (event: Record<string, unknown>): void => {
        const phase = typeof event["phase"] === "string" ? event["phase"] : "running";
        const msgText = typeof event["message"] === "string" ? event["message"] : "";
        getHostCapabilities().appendOutput(
          `[aitest][${command}] ${phase}${msgText ? `：${truncate(msgText, 240)}` : ""}`,
        );
        for (const [subId, subView] of entry.subscribers) {
          sendToWebview(subView, {
            ...event,
            type: "progress",
            id: subId,
            phase,
          });
        }
      };
      stageListeners.add(onStage);
      if (command === "run.start" || command === "run.quick") {
        getHostCapabilities().appendOutput(`[aitest][${command}] 开始执行...`);
        refreshStatusBar("运行中");
      }
      try {
        return await execute(command, payload, confirm, controller.signal, onCliProgress);
      } finally {
        clearInterval(heartbeat);
        stageListeners.delete(onStage);
      }
    }).finally(() => {
      entry.subscribers.delete(id);
      if (inFlightByKey.get(key) === entry) {
        inFlightByKey.delete(key);
      }
    });
    entry.promise = promise;
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
  if (command === "run.start" || command === "run.quick") {
    getHostCapabilities().appendOutput(
      `[aitest][${command}] 执行结束：${result.ok ? "成功" : `失败 (${result.error?.code ?? ""})`}`,
    );
    refreshStatusBar(result.ok ? "就绪" : "异常");
  }
  postResult(webview, id, result);
}

async function execute(
  command: string,
  payload: Record<string, unknown>,
  confirmRequested: boolean,
  signal?: AbortSignal,
  onProgress?: (event: Record<string, unknown>) => void,
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
  if (runtime.source === "portable-new") {
    // 刚新建便携运行时后，工作区绑定应能看到新解释器；此时才清掉旧发现缓存。
    discoveryCache = null;
  }
  const bridge = createBridge({
    pythonPath: runtime.pythonPath,
    pythonArgs: runtime.pythonArgs,
    workspacePath: settings.workspacePath,
    timeoutMs: COMMAND_TIMEOUT_MS[command],
  });
  const confirm = confirmRequested && CONFIRMABLE_COMMANDS.has(command);
  const result = await bridge.call(command, payload, {
    confirm,
    ...(signal ? { signal } : {}),
    ...(onProgress ? { onProgress } : {}),
  });
  if (
    result.ok &&
    (command === "project.add" ||
      command === "project.update" ||
      command === "backup.restore" ||
      command === "data.cleanup")
  ) {
    rootsCache = null;
  }
  return result;
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
  hostCapabilities?.dispose();
  hostCapabilities = null;
  watchedProjectRoot = "";
  latestHostGit = null;
  latestDiagCount = 0;
  activeStatusNote = "";
  webviewStates.clear();
  sidebarPanel = null;
  editorPanel = null;
  for (const entry of inFlightByKey.values()) {
    entry.controller.abort();
  }
  inFlightByKey.clear();
}
