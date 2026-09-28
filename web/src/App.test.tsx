import { render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  type AcceptResponse,
  acceptBatch,
  discardBatch,
  type DiscardResponse,
  getHoldings,
  getImportDiff,
  getPortfolioStatus,
  getValue,
  type HoldingsResponse,
  type ImportDiff,
  type PortfolioStatus,
  uploadImportFiles,
  type ValueResponse,
} from "./api";
import { App } from "./App";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    getPortfolioStatus: vi.fn(),
    getHoldings: vi.fn(),
    getValue: vi.fn(),
    getImportDiff: vi.fn(),
    uploadImportFiles: vi.fn(),
    acceptBatch: vi.fn(),
    discardBatch: vi.fn(),
  };
});

const mockStatus = vi.mocked(getPortfolioStatus);
const mockHoldings = vi.mocked(getHoldings);
const mockValue = vi.mocked(getValue);
const mockImportDiff = vi.mocked(getImportDiff);
const mockUpload = vi.mocked(uploadImportFiles);
const mockAccept = vi.mocked(acceptBatch);
const mockDiscard = vi.mocked(discardBatch);

// What GET /portfolio says before anything is accepted: the reminder is due, worded for the CLI.
const EMPTY_PORTFOLIO_REMINDER = {
  due: true,
  days_since_last_import: null,
  message: "Nothing has been accepted yet. Import your Trade Republic exports with: pg import FILE...",
};

function status(overrides: Partial<PortfolioStatus> = {}): PortfolioStatus {
  return {
    portfolio: { name: "Trade Republic", base_currency: "EUR", source: "trade_republic" },
    today: "2024-12-31",
    last_import_at: null,
    reminder: { due: false, days_since_last_import: null, message: "" },
    staged_batch: null,
    open_review_items: 0,
    latest_value: null,
    ...overrides,
  };
}

const EMPTY_HOLDINGS: HoldingsResponse = {
  as_of: "2024-12-31",
  currency: "EUR",
  value_eur: "0.00",
  cost_basis_eur: "0.00",
  complete: true,
  holdings: [],
};

const EMPTY_VALUE: ValueResponse = {
  currency: "EUR",
  from: null,
  to: null,
  stale_after_days: 5,
  days: 0,
  complete_days: 0,
  latest: null,
  series: [],
};

function diffFixture(overrides: Partial<ImportDiff> = {}): ImportDiff {
  return {
    batch: { id: 1, status: "staged", created_at: "2024-12-31T11:00:00Z", accepted_at: null },
    as_of: "2024-12-31",
    counts: {
      files: 1,
      duplicate_files: 0,
      candidates: 1,
      new: 1,
      merged: 0,
      already_known: 0,
      held_back: 0,
      completed: 0,
      review_new: 0,
      review_closed: 0,
    },
    files: [],
    new: [
      {
        id: 1,
        type: "buy",
        isin: "DE0007164600",
        name: "SAP SE",
        date: "2024-01-15",
        amount_eur: "-1401.00",
        quantity: "10",
      },
    ],
    merged: [],
    already_known: [],
    held_back: [],
    holdings: [],
    review_new: [],
    review_closed: [],
    confirmed: null,
    ...overrides,
  };
}

describe("App", () => {
  beforeEach(() => {
    mockStatus.mockReset();
    mockHoldings.mockReset();
    mockValue.mockReset();
    mockImportDiff.mockReset();
    mockUpload.mockReset();
    mockAccept.mockReset();
    mockDiscard.mockReset();
  });

  it("boots with an empty portfolio and shows only the badge and the import panel", async () => {
    mockStatus.mockResolvedValueOnce(status());
    mockHoldings.mockResolvedValueOnce(EMPTY_HOLDINGS);
    mockValue.mockResolvedValueOnce(EMPTY_VALUE);

    render(<App />);

    expect(screen.getByTestId("no-real-orders-badge")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("app-loading")).not.toBeInTheDocument());

    expect(screen.getByTestId("import-file-input")).toBeEnabled();
    expect(screen.queryByTestId("diff-view")).not.toBeInTheDocument();
    expect(screen.queryByTestId("holdings-table")).not.toBeInTheDocument();
  });

  it("shows the diff for a batch that was already staged before this page load", async () => {
    mockStatus.mockResolvedValueOnce(status({ staged_batch: 5 }));
    mockImportDiff.mockResolvedValueOnce(
      diffFixture({ batch: { id: 5, status: "staged", created_at: "x", accepted_at: null } }),
    );
    mockHoldings.mockResolvedValueOnce(EMPTY_HOLDINGS);
    mockValue.mockResolvedValueOnce(EMPTY_VALUE);

    render(<App />);

    await waitFor(() => expect(screen.getByTestId("diff-view")).toBeInTheDocument());
    expect(mockImportDiff).toHaveBeenCalledWith(5);
    // The import panel is disabled while a batch is staged: it must be accepted or discarded first.
    expect(screen.getByTestId("import-file-input")).toBeDisabled();
  });

  it("shows an import reminder when the API says one is due", async () => {
    mockStatus.mockResolvedValueOnce(
      status({
        reminder: { due: true, days_since_last_import: 34, message: "Import your Trade Republic exports again." },
      }),
    );
    mockHoldings.mockResolvedValueOnce(EMPTY_HOLDINGS);
    mockValue.mockResolvedValueOnce(EMPTY_VALUE);

    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("import-reminder")).toHaveTextContent("Import your Trade Republic exports again."),
    );
  });

  it("goes from upload to accept to the holdings table, without a full page reload", async () => {
    mockStatus.mockResolvedValueOnce(status());
    mockHoldings.mockResolvedValueOnce(EMPTY_HOLDINGS);
    mockValue.mockResolvedValueOnce(EMPTY_VALUE);

    render(<App />);
    await waitFor(() => expect(screen.getByTestId("import-file-input")).toBeEnabled());

    const stagedDiff = diffFixture();
    mockUpload.mockResolvedValueOnce(stagedDiff);
    fireEvent.change(screen.getByTestId("import-file-input"), {
      target: { files: [new File(["x"], "tr_transactions_2024.csv")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => expect(screen.getByTestId("diff-view")).toBeInTheDocument());
    expect(screen.getByTestId("import-file-input")).toBeDisabled();

    const acceptedHoldings: HoldingsResponse = {
      ...EMPTY_HOLDINGS,
      value_eur: "708.00",
      holdings: [
        {
          isin: "DE0007164600",
          name: "SAP SE",
          quantity: "3",
          cost_eur: "480.60",
          value_eur: "708.00",
          pricing_quantity: "3",
          price: null,
          fx: null,
          mapping: { status: "confirmed", source: "stooq", symbol: "sap.de", currency: "EUR" },
          flags: [],
        },
      ],
    };
    const acceptedValue: ValueResponse = {
      ...EMPTY_VALUE,
      from: "2024-01-02",
      to: "2024-12-31",
      series: [{ date: "2024-12-31", value_eur: "708.00", cost_basis_eur: "480.60", complete: true, flags: [] }],
    };
    mockAccept.mockResolvedValueOnce({
      batch: stagedDiff.batch,
      accepted: 1,
      held_back: 0,
      review_opened: [],
      lots: 1,
      disposals: 0,
    } as AcceptResponse);
    mockHoldings.mockResolvedValueOnce(acceptedHoldings);
    mockValue.mockResolvedValueOnce(acceptedValue);
    mockStatus.mockResolvedValueOnce(status({ last_import_at: "2024-12-31T11:00:00Z" }));

    fireEvent.click(screen.getByRole("button", { name: "Accept these transactions into my portfolio copy" }));

    await waitFor(() => expect(screen.queryByTestId("diff-view")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId("holdings-row-DE0007164600")).toBeInTheDocument());
    expect(screen.getByTestId("import-file-input")).toBeEnabled();
    expect(screen.getByTestId("value-chart")).toHaveAttribute("data-from", "2024-01-02");
  });

  // --- The reminder (QA P0 round 1, M2) ---------------------------------------------------------

  it("words the empty-portfolio reminder for the page, not as a pg command", async () => {
    mockStatus.mockResolvedValueOnce(status({ reminder: EMPTY_PORTFOLIO_REMINDER }));
    mockHoldings.mockResolvedValueOnce(EMPTY_HOLDINGS);
    mockValue.mockResolvedValueOnce(EMPTY_VALUE);

    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("import-reminder")).toHaveTextContent(
        "Nothing has been imported yet. Choose your Trade Republic files below.",
      ),
    );
    expect(screen.getByTestId("import-reminder")).not.toHaveTextContent("pg import");
  });

  it("takes the reminder away once a batch is accepted, without a reload", async () => {
    mockStatus.mockResolvedValueOnce(status({ reminder: EMPTY_PORTFOLIO_REMINDER }));
    mockHoldings.mockResolvedValueOnce(EMPTY_HOLDINGS);
    mockValue.mockResolvedValueOnce(EMPTY_VALUE);
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("import-reminder")).toBeInTheDocument());

    const stagedDiff = diffFixture();
    mockUpload.mockResolvedValueOnce(stagedDiff);
    fireEvent.change(screen.getByTestId("import-file-input"), { target: { files: [new File(["x"], "export.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    await waitFor(() => expect(screen.getByTestId("diff-view")).toBeInTheDocument());
    // While a batch waits for accept or discard, the page is already importing: no reminder to import.
    expect(screen.queryByTestId("import-reminder")).not.toBeInTheDocument();

    mockAccept.mockResolvedValueOnce({
      batch: stagedDiff.batch,
      accepted: 1,
      held_back: 0,
      review_opened: [],
      lots: 1,
      disposals: 0,
    } as AcceptResponse);
    mockHoldings.mockResolvedValueOnce(EMPTY_HOLDINGS);
    mockValue.mockResolvedValueOnce(EMPTY_VALUE);
    mockStatus.mockResolvedValueOnce(
      status({
        last_import_at: "2024-12-31T11:00:00Z",
        reminder: { due: false, days_since_last_import: 0, message: "Your last import was today." },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Accept these transactions into my portfolio copy" }));

    await waitFor(() => expect(mockStatus).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId("diff-view")).not.toBeInTheDocument());
    expect(screen.queryByTestId("import-reminder")).not.toBeInTheDocument();
  });

  it("checks the portfolio status again after a discard", async () => {
    mockStatus.mockResolvedValueOnce(status({ reminder: EMPTY_PORTFOLIO_REMINDER }));
    mockHoldings.mockResolvedValueOnce(EMPTY_HOLDINGS);
    mockValue.mockResolvedValueOnce(EMPTY_VALUE);
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("import-file-input")).toBeEnabled());

    const stagedDiff = diffFixture();
    mockUpload.mockResolvedValueOnce(stagedDiff);
    fireEvent.change(screen.getByTestId("import-file-input"), { target: { files: [new File(["x"], "export.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    await waitFor(() => expect(screen.getByTestId("diff-view")).toBeInTheDocument());

    mockDiscard.mockResolvedValueOnce({ batch: stagedDiff.batch, removed_transactions: 1 } as DiscardResponse);
    mockStatus.mockResolvedValueOnce(status({ reminder: EMPTY_PORTFOLIO_REMINDER }));
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));

    await waitFor(() => expect(mockStatus).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByTestId("import-reminder")).toHaveTextContent("Nothing has been imported yet."),
    );
  });
});
