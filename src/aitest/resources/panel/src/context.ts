/** The narrow interface views use to talk to the app and the core. */

import type {
  AppState,
  Json,
  Outcome,
  Route,
  Row,
  Tone,
  WizardResult,
  WriteOptions,
} from "./types";
import type { WizardController } from "./wizard";

export interface AppContext {
  readonly state: AppState;
  readonly wizard: WizardController;
  readonly hostAvailable: boolean;

  navigate(route: Route): void;
  render(): void;

  /** Read one committed DTO view. Returns the `view` object or null on failure. */
  read(view: string, parameters?: Record<string, unknown>): Promise<Json | null>;
  /** Run one read-only action (copy_repair_brief, storage_usage, events). */
  readOnly(action: string, parameters?: Record<string, unknown>): Promise<Json | null>;
  /** Finite list query; returns rows only. */
  query(parameters: Record<string, unknown>): Promise<Row[]>;
  /** Controlled/ordinary write. The UI never signs a human confirmation. */
  write(action: string, parameters: Record<string, unknown>, options?: WriteOptions): Promise<Outcome>;
  /** Wizard messages handled by the extension host. */
  wizardRequest(kind: string, extra?: Record<string, unknown>): Promise<WizardResult>;

  ensureCore(): Promise<void>;
  restartCore(): Promise<Outcome>;
  refreshCurrent(): Promise<void>;
  loadPlans(): Promise<void>;
  loadRules(): Promise<void>;
  loadReports(): Promise<void>;
  loadIssues(): Promise<void>;
  loadDeliveries(): Promise<void>;
  loadEvents(): Promise<void>;

  modelLoad(): Promise<void>;
  modelSave(): Promise<void>;
  modelSelect(profileId: string): Promise<void>;
  modelCredential(): Promise<void>;

  intent(action: string, parameters: Record<string, unknown>, target?: string, expectedRevision?: number): string;
  setNotice(message: string, tone?: Tone): void;
  clearNotice(): void;
  showDialog(title: string, content: HTMLElement): void;
  closeDialog(): void;
  openHostSettings(): void;
  openDetail(detail: "workbench" | "review"): void;
}

export interface WizardHost {
  readonly hostAvailable: boolean;
  wizardRequest(kind: string, extra?: Record<string, unknown>): Promise<WizardResult>;
  write(action: string, parameters: Record<string, unknown>, options?: WriteOptions): Promise<Outcome>;
  restartCore(): Promise<Outcome>;
  render(): void;
  navigate(route: Route): void;
  setNotice(message: string, tone?: Tone): void;
  projectId(): string | null;
  workspaceId(): string;
  contextId(): string;
}
