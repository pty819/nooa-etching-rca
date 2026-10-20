import { describe, expect, it, vi } from "vitest";
import { newRequestId } from "../src/requestId";

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("newRequestId", () => {
  it("uses randomUUID when available (secure context)", () => {
    const c = { randomUUID: vi.fn(() => "11111111-1111-4111-8111-111111111111"), getRandomValues: vi.fn() } as any;
    expect(newRequestId(c)).toBe("11111111-1111-4111-8111-111111111111");
    expect(c.getRandomValues).not.toHaveBeenCalled();
  });
  it("falls back to getRandomValues when randomUUID is missing (LAN HTTP)", () => {
    const c = { getRandomValues: (a: Uint8Array) => globalThis.crypto.getRandomValues(a) } as any;
    const ids = new Set(Array.from({ length: 50 }, () => newRequestId(c)));
    expect(ids.size).toBe(50);
    for (const id of ids) expect(id).toMatch(V4);
  });
});
