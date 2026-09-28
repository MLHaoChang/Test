import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Badge } from "./Badge";

describe("Badge", () => {
  it("always renders the exact no-real-orders text", () => {
    render(<Badge />);
    const badge = screen.getByTestId("no-real-orders-badge");
    expect(badge).toHaveTextContent("NO REAL ORDERS · portfolio read-only");
  });

  it("never mentions live trading", () => {
    render(<Badge />);
    expect(screen.getByTestId("no-real-orders-badge").textContent?.toLowerCase()).not.toContain("live trading");
  });
});
