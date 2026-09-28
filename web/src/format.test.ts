import { describe, expect, it } from "vitest";

import { formatDate, formatMoney, formatQuantity } from "./format";

describe("formatMoney", () => {
  it("groups thousands and keeps two decimal places", () => {
    expect(formatMoney("1401.00")).toBe("1,401.00 EUR");
    expect(formatMoney("6146.85")).toBe("6,146.85 EUR");
  });

  it("shows a dash for an unknown (null) amount", () => {
    expect(formatMoney(null)).toBe("-");
  });

  it("keeps the minus sign on a negative amount", () => {
    expect(formatMoney("-1401.00")).toBe("-1,401.00 EUR");
  });

  it("uses the given currency", () => {
    expect(formatMoney("250.00", "USD")).toBe("250.00 USD");
  });

  it("shows every digit of a number beyond float precision, without parsing it as a number", () => {
    // 2^53 + 1: the smallest integer a JavaScript `Number` cannot represent exactly. If
    // formatMoney ever routed this through Number(...) or parseFloat(...), the trailing "1" would
    // silently become "0" (9007199254740992.01 instead of 9007199254740993.01).
    const huge = "9007199254740993.01";
    expect(formatMoney(huge)).toBe("9,007,199,254,740,993.01 EUR");
  });

  it("pads a single fraction digit to two places", () => {
    expect(formatMoney("24.3")).toBe("24.30 EUR");
  });

  it("formats a whole amount with no fraction", () => {
    expect(formatMoney("0")).toBe("0.00 EUR");
  });
});

describe("formatQuantity", () => {
  it("groups thousands but keeps every fraction digit", () => {
    expect(formatQuantity("2.5")).toBe("2.5");
    expect(formatQuantity("0.4534")).toBe("0.4534");
    expect(formatQuantity("1234.5")).toBe("1,234.5");
  });

  it("shows a dash for an unknown (null) quantity", () => {
    expect(formatQuantity(null)).toBe("-");
  });

  it("formats a whole share count with no fraction", () => {
    expect(formatQuantity("20")).toBe("20");
  });
});

describe("formatDate", () => {
  it("passes an ISO date through unchanged", () => {
    expect(formatDate("2024-12-31")).toBe("2024-12-31");
  });

  it("shows a dash for an unknown (null) date", () => {
    expect(formatDate(null)).toBe("-");
  });
});
