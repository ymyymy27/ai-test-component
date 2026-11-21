import type { EvidenceInput } from "@/repositories/ProjectRepository";
import { sha256HexBytes } from "./sha256";

export async function sha256Bytes(buffer: ArrayBuffer): Promise<string> {
  return sha256HexBytes(buffer);
}

export interface EvidenceFileLimits {
  /** 单文件上限（MB）；0 表示不限制。 */
  singleFileMb?: number;
  /** 单次媒体总量上限（MB）；0 表示不限制。 */
  mediaMb?: number;
}

export async function fileToEvidence(
  file: File,
  note: string,
  caseId = "",
  limits: EvidenceFileLimits = {},
): Promise<EvidenceInput> {
  const singleLimit = (limits.singleFileMb ?? 0) * 1024 * 1024;
  if (singleLimit > 0 && file.size > singleLimit) {
    throw new Error(`「${file.name}」超过单文件上限 ${limits.singleFileMb} MB。`);
  }
  const buffer = await file.arrayBuffer();
  return {
    caseId,
    name: file.name,
    mediaType: file.type || file.name.split(".").pop()?.toUpperCase() || "FILE",
    bytes: file.size,
    sha256: await sha256HexBytes(buffer),
    note,
  };
}

export function totalBytes(files: File[]): number {
  return files.reduce((total, file) => total + file.size, 0);
}

export function assertMediaBudget(files: File[], limits: EvidenceFileLimits): void {
  const budget = (limits.mediaMb ?? 0) * 1024 * 1024;
  if (budget > 0 && totalBytes(files) > budget) {
    throw new Error(`本次选择的文件合计超过 ${limits.mediaMb} MB。`);
  }
}
