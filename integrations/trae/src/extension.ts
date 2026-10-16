/** Candidate VSIX host adapter. Actual Trae edition/API compatibility is untested.
 *
 * 本文件把面板与唯一核心接通：webview 只发"要派发什么"，扩展经 `core-bridge`
 * （内部走既有 CLI 与命名管道）派发，再把**结构化回执**原样送回面板。
 * 扩展不计算业务结论、不代签确认、不复制协议。
 */
import * as vscode from "vscode";
import { randomBytes, randomUUID } from "node:crypto";

import { dispatchControlled, dispatchReadOnly, ensureCore, type DispatchOutcome, type DispatchRequest } from "./core-bridge";

const PANEL_VIEW_TYPE = "aitest";
const DISPATCH_TIMEOUT_MS = 30_000;

function readSettings(): { python: string; workspaceRoot: string; pythonCarriersFile: string; businessQueriesFile: string; modelConfigurationFile: string; projectId: string | null; runId: string | null } {
  const configuration = vscode.workspace.getConfiguration("aitest");
  const python = configuration.get<unknown>("pythonPath", "python");
  const workspace = configuration.get<unknown>("workspaceRoot", "");
  return {
    python: typeof python === "string" ? python : "",
    workspaceRoot: typeof workspace === "string" ? workspace : "",
    pythonCarriersFile: textOrNull(configuration.get<unknown>("pythonCarriersFile", "")) ?? "",
    businessQueriesFile: textOrNull(configuration.get<unknown>("businessQueriesFile", "")) ?? "",
    modelConfigurationFile: textOrNull(configuration.get<unknown>("modelConfigurationFile", "")) ?? "",
    projectId: identityOrNull(configuration.get<string>("projectId", "")),
    runId: identityOrNull(configuration.get<string>("runId", "")),
  };
}

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("aitest.openPanel", () => {
      openPanel(context);
    }),
    vscode.commands.registerCommand("aitest.configureEntry", async () => {
      if (!vscode.workspace.isTrusted) {
        void vscode.window.showWarningMessage("请先通过编辑器确认工作区信任。"); return;
      }
      const settings = readSettings();
      const python = await vscode.window.showInputBox({title:"AI Test 核心解释器",
        prompt:"填写已安装 AI Test 的 Python 3.13 解释器路径", value:settings.python,
        ignoreFocusOut:true});
      if (!python?.trim()) { return; }
      const folders = await vscode.window.showOpenDialog({title:"选择专用用户数据工作空间（不要选源码目录）",
        canSelectFiles:false, canSelectFolders:true, canSelectMany:false,
        ...(settings.workspaceRoot ? {defaultUri:vscode.Uri.file(settings.workspaceRoot)} : {})});
      if (!folders?.[0]) { return; }
      const project = await vscode.window.showInputBox({title:"AI Test 项目编号",
        prompt:"填写既有项目编号，或为新项目保留此新编号", value:settings.projectId ?? randomUUID(),
        ignoreFocusOut:true, validateInput:value=>identityOrNull(value) ? null : "请输入有效项目编号"});
      if (!project) { return; }
      const configuration = vscode.workspace.getConfiguration("aitest");
      await configuration.update("pythonPath", python, vscode.ConfigurationTarget.Global);
      await configuration.update("workspaceRoot", folders[0].fsPath, vscode.ConfigurationTarget.Global);
      await configuration.update("projectId", project, vscode.ConfigurationTarget.Global);
      await configuration.update("runId", "", vscode.ConfigurationTarget.Global);
      void vscode.window.showInformationMessage("入口已配置，打开 AI Test 面板连接核心后新建或读取项目。");
    }),
  );
}

function openPanel(context: vscode.ExtensionContext): void {
  if (!vscode.workspace.isTrusted) {
    void vscode.window.showWarningMessage("请先通过编辑器确认工作区信任。");
    return;
  }
  const panel = vscode.window.createWebviewPanel(PANEL_VIEW_TYPE, "AI 辅助测试", vscode.ViewColumn.One, {
    enableScripts: true,
    localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "dist")],
  });
  const script = panel.webview.asWebviewUri(
    vscode.Uri.joinPath(context.extensionUri, "dist", "panel.js"),
  );
  const nonceValue = randomBytes(24).toString("hex");
  panel.webview.html = `<!doctype html><html lang="zh-CN"><head>
      <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none';
        script-src 'nonce-${nonceValue}'; style-src 'unsafe-inline';">
      <title>AI 辅助测试</title></head><body><ai-test-panel></ai-test-panel>
      <script nonce="${nonceValue}" src="${script}"></script></body></html>`;
  const scope: PanelScope = { key: "", id: randomUUID() };
  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleWebviewMessage(panel, message, scope);
  });
  const configurationWatch = vscode.workspace.onDidChangeConfiguration(event => {
    if (event.affectsConfiguration("aitest")) { publishProject(panel, scope); }
  });
  panel.onDidDispose(() => configurationWatch.dispose());
  context.subscriptions.push(panel);
}

function textOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function identityOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() && value.length <= 128
    && !/[\u0000-\u001f]/.test(value) ? value : null;
}

interface PanelScope { key: string; id: string }

function publishProject(panel: vscode.WebviewPanel, scope: PanelScope): string {
  const settings = readSettings();
  const key = JSON.stringify(settings);
  if (key !== scope.key) { scope.key = key; scope.id = randomUUID(); }
  post(panel, { kind: "projectContext", project_id: settings.projectId ?? "",
    run_id: settings.projectId ? settings.runId : null, context_id: scope.id });
  return key;
}

/** 只接受"要派发什么"；参数必须是 JSON 对象，身份字段不补造。 */
function toDispatchRequest(raw: Record<string, unknown>): DispatchRequest | null {
  const action = textOrNull(raw["action"]);
  if (action === null) {
    return null;
  }
  const parameters = raw["parameters"];
  if (
    parameters !== undefined &&
    (typeof parameters !== "object" || parameters === null || Array.isArray(parameters))
  ) {
    return null;
  }
  return {
    action,
    parameters: (parameters as Record<string, unknown> | undefined) ?? {},
    projectId: textOrNull(raw["projectId"]),
    ...(raw["intentId"] !== undefined ? { intentId: identityOrNull(raw["intentId"]) } : {}),
    ...(raw["target"] !== undefined ? { target: identityOrNull(raw["target"]) } : {}),
    ...(raw["expectedRevision"] !== undefined ? { expectedRevision: raw["expectedRevision"] as number } : {}),
    ...(raw["bindingRevision"] !== undefined ? { bindingRevision: raw["bindingRevision"] as number } : {}),
  };
}

async function handleWebviewMessage(panel: vscode.WebviewPanel, message: unknown, scope: PanelScope): Promise<void> {
  if (!vscode.workspace.isTrusted) { return; }
  if (typeof message !== "object" || message === null) {
    return;
  }
  const raw = message as Record<string, unknown>;
  const clientRequestId = identityOrNull(raw["clientRequestId"]);
  if (clientRequestId === null) { return; }
  const expectedContext = publishProject(panel, scope);
  if (raw["kind"] === "ensureCore") {
    const { python, workspaceRoot, pythonCarriersFile, businessQueriesFile, modelConfigurationFile } = readSettings();
    const ensured = await ensureCore(python, workspaceRoot, 60_000, pythonCarriersFile, businessQueriesFile, modelConfigurationFile);
    if (JSON.stringify(readSettings()) !== expectedContext) {
      publishProject(panel, scope);
      post(panel, { kind: "outcome", action: "ensure-core", status: "unavailable",
        code: "PROJECT_CONTEXT_CHANGED", message: "连接期间宿主配置变化，请重新核对核心", clientRequestId });
      return;
    }
    post(panel, {
      kind: "outcome",
      action: "ensure-core",
      status: ensured.status,
      code: ensured.code,
      message: ensured.message,
      response: ensured.response,
      clientRequestId,
    });
    return;
  }
  if (raw["kind"] !== "dispatch" && raw["kind"] !== "controlledDispatch") {
    return;
  }
  if (raw["kind"] === "controlledDispatch" && raw["contextId"] !== scope.id) {
    post(panel, { kind: "outcome", action: textOrNull(raw["action"]), status: "refused",
      code: "PROJECT_CONTEXT_MISMATCH", message: "提交时的宿主配置已失效，请刷新后核对工作空间与操作输入", clientRequestId });
    return;
  }
  const request = toDispatchRequest(raw);
  if (request === null) {
    post(panel, {
      kind: "outcome",
      action: null,
      status: "refused",
      code: "INVALID_REQUEST",
      message: "派发请求缺少动作名，或参数不是 JSON 对象",
      clientRequestId,
    });
    return;
  }
  const { python, workspaceRoot, projectId, pythonCarriersFile, businessQueriesFile, modelConfigurationFile } = readSettings();
  if ((request.projectId !== null && request.projectId !== projectId)
      || (["query", "read_view", "check_source", "inspect_case_reuse", "copy_repair_brief"].includes(request.action) && projectId === null)) {
    post(panel, { kind: "outcome", action: request.action, status: "refused",
      code: "PROJECT_CONTEXT_MISMATCH", message: "请求与宿主所选项目不一致，请重新选择项目", clientRequestId });
    return;
  }
  let outcome: DispatchOutcome;
  try {
    // Even the first doctor request must use the configured, host-owned launcher.
    // A dispatch CLI can otherwise start an unconfigured core owned by that short-lived CLI.
    const connected = await ensureCore(python, workspaceRoot, 60_000,
      pythonCarriersFile, businessQueriesFile, modelConfigurationFile);
    if (JSON.stringify(readSettings()) !== expectedContext) {
      publishProject(panel, scope);
      post(panel, { kind: "outcome", action: request.action, status: "unavailable",
        code: "PROJECT_CONTEXT_CHANGED", message: "连接期间宿主配置变化，操作尚未派发，请重新核对", clientRequestId });
      return;
    }
    if (connected.status !== "ok") {
      post(panel, { kind: "outcome", action: request.action, ...connected, clientRequestId });
      return;
    }
    outcome = raw["kind"] === "controlledDispatch"
      ? await dispatchControlled(python, workspaceRoot, { ...request, projectId }, clientRequestId)
      : await dispatchReadOnly(python, workspaceRoot, { ...request, projectId }, clientRequestId, DISPATCH_TIMEOUT_MS);
  } catch (error) {
    // 白名单外动作在此被拒：扩展不代签确认，也不把它降级成"可用"。
    outcome = {
      status: "refused",
      code: "ENTRY_ACTION_NOT_ALLOWED",
      message: error instanceof Error ? error.message : "该动作不能由面板发起",
    };
  }
  if (JSON.stringify(readSettings()) !== expectedContext) {
    publishProject(panel, scope);
    post(panel, { kind: "outcome", action: request.action, status: "unavailable",
      code: "PROJECT_CONTEXT_CHANGED", message: "操作期间宿主上下文变化，原回执未应用；写入结果须按原意图核实", clientRequestId });
    return;
  }
  post(panel, { kind: "outcome", action: request.action, ...outcome, clientRequestId });
}

function post(panel: vscode.WebviewPanel, payload: Record<string, unknown>): void {
  void panel.webview.postMessage(payload);
}

export function deactivate(): void {
  // Disposing an editor entry never means canceling a business run.
}
