export type RepositoryErrorCode =
  | "NOT_FOUND"
  | "FORMAT"
  | "CONFLICT"
  | "VALIDATION"
  | "STORAGE"
  | "STAGE"
  | "SCOPE_EMPTY"
  | "PLAN_STALE"
  | "CONFIRMATION_REQUIRED"
  | "RUN_ACTIVE"
  | "ISSUE_GATE";

export class RepositoryError extends Error {
  readonly code: RepositoryErrorCode;
  readonly nextStep: string;

  constructor(code: RepositoryErrorCode, message: string, nextStep = "") {
    super(message);
    this.name = "RepositoryError";
    this.code = code;
    this.nextStep = nextStep;
  }
}
