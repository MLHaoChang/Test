import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type ImportDiff, uploadImportFiles } from "../api";
import { ImportPanel } from "./ImportPanel";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, uploadImportFiles: vi.fn() };
});

const mockUpload = vi.mocked(uploadImportFiles);

function makeFile(name: string): File {
  return new File(["content"], name, { type: "text/csv" });
}

const DIFF = { batch: { id: 1, status: "staged" } } as unknown as ImportDiff;

describe("ImportPanel", () => {
  beforeEach(() => {
    mockUpload.mockReset();
  });

  it("disables Upload until a file is chosen", () => {
    render(<ImportPanel onImported={vi.fn()} disabled={false} />);
    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
  });

  it("uploads the chosen files and reports the diff", async () => {
    mockUpload.mockResolvedValueOnce(DIFF);
    const onImported = vi.fn();
    render(<ImportPanel onImported={onImported} disabled={false} />);

    const input = screen.getByTestId("import-file-input");
    fireEvent.change(input, { target: { files: [makeFile("a.csv"), makeFile("b.pdf")] } });
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => expect(onImported).toHaveBeenCalledWith(DIFF));
    expect(mockUpload).toHaveBeenCalledTimes(1);
    expect(mockUpload.mock.calls[0][0]).toHaveLength(2);
  });

  it("shows the server's plain-English message when the upload fails", async () => {
    mockUpload.mockRejectedValueOnce(new ApiError("Name at least one file to import.", "no_files", 422));
    const onImported = vi.fn();
    render(<ImportPanel onImported={onImported} disabled={false} />);

    fireEvent.change(screen.getByTestId("import-file-input"), { target: { files: [makeFile("a.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => expect(screen.getByTestId("import-error")).toHaveTextContent("Name at least one file"));
    expect(onImported).not.toHaveBeenCalled();
  });

  it("empties the file input after an upload, so the same files can be chosen again", async () => {
    mockUpload.mockResolvedValueOnce(DIFF);
    render(<ImportPanel onImported={vi.fn()} disabled={false} />);
    const first = screen.getByTestId("import-file-input") as HTMLInputElement;

    fireEvent.change(first, { target: { files: [makeFile("tr_transactions_2024.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    await waitFor(() => expect(mockUpload).toHaveBeenCalledTimes(1));

    // A fresh, empty input replaces the one that still showed the uploaded files: Chromium fires no
    // change event for a choice identical to the input's current one, so the old input would have
    // left Upload disabled for the same files (QA P0 round 1, M1).
    await waitFor(() => expect(screen.getByTestId("import-file-input")).not.toBe(first));
    const second = screen.getByTestId("import-file-input") as HTMLInputElement;
    expect(second.files?.length ?? 0).toBe(0);
    expect(second.value).toBe("");
    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();

    fireEvent.change(second, { target: { files: [makeFile("tr_transactions_2024.csv")] } });
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();
  });

  it("keeps the chosen files after a failed upload, so Upload can simply be tried again", async () => {
    mockUpload.mockRejectedValueOnce(new ApiError("The server could not be reached.", "http_error", 502));
    render(<ImportPanel onImported={vi.fn()} disabled={false} />);
    const input = screen.getByTestId("import-file-input");

    fireEvent.change(input, { target: { files: [makeFile("a.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => expect(screen.getByTestId("import-error")).toBeInTheDocument());
    expect(screen.getByTestId("import-file-input")).toBe(input);
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();
  });

  it("disables the input and shows a hint while a batch is already staged", () => {
    render(<ImportPanel onImported={vi.fn()} disabled={true} />);
    expect(screen.getByTestId("import-file-input")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
    expect(screen.getByTestId("import-disabled-hint")).toBeInTheDocument();
  });
});
