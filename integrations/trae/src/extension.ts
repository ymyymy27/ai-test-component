/** Candidate VSIX host adapter. Actual Trae edition/API compatibility is untested.
 *
 * 本文件把面板与唯一核心接通：webview 只发"要派发什么"，扩展经 `core-bridge`
 * （内部走既有 CLI 与命名管道）派发，再把**结构化回执**原样送回面板。
 * 扩展不计算业务结论、不代签确认、不复制协议。
 */
import * as vscode from "vscode";
import { randomBytes, randomUUID } from "node:crypto";

import { dispatchReadOnly, type DispatchOutcome, type DispatchRequest } from "./core-bridge";

const PANEL_VIEW_TYPE = "aitest";
const DISPATCH_TIMEOUT_MS = 30_000;

function readSettings(): { python: string; workspaceRoot: string } {
  const configuration = vscode.workspace.getConfiguration("aitest");
  return {
    python: configuration.get<string>("pythonPath", "python"),
    workspaceRoot: configuration.get<string>("workspaceRoot", ""),
  };
}

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("aitest.openPanel", () => {
      openPanel(context);
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
  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleWebviewMessage(panel, message);
  });
  context.subscriptions.push(panel);
}

function textOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
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
  };
}

async function handleWebviewMessage(panel: vscode.WebviewPanel, message: unknown): Promise<void> {
  if (typeof message !== "object" || message === null) {
    return;
  }
  const raw = message as Record<string, unknown>;
  if (raw["kind"] !== "dispatch") {
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
    });
    return;
  }
  const { python, workspaceRoot } = readSettings();
  let outcome: DispatchOutcome;
  try {
    outcome = await dispatchReadOnly(python, workspaceRoot, request, randomUUID(), DISPATCH_TIMEOUT_MS);
  } catch (error) {
    // 白名单外动作在此被拒：扩展不代签确认，也不把它降级成"可用"。
    outcome = {
      status: "refused",
      code: "ENTRY_ACTION_NOT_ALLOWED",
      message: error instanceof Error ? error.message : "该动作不能由面板发起",
    };
  }
  post(panel, { kind: "outcome", action: request.action, ...outcome });
}

function post(panel: vscode.WebviewPanel, payload: Record<string, unknown>): void {
  void panel.webview.postMessage(payload);
}

export function deactivate(): void {
  // Disposing an editor entry never means canceling a business run.
}
