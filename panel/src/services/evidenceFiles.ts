import { hashText } from "@/domain/ids";
import type { EvidenceInput } from "@/repositories/ProjectRepository";

export async function sha256Bytes(buffer: ArrayBuffer): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (subtle) {
    const digest = await subtle.digest("SHA-256", buffer);
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  return hashText(new TextDecoder().decode(new Uint8Array(buffer)))
    .padEnd(64, "0")
    .slice(0, 64);
}

export async function fileToEvidence(
  file: File,
  note: string,
  caseId = "",
): Promise<EvidenceInput> {
  const buffer = await file.arrayBuffer();
  return {
    caseId,
    name: file.name,
    mediaType: file.type || file.name.split(".").pop()?.toUpperCase() || "FILE",
    bytes: file.size,
    sha256: await sha256Bytes(buffer),
    note,
  };
}
