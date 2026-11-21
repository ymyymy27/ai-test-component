import { describe, expect, it } from "vitest";

import { sha256BytesSync, sha256HexBytes } from "@/services/sha256";

function bytes(values: number[]): ArrayBuffer {
  return new Uint8Array(values).buffer as ArrayBuffer;
}

describe("sha256 fallback", () => {
  it("matches known SHA-256 vectors without crypto.subtle", () => {
    expect(sha256BytesSync(bytes([]))).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
    expect(sha256BytesSync(bytes([0x61, 0x62, 0x63]))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });

  it("hashes binary bytes instead of decoding them as text", () => {
    expect(sha256BytesSync(bytes([0, 255, 1]))).toBe(
      "47ffa3ea45a70b8a41c2c0825df323c00a8b7a01c1ea06083cc41dddcc001123",
    );
  });

  it("returns the same 64-character digest through the async API", async () => {
    const digest = await sha256HexBytes(bytes([0x61, 0x62, 0x63]));
    expect(digest).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    expect(digest).toHaveLength(64);
  });
});
