/** Candidate VSIX host adapter. Actual Trae edition/API compatibility is untested. */
import * as vscode from "vscode";
import { randomBytes } from "node:crypto";

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(vscode.commands.registerCommand("aitest.openPanel", () => {
    if (!vscode.workspace.isTrusted) {
      void vscode.window.showWarningMessage("请先通过编辑器确认工作区信任。");
      return;
    }
    const panel = vscode.window.createWebviewPanel("aitest", "AI 辅助测试",
      vscode.ViewColumn.One, { enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "dist")] });
    const script = panel.webview.asWebviewUri(
      vscode.Uri.joinPath(context.extensionUri, "dist", "panel.js"));
    const nonce = randomBytes(24).toString("hex");
    panel.webview.html = `<!doctype html><html lang="zh-CN"><head>
      <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none';
        script-src 'nonce-${nonce}'; style-src 'unsafe-inline';">
      <title>AI 辅助测试</title></head><body><ai-test-panel></ai-test-panel>
      <script nonce="${nonce}" src="${script}"></script></body></html>`;
    context.subscriptions.push(panel);
  }));
}

export function deactivate(): void {
  // Disposing an editor entry never means canceling a business run.
}
