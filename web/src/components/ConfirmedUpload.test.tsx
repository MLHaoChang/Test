import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type ImportDiff, uploadConfirmedHoldings } from "../api";
import { ConfirmedUpload } from "./ConfirmedUpload";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, uploadConfirmedHoldings: vi.fn() };
});

const mockUpload = vi.mocked(uploadConfirmedHoldings);

const DIFF_WITH_MATCH = {
  confirmed: { message: "5 of 5 match", all_match: true },
} as unknown as ImportDiff;

describe("ConfirmedUpload", () => {
  beforeEach(() => {
    mockUpload.mockReset();
  });

  it("describes the file layout the server reads: the header, then one line per position", () => {
    // QA P0 round 2, R2-D1: the hint used to describe a layout the server refused.
    render(<ConfirmedUpload batchId={1} onUpdated={vi.fn()} />);
    const hint = screen.getByTestId("confirmed-upload-hint");
    expect(hint).toHaveTextContent("isin;quantity;as_of");
    expect(hint).toHaveTextContent("DE0007164600;3;2024-12-31");
  });

  it("disables the button until a file is chosen", () => {
    render(<ConfirmedUpload batchId={1} onUpdated={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Upload confirmed holdings" })).toBeDisabled();
  });

  it("uploads the chosen file for the given batch and reports the updated diff", async () => {
    mockUpload.mockResolvedValueOnce(DIFF_WITH_MATCH);
    const onUpdated = vi.fn();
    render(<ConfirmedUpload batchId={7} onUpdated={onUpdated} />);

    const file = new File(["isin;quantity;as_of"], "confirmed.csv", { type: "text/csv" });
    fireEvent.change(screen.getByTestId("confirmed-holdings-input"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload confirmed holdings" }));

    await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(DIFF_WITH_MATCH));
    expect(mockUpload).toHaveBeenCalledWith(7, file);
  });

  it("shows the server's message on failure", async () => {
    mockUpload.mockRejectedValueOnce(new ApiError("isin must be 12 characters.", "invalid_request", 400));
    render(<ConfirmedUpload batchId={1} onUpdated={vi.fn()} />);

    const file = new File(["bad"], "confirmed.csv", { type: "text/csv" });
    fireEvent.change(screen.getByTestId("confirmed-holdings-input"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload confirmed holdings" }));

    await waitFor(() =>
      expect(screen.getByTestId("confirmed-upload-error")).toHaveTextContent("isin must be 12 characters."),
    );
  });
});
