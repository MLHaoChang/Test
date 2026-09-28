import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HoldingsResponse } from "../api";
import { HoldingsTable } from "./HoldingsTable";

const HOLDINGS: HoldingsResponse = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../tests/fixtures/golden/expected/holdings_2024-12-31.json"), "utf-8"),
) as HoldingsResponse;

describe("HoldingsTable", () => {
  it("renders the golden holdings, five rows, SAP at quantity 3", () => {
    render(<HoldingsTable holdings={HOLDINGS.holdings} currency={HOLDINGS.currency} />);

    const rows = screen.getAllByRole("row");
    // One header row plus one per holding.
    expect(rows).toHaveLength(HOLDINGS.holdings.length + 1);

    const sapRow = screen.getByTestId("holdings-row-DE0007164600");
    expect(within(sapRow).getByText("SAP SE")).toBeInTheDocument();
    expect(sapRow).toHaveTextContent("3");
    expect(sapRow).toHaveTextContent("708.00 EUR");
  });

  it("shows an unknown cost as a dash rather than as a number", () => {
    const withMissingCost: HoldingsResponse["holdings"] = [{ ...HOLDINGS.holdings[0], cost_eur: null }];
    render(<HoldingsTable holdings={withMissingCost} currency="EUR" />);
    const row = screen.getByTestId(`holdings-row-${withMissingCost[0].isin}`);
    expect(within(row).getByText("-")).toBeInTheDocument();
  });

  it("shows a dash, not the ISIN a second time, for a holding no document has named (QA P0 round 2)", () => {
    // An instrument known only from a manual CSV row is stored with its ISIN as its name.
    const unnamed: HoldingsResponse["holdings"] = [
      { ...HOLDINGS.holdings[0], isin: "DE0005557508", name: "DE0005557508" },
    ];
    render(<HoldingsTable holdings={unnamed} currency="EUR" />);
    const cells = within(screen.getByTestId("holdings-row-DE0005557508")).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent(/^DE0005557508$/);
    expect(cells[1]).toHaveTextContent(/^-$/);
  });

  it("shows each holding's mapped price symbol", () => {
    render(<HoldingsTable holdings={HOLDINGS.holdings} currency={HOLDINGS.currency} />);
    const nvdaRow = screen.getByTestId("holdings-row-US67066G1040");
    expect(nvdaRow).toHaveTextContent("nvda.us");
  });

  it("renders nothing but a dash for a holding with no flags", () => {
    const noFlags: HoldingsResponse["holdings"] = [{ ...HOLDINGS.holdings[0], flags: [] }];
    render(<HoldingsTable holdings={noFlags} currency="EUR" />);
    const row = screen.getByTestId(`holdings-row-${noFlags[0].isin}`);
    expect(row).toHaveTextContent("-");
  });

  it("sits in a container that scrolls sideways on a narrow screen (QA P0 round 1, M4)", () => {
    render(<HoldingsTable holdings={HOLDINGS.holdings} currency={HOLDINGS.currency} />);
    expect(screen.getByTestId("holdings-table").parentElement).toHaveClass("table-scroll");
  });
});
