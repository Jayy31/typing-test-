import { describe, expect, it } from "vitest";
import { freshness, marketOpen, worst } from "./fresh";
import { calibrationLabel, inr, pct } from "./format";

const at = (iso: string) => new Date(iso);

describe("freshness", () => {
  it("flags amber after 2x and red after 6x the interval", () => {
    const now = at("2026-10-15T06:00:00Z"); // Thu 11:30 IST
    const spec = { job: "macro", everyMin: 15 };
    expect(freshness(at("2026-10-15T05:50:00Z"), spec, now)).toBe("fresh");
    expect(freshness(at("2026-10-15T05:20:00Z"), spec, now)).toBe("amber");
    expect(freshness(at("2026-10-15T04:00:00Z"), spec, now)).toBe("red");
    expect(freshness(null, spec, now)).toBe("unknown");
  });
  it("does not flag market-hours jobs when the market is closed", () => {
    const sat = at("2026-10-17T06:00:00Z");
    expect(marketOpen(sat)).toBe(false);
    expect(freshness(at("2026-10-16T10:00:00Z"), { job: "trading_cycle", everyMin: 5, marketOnly: true }, sat)).toBe("fresh");
    expect(marketOpen(at("2026-10-15T03:44:00Z"))).toBe(false); // 09:14 IST
    expect(marketOpen(at("2026-10-15T03:45:00Z"))).toBe(true); // 09:15 IST
  });
  it("worst level wins", () => {
    expect(worst(["fresh", "amber", "red"])).toBe("red");
    expect(worst(["fresh"])).toBe("fresh");
  });
});

describe("format", () => {
  it("formats rupees and percents", () => {
    expect(inr(123456)).toBe("₹1,23,456");
    expect(inr(-500, true)).toBe("-₹500");
    expect(pct(2.345)).toBe("+2.3%");
  });
  it("says not yet calibrated until a calibrator exists", () => {
    expect(calibrationLabel(null, "shrink")).toBe("not yet calibrated");
    expect(calibrationLabel(3, "shrink")).toBe("not yet calibrated");
    expect(calibrationLabel(3, "platt")).toBe("calibrated (platt)");
  });
});
