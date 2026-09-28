import { useState } from "react";

import { ApiError, type ImportDiff, uploadConfirmedHoldings } from "../api";

export interface ConfirmedUploadProps {
  batchId: number;
  /** Called with the diff, now carrying the `confirmed` comparison, once the upload succeeds. */
  onUpdated: (diff: ImportDiff) => void;
}

/**
 * An optional confirmed-holdings upload (plan 5.10): what the Trade Republic app shows you, so you
 * can check the rebuilt holdings before you accept them. `POST
 * /portfolio/imports/{batchId}/confirmed-holdings`.
 */
export function ConfirmedUpload({ batchId, onUpdated }: ConfirmedUploadProps): JSX.Element {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleUpload = async (): Promise<void> => {
    if (!file) {
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const diff = await uploadConfirmedHoldings(batchId, file);
      onUpdated(diff);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "The confirmed-holdings upload failed. Check your connection.",
      );
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="confirmed-upload">
      <h3>Check against your confirmed holdings</h3>
      <p className="hint">
        Optional: a CSV of the holdings the Trade Republic app shows you (columns isin, quantity, as_of).
      </p>
      <input
        type="file"
        accept=".csv,text/csv"
        data-testid="confirmed-holdings-input"
        disabled={uploading}
        onChange={(event) => setFile(event.target.files?.[0] ?? null)}
      />
      <button type="button" onClick={() => void handleUpload()} disabled={uploading || !file}>
        {uploading ? "Uploading..." : "Upload confirmed holdings"}
      </button>
      {error && (
        <p className="error" role="alert" data-testid="confirmed-upload-error">
          {error}
        </p>
      )}
    </div>
  );
}
