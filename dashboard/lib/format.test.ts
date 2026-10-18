import { describe, expect, it } from "vitest";
import { inrShort } from "./format";

describe("inrShort", () => {
  it("fits chart axes", () => {
    expect(inrShort(1080000)).toBe("₹10.8L");
    expect(inrShort(1000000)).toBe("₹10L");
    expect(inrShort(12500000)).toBe("₹1.25Cr");
    expect(inrShort(45000)).toBe("₹45k");
    expect(inrShort(-3200)).toBe("-₹3.2k");
    expect(inrShort(820)).toBe("₹820");
  });
});
