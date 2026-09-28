import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen, waitFor, within } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  acceptBatch,
  ApiError,
  type ConfirmedComparison,
  discardBatch,
  type HeldBackTransaction,
  type ImportDiff,
} from "../api";
import { DiffView } from "./DiffView";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, acceptBatch: vi.fn(), discardBatch: vi.fn(), uploadConfirmedHoldings: vi.fn() };
});

const mockAccept = vi.mocked(acceptBatch);
const mockDiscard = vi.mocked(discardBatch);

// The same golden file the CLI and the API are checked against (plan 7.6 step 6), so this test
// fails the moment the diff view stops rendering what the backend actually sends.
const IMPORT1: ImportDiff = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../tests/fixtures/golden/expected/import1.json"), "utf-8"),
) as ImportDiff;

// The golden round 2 (plan 7.6 step 11): ten files already imported, the statement's eleven
// lines all already known, nothing new.
const IMPORT2: ImportDiff = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../tests/fixtures/golden/expected/import2.json"), "utf-8"),
) as ImportDiff;

// The golden confirmed-holdings comparison (plan 7.6 step 7): every row matches, the same fixture
// the API's own contract test checks (tests/api/test_contract.py::test_confirmed_holdings_accepts_a_csv_upload_and_a_json_body).
const RECONCILE_CONFIRMED: ConfirmedComparison = (
  JSON.parse(
    readFileSync(path.resolve(__dirname, "../../../tests/fixtures/golden/expected/reconcile.json"), "utf-8"),
  ) as { confirmed: ConfirmedComparison }
).confirmed;

describe("DiffView", () => {
  beforeEach(() => {
    mockAccept.mockReset();
    mockDiscard.mockReset();
  });

  it("renders the golden import1.json diff", () => {
    render(<DiffView diff={IMPORT1} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />);

    expect(screen.getByTestId("diff-summary")).toHaveTextContent("14 new transactions");
    expect(screen.getByTestId("diff-summary")).toHaveTextContent("2 need your review");

    // The new-transactions table lists SAP's first buy by name.
    expect(screen.getByTestId("diff-new-transactions")).toHaveTextContent("SAP SE");
    // The merged table shows the CSV rows that matched an already-known PDF transaction.
    expect(screen.getByTestId("diff-merged-transactions")).toHaveTextContent("tr_transactions_2024.csv");
    // Both review items' plain-English messages are shown.
    expect(screen.getByTestId("diff-review-items")).toHaveTextContent("No parser recognises this document layout");
    expect(screen.getByTestId("diff-review-items")).toHaveTextContent("were transferred in on 2024-06-20");
    // No held-back transactions in this golden batch: the table is not rendered at all.
    expect(screen.queryByTestId("diff-held-back-transactions")).not.toBeInTheDocument();

    // Every one of the five holdings the import touches, before (0) and after.
    const holdingsTable = screen.getByTestId("diff-holdings");
    expect(holdingsTable).toHaveTextContent("DE0007164600");
    expect(holdingsTable).toHaveTextContent("US67066G1040");
    expect(holdingsTable.textContent).not.toContain("live trading");
  });

  it("shows the confirmed-holdings match message once the diff carries one", () => {
    const withConfirmed: ImportDiff = { ...IMPORT1, confirmed: RECONCILE_CONFIRMED };
    render(<DiffView diff={withConfirmed} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />);
    expect(screen.getByTestId("confirmed-message")).toHaveTextContent("5 of 5 match");
    // Every row matches, but the match table (plan 5.10) is still shown, not just the count.
    const table = screen.getByTestId("diff-confirmed-rows");
    expect(table).toHaveTextContent("DE0007164600");
    expect(table).toHaveTextContent("SAP SE");
    expect(table).toHaveTextContent("match");
  });

  it("renders the confirmed-holdings comparison as a table of per-ISIN rows, mismatches included", () => {
    // The same scenario the backend's own reconciliation test checks (plan 5.3.6): one mismatch,
    // one match, one row your list names that the import does not have, and one the import has
    // that your list does not name. A user with a real mismatch needs to see all four here, on
    // the page, not just the aggregate "1 of 4 match" sentence.
    const confirmed: ConfirmedComparison = {
      rows: [
        {
          isin: "DE0007164600",
          name: "SAP SE",
          as_of: "2024-12-31",
          computed: "3",
          confirmed: "4",
          difference: "-1",
          status: "mismatch",
        },
        {
          isin: "IE00B4L5Y983",
          name: "iShsIII-Core MSCI World U.ETF",
          as_of: "2024-12-31",
          computed: "5",
          confirmed: "5",
          difference: "0",
          status: "match",
        },
        {
          isin: "US5949181045",
          name: null,
          as_of: "2024-12-31",
          computed: "0",
          confirmed: "1",
          difference: "-1",
          status: "missing_in_import",
        },
        {
          isin: "US0378331005",
          name: "Apple Inc.",
          as_of: "2024-12-31",
          computed: "5",
          confirmed: "0",
          difference: "5",
          status: "missing_in_confirmed",
        },
      ],
      counts: { match: 1, mismatch: 1, missing_in_import: 1, missing_in_confirmed: 1 },
      all_match: false,
      message: "1 of 4 match",
    };
    const withMismatch: ImportDiff = { ...IMPORT1, confirmed };
    render(<DiffView diff={withMismatch} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />);

    const table = screen.getByTestId("diff-confirmed-rows");
    const sapRow = within(table).getByText("SAP SE").closest("tr");
    if (sapRow === null) {
      throw new Error("expected a table row for SAP SE");
    }
    // The computed quantity (3), what was confirmed (4) and the difference (-1): exactly what a
    // user needs to act on the mismatch, formatted with formatQuantity like every other table here.
    expect(within(sapRow).getByText("2024-12-31")).toBeInTheDocument();
    expect(sapRow).toHaveTextContent("3");
    expect(sapRow).toHaveTextContent("4");
    expect(sapRow).toHaveTextContent("-1");
    expect(sapRow).toHaveTextContent("mismatch");
    // An ISIN the import does not know has no name: its ISIN is shown once, in the ISIN column.
    const unknownRow = within(table).getByText("US5949181045").closest("tr");
    if (unknownRow === null) {
      throw new Error("expected a table row for US5949181045");
    }
    expect(within(unknownRow).getAllByRole("cell")[1]).toHaveTextContent(/^-$/);

    expect(screen.getByTestId("confirmed-message")).toHaveTextContent("1 of 4 match");
    expect(screen.getByTestId("confirmed-message")).toHaveTextContent("Check the rows above before you accept.");
  });

  it("shows a dash, not the ISIN a second time, for an instrument no document has named (QA P0 round 2)", () => {
    // An instrument known only from a manual CSV row is stored with its ISIN as its name.
    const unnamed: ImportDiff = {
      ...IMPORT1,
      holdings: [{ isin: "DE0005557508", name: "DE0005557508", before: "0", after: "10", change: "10" }],
    };
    render(<DiffView diff={unnamed} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />);

    const row = within(screen.getByTestId("diff-holdings")).getAllByRole("row")[1];
    const cells = within(row).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent(/^DE0005557508$/);
    expect(cells[1]).toHaveTextContent(/^-$/);
  });

  it("shows a plain-English reason for a held-back transaction, not the internal review-kind slug", () => {
    const heldBack: HeldBackTransaction = {
      id: 9001,
      type: "buy",
      isin: "DE0007164600",
      name: "SAP SE",
      date: "2024-01-15",
      amount_eur: "-1401.00",
      quantity: "3",
      reasons: ["possible_duplicate", "missing_cost_basis"],
    };
    const withHeldBack: ImportDiff = { ...IMPORT1, held_back: [heldBack] };
    render(<DiffView diff={withHeldBack} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />);

    const table = screen.getByTestId("diff-held-back-transactions");
    expect(table).toHaveTextContent("possible duplicate, missing cost basis");
    expect(table.textContent).not.toContain("possible_duplicate");
    expect(table.textContent).not.toContain("missing_cost_basis");
  });

  it("accepts the batch and reports the result", async () => {
    const result = { batch: { id: 1, status: "accepted" } } as unknown as Awaited<ReturnType<typeof acceptBatch>>;
    mockAccept.mockResolvedValueOnce(result);
    const onAccepted = vi.fn();
    render(<DiffView diff={IMPORT1} onDiffChanged={vi.fn()} onAccepted={onAccepted} onDiscarded={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Accept these transactions into my portfolio copy" }));

    await waitFor(() => expect(onAccepted).toHaveBeenCalledWith(result));
    expect(mockAccept).toHaveBeenCalledWith(IMPORT1.batch.id);
  });

  it("discards the batch and reports the result", async () => {
    const result = { batch: { id: 1, status: "discarded" } } as unknown as Awaited<ReturnType<typeof discardBatch>>;
    mockDiscard.mockResolvedValueOnce(result);
    const onDiscarded = vi.fn();
    render(<DiffView diff={IMPORT1} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={onDiscarded} />);

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));

    await waitFor(() => expect(onDiscarded).toHaveBeenCalledWith(result));
  });

  it("shows a plain-English message when accept fails", async () => {
    mockAccept.mockRejectedValueOnce(new ApiError("This batch is not staged.", "batch_not_staged", 409));
    render(<DiffView diff={IMPORT1} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Accept these transactions into my portfolio copy" }));

    await waitFor(() => expect(screen.getByTestId("diff-error")).toHaveTextContent("This batch is not staged."));
  });

  // --- What the CLI and the API say, the page says too (QA P0 round 1, M3 and M4) --------------

  it("names the file of every review item", () => {
    render(<DiffView diff={IMPORT1} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />);

    const items = within(screen.getByTestId("diff-review-items")).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("unbekannt_kosteninformation.pdf: No parser recognises this document layout");
    expect(items[1]).toHaveTextContent("tr_transactions_2024.csv:");
  });

  it("counts the already-known transactions and the files skipped as already imported", () => {
    render(<DiffView diff={IMPORT2} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />);

    const summary = screen.getByTestId("diff-summary");
    expect(summary).toHaveTextContent("0 new transactions");
    expect(summary).toHaveTextContent("11 already known");
    expect(summary).toHaveTextContent("10 files already imported, skipped");
    const known = screen.getByTestId("diff-already-known-transactions");
    expect(known).toHaveTextContent("kontoauszug_2024_h1.pdf");
    expect(within(known).getAllByRole("row")).toHaveLength(IMPORT2.already_known.length + 1);
  });

  it("shows plain words, never an internal id, for the match rule and the type", () => {
    render(<DiffView diff={IMPORT1} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />);

    const merged = screen.getByTestId("diff-merged-transactions");
    expect(merged).toHaveTextContent("same day, type and amount");
    expect(merged.textContent).not.toContain("same_key");
    const added = screen.getByTestId("diff-new-transactions");
    expect(added).toHaveTextContent("transfer in");
    expect(added).toHaveTextContent("purchase");
    expect(added.textContent).not.toContain("transfer_in");
  });

  it("counts one of a kind in the singular", () => {
    const one: ImportDiff = {
      ...IMPORT1,
      counts: { ...IMPORT1.counts, new: 1, review_new: 1, duplicate_files: 1 },
    };
    render(<DiffView diff={one} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />);

    const summary = screen.getByTestId("diff-summary");
    expect(summary).toHaveTextContent("1 new transaction,");
    expect(summary).toHaveTextContent("1 needs your review");
    expect(summary).toHaveTextContent("1 file already imported, skipped");
  });

  it("puts every table in a container that scrolls sideways on a narrow screen", () => {
    const withConfirmed: ImportDiff = { ...IMPORT1, confirmed: RECONCILE_CONFIRMED };
    const { container } = render(
      <DiffView diff={withConfirmed} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />,
    );

    const tables = container.querySelectorAll("table");
    expect(tables.length).toBeGreaterThan(0);
    for (const table of Array.from(tables)) {
      expect(table.parentElement).toHaveClass("table-scroll");
    }
  });
});
