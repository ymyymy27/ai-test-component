/** Bounded host transport. The extension owns CLI/core identity; the panel only asks. */

import type { Outcome, ProjectContext, WizardResult, WriteOptions } from "./types";

type PendingOutcome = {
  resolve: (outcome: Outcome) => void;
  timer: number;
  action: string;
};

type PendingWizard = {
  resolve: (result: WizardResult) => void;
  timer: number;
};

const DEFAULT_READ_TIMEOUT = 30_000;
const DEFAULT_WRITE_TIMEOUT = 180_000;
const DEFAULT_CONTROLLED_TIMEOUT = 270_000;
const DEFAULT_WIZARD_TIMEOUT = 120_000;

export class HostClient {
  private readonly pendingOutcomes = new Map<string, PendingOutcome>();
  private readonly pendingWizard = new Map<string, PendingWizard>();
  private contextId = "";
  private sequence = 0;
  private readonly prefix = `panel-${Date.now().toString(36)}`;

  constructor(
    private readonly postMessage: (message: unknown) => void,
    private readonly onContext: (context: ProjectContext) => void,
    private readonly onLateOutcome: (message: string) => void,
    private readonly onHostNotice: (message: string) => void,
    private readonly onConfirmation: (requestId: string, prompt: Record<string, unknown>) => void,
  ) {
    window.addEventListener("message", this.receive);
  }

  dispose(): void {
    window.removeEventListener("message", this.receive);
    for (const pending of this.pendingOutcomes.values()) window.clearTimeout(pending.timer);
    for (const pending of this.pendingWizard.values()) window.clearTimeout(pending.timer);
    this.pendingOutcomes.clear();
    this.pendingWizard.clear();
  }

  get context(): string {
    return this.contextId;
  }

  nextId(): string {
    this.sequence += 1;
    return `${this.prefix}-${this.sequence}`;
  }

  private readonly receive = (event: MessageEvent): void => {
    const data: unknown = event.data;
    if (data === null || typeof data !== "object" || Array.isArray(data)) return;
    const raw = data as Record<string, unknown>;
    const kind = raw["kind"];
    if (kind === "projectContext") {
      const projectId = raw["project_id"];
      const runId = raw["run_id"];
      const contextId = raw["context_id"];
      if (typeof projectId !== "string" || projectId.length > 128 || /[\u0000-\u001f]/.test(projectId)) return;
      if (runId !== undefined && runId !== null && typeof runId !== "string") return;
      if (typeof contextId !== "string" || !contextId) return;
      this.contextId = contextId;
      this.onContext({
        kind: "projectContext",
        project_id: projectId,
        run_id: typeof runId === "string" ? runId : null,
        scope_name: typeof raw["scope_name"] === "string" ? raw["scope_name"] : null,
        context_id: contextId,
      });
      return;
    }
    if (kind === "runNotification") {
      const message = raw["message"];
      if (typeof message === "string" && message.trim()) this.onHostNotice(message);
      return;
    }
    if (kind === "confirmationPrompt") {
      const prompt = raw["prompt"];
      const promptId = raw["clientRequestId"];
      if (typeof promptId === "string" && promptId && prompt && typeof prompt === "object" && !Array.isArray(prompt)) {
        this.onConfirmation(promptId, prompt as Record<string, unknown>);
      }
      return;
    }
    const requestId = typeof raw["clientRequestId"] === "string" ? raw["clientRequestId"] : "";
    if (kind === "wizardResult") {
      const pending = this.pendingWizard.get(requestId);
      if (!pending) return;
      window.clearTimeout(pending.timer);
      this.pendingWizard.delete(requestId);
      pending.resolve(raw as WizardResult);
      return;
    }
    if (kind !== "outcome") return;
    const pending = this.pendingOutcomes.get(requestId);
    if (!pending) {
      const action = typeof raw["action"] === "string" ? raw["action"] : null;
      if (action && action !== "doctor" && action !== "ensure-core") {
        this.onLateOutcome(`收到一条迟到回执（${action}）。请刷新状态后核对原操作，不要直接重复执行。`);
      }
      return;
    }
    window.clearTimeout(pending.timer);
    this.pendingOutcomes.delete(requestId);
      pending.resolve(raw as unknown as Outcome);
  };

  requestOutcome(message: Record<string, unknown>, timeoutMs: number): Promise<Outcome> {
    const requestId = this.nextId();
    const action = typeof message["action"] === "string" ? message["action"] : "request";
    this.postMessage({ ...message, clientRequestId: requestId });
    return new Promise<Outcome>((resolve) => {
      const timer = window.setTimeout(() => {
        this.pendingOutcomes.delete(requestId);
        resolve({
          action,
          status: "unavailable",
          code: "ENTRY_TIMEOUT",
          message: "核心在限定时间内没有返回可核实回执。请先核对原操作，不要直接新建意图重试。",
        });
      }, timeoutMs);
      this.pendingOutcomes.set(requestId, { resolve, timer, action });
    });
  }

  requestWizard(message: Record<string, unknown>, timeoutMs = DEFAULT_WIZARD_TIMEOUT): Promise<WizardResult> {
    const requestId = this.nextId();
    this.postMessage({ ...message, clientRequestId: requestId });
    return new Promise<WizardResult>((resolve) => {
      const timer = window.setTimeout(() => {
        this.pendingWizard.delete(requestId);
        resolve({
          kind: "wizardResult",
          error: "准备操作在限定时间内没有返回结果，请核对当前状态后再继续。",
        });
      }, timeoutMs);
      this.pendingWizard.set(requestId, { resolve, timer });
    });
  }

  resolveConfirmation(requestId: string, confirmed: boolean): void {
    this.postMessage({ kind: "confirmationResult", clientRequestId: requestId, confirmed });
  }

  ensureCore(): Promise<Outcome> {
    return this.requestOutcome({ kind: "ensureCore" }, 90_000);
  }

  restartCore(): Promise<Outcome> {
    return this.requestOutcome({ kind: "restartCore" }, 150_000);
  }

  read(action: string, parameters: Record<string, unknown>, projectId: string | null): Promise<Outcome> {
    return this.requestOutcome(
      {
        kind: "dispatch",
        action,
        parameters,
        ...(projectId ? { projectId } : {}),
      },
      DEFAULT_READ_TIMEOUT,
    );
  }

  write(
    action: string,
    parameters: Record<string, unknown>,
    options: WriteOptions,
    projectId: string | null,
  ): Promise<Outcome> {
    if (!projectId) {
      return Promise.resolve({
        action,
        status: "refused",
        code: "PROJECT_CONTEXT_MISMATCH",
        message: "尚未取得宿主项目上下文，写入未派发。",
      });
    }
    if (!options.intentId) {
      return Promise.resolve({
        action,
        status: "refused",
        code: "INVALID_REQUEST",
        message: "写入缺少稳定业务意图，未派发。",
      });
    }
    if (!this.contextId) {
      return Promise.resolve({
        action,
        status: "refused",
        code: "PROJECT_CONTEXT_MISMATCH",
        message: "宿主尚未发布项目上下文，写入未派发。",
      });
    }
    return this.requestOutcome(
      {
        kind: options.controlled ? "controlledDispatch" : "writeDispatch",
        action,
        parameters,
        projectId,
        intentId: options.intentId,
        expectedRevision: options.expectedRevision ?? 0,
        ...(options.target ? { target: options.target } : {}),
        ...(typeof options.bindingRevision === "number" ? { bindingRevision: options.bindingRevision } : {}),
        contextId: this.contextId,
      },
      options.controlled ? DEFAULT_CONTROLLED_TIMEOUT : DEFAULT_WRITE_TIMEOUT,
    );
  }

  batch(
    items: ReadonlyArray<{
      action: "authorize_step";
      intentId: string;
      target: string;
      expectedRevision: number;
      parameters: Record<string, unknown>;
    }>,
  ): Promise<Outcome> {
    if (!this.contextId) {
      return Promise.resolve({
        action: "authorize_batch",
        status: "refused",
        code: "PROJECT_CONTEXT_MISMATCH",
        message: "宿主尚未发布项目上下文，批量授权未派发。",
      });
    }
    return this.requestOutcome(
      {
        kind: "nativeBatch",
        contextId: this.contextId,
        items: items.map(item => ({
          action: item.action,
          requestId: this.nextId(),
          intentId: item.intentId,
          target: item.target,
          expectedRevision: item.expectedRevision,
          parameters: item.parameters,
        })),
      },
      DEFAULT_CONTROLLED_TIMEOUT,
    );
  }

  notify(message: string): void {
    this.postMessage({ kind: "runNotification", message });
  }

  /** Fire-and-forget host command (openSettings/openDetail). */
  send(message: Record<string, unknown>): void {
    this.postMessage(message);
  }
}
