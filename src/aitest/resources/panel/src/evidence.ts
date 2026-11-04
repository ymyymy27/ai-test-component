/** Evidence refs originate in core receipts; the panel never guesses a ref or revision. */
export type EvidenceRef = {
  evidenceId: string;
  revision: number;
};

export type EvidenceBodyState =
  | {kind: "body"; text: string}
  | {kind: "omitted"; text: string; reason: string | null; conflict: boolean}
  | {kind: "missing"; text: string};

type RecordLike = Record<string, unknown>;

const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/;

function asRecord(value: unknown): RecordLike | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as RecordLike
    : null;
}

function validId(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const id = value.trim();
  if (!id || id.length > 256 || CONTROL_CHARACTERS.test(id)) return null;
  return id;
}

function validRevision(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isSafeInteger(value) && value > 0 ? value : null;
  }
  if (typeof value === "string" && /^[1-9]\d*$/.test(value.trim())) {
    const revision = Number(value);
    return Number.isSafeInteger(revision) ? revision : null;
  }
  return null;
}

/** Accept only explicit evidence identities. Strings use the receipt form `id@revision`. */
export function evidenceRefOf(value: unknown): EvidenceRef | null {
  if (typeof value === "string") {
    const text = value.trim();
    const at = text.lastIndexOf("@");
    if (at <= 0) return null;
    const evidenceId = validId(text.slice(0, at));
    const revision = validRevision(text.slice(at + 1));
    return evidenceId && revision !== null ? {evidenceId, revision} : null;
  }
  const raw = asRecord(value);
  if (!raw) return null;
  const evidenceId = validId(raw["evidence_id"] ?? raw["record_id"]);
  const revision = validRevision(raw["evidence_revision"] ?? raw["revision"]);
  return evidenceId && revision !== null ? {evidenceId, revision} : null;
}

/** Deduplicate refs while preserving receipt order. Invalid refs remain non-actionable. */
export function evidenceRefsOf(value: unknown): EvidenceRef[] {
  const items = Array.isArray(value) ? value : [value];
  const refs: EvidenceRef[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    const ref = evidenceRefOf(item);
    if (!ref) continue;
    const key = `${ref.evidenceId}\u0000${ref.revision}`;
    if (seen.has(key)) continue;
    seen.add(key);
    refs.push(ref);
  }
  return refs;
}

export function evidenceRequest(ref: EvidenceRef): Record<string, unknown> {
  return {view: "evidence", record_id: ref.evidenceId, revision: ref.revision};
}

const OMITTED_REASON_TEXT: Record<string, string> = {
  redaction_unsafe: "证据正文未通过脱敏安全检查，仅展示元数据。",
  object_unavailable: "证据对象不可用：对象仓未接入或引用不合法。",
  body_too_large: "证据正文超过 4 MB，仅展示元数据。",
  object_unreadable: "证据对象读取或摘要校验失败，正文未展示。",
  object_corrupted: "证据对象大小与登记不一致，正文未展示。",
  binary_content: "该证据是二进制材料，不在正文视图中展开。",
};

export function evidenceOmissionText(reason: unknown): string {
  if (typeof reason !== "string" || reason.trim() === "") {
    return "回执未提供证据正文，也未提供省略原因（不推断）。";
  }
  const known = OMITTED_REASON_TEXT[reason];
  return known ?? `证据正文未展示，核心返回原因：${reason}。`;
}

/**
 * The core contract returns either body or a reason. If both are present the
 * receipt is contradictory, so fail closed and do not render the body.
 */
export function evidenceBodyState(view: RecordLike): EvidenceBodyState {
  const body = view["body"];
  const reason = view["body_omitted_reason"];
  const hasReason = reason !== undefined && reason !== null;
  if (typeof body === "string" && !hasReason) return {kind: "body", text: body};
  if (hasReason) {
    return {
      kind: "omitted",
      text: evidenceOmissionText(reason),
      reason: typeof reason === "string" ? reason : null,
      conflict: typeof body === "string",
    };
  }
  return {kind: "missing", text: evidenceOmissionText(null)};
}
