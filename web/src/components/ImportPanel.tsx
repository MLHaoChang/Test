import { useState } from "react";

import { ApiError, type ImportDiff, uploadImportFiles } from "../api";

export interface ImportPanelProps {
  /** Called with the new batch's diff once the upload is staged. */
  onImported: (diff: ImportDiff) => void;
  /** True while a batch is already staged: accept or discard it before importing more. */
  disabled: boolean;
}

/**
 * Upload: a file input for several CSV and PDF files and an Upload button (plan 5.10),
 * `POST /portfolio/imports`.
 */
export function ImportPanel({ onImported, disabled }: ImportPanelProps): JSX.Element {
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleUpload = async (): Promise<void> => {
    setUploading(true);
    setError(null);
    try {
      const diff = await uploadImportFiles(files);
      setFiles([]);
      onImported(diff);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The upload failed. Check your connection and try again.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <section className="panel" aria-label="Import files">
      <h2>Import your Trade Republic files</h2>
      <p className="hint">Choose one or more CSV or PDF files from your Trade Republic export.</p>
      {disabled && (
        <p className="hint" data-testid="import-disabled-hint">
          Accept or discard the staged batch below before importing more files.
        </p>
      )}
      <input
        type="file"
        multiple
        accept=".csv,.pdf,application/pdf,text/csv"
        data-testid="import-file-input"
        disabled={disabled || uploading}
        onChange={(event) => setFiles(event.target.files ? Array.from(event.target.files) : [])}
      />
      <button type="button" onClick={() => void handleUpload()} disabled={disabled || uploading || files.length === 0}>
        {uploading ? "Uploading..." : "Upload"}
      </button>
      {error && (
        <p className="error" role="alert" data-testid="import-error">
          {error}
        </p>
      )}
    </section>
  );
}
