import { describe, expect, it } from "vitest";

import { formatTick, valueTicks } from "./axis";

describe("valueTicks", () => {
  it("gives round values from zero to just above the highest value", () => {
    // The golden value series runs from 0.00 to 6,146.85 EUR.
    expect(valueTicks(0, 6146.85)).toEqual([0, 2500, 5000, 7500]);
  });

  it.each([
    [950, [0, 500, 1000]],
    [2000, [0, 1000, 2000]],
    [100000, [0, 50000, 100000]],
    [12, [0, 5, 10, 15]],
  ])("gives three or four ticks for a top value of %d", (top, ticks) => {
    expect(valueTicks(0, top)).toEqual(ticks);
  });

  it("still gives an axis when every value is zero, in whole euros", () => {
    expect(valueTicks(0, 0)).toEqual([0, 1]);
  });

  it("always starts at zero or below, so the chart keeps its zero baseline", () => {
    expect(valueTicks(4000, 6000)[0]).toBe(0);
  });
});

describe("formatTick", () => {
  it("shows a whole amount with thousands commas and the currency", () => {
    expect(formatTick(7500, "EUR")).toBe("7,500 EUR");
    expect(formatTick(0, "EUR")).toBe("0 EUR");
    expect(formatTick(1250000, "EUR")).toBe("1,250,000 EUR");
  });
});
