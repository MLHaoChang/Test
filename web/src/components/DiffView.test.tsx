import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { acceptBatch, ApiError, discardBatch, type ImportDiff } from "../api";
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
    const withConfirmed: ImportDiff = { ...IMPORT1, confirmed: { rows: [], counts: {}, all_match: true, message: "5 of 5 match" } };
    render(<DiffView diff={withConfirmed} onDiffChanged={vi.fn()} onAccepted={vi.fn()} onDiscarded={vi.fn()} />);
    expect(screen.getByTestId("confirmed-message")).toHaveTextContent("5 of 5 match");
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
});
