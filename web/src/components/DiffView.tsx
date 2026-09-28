import { useState } from "react";

import {
  acceptBatch,
  type AcceptResponse,
  ApiError,
  discardBatch,
  type DiscardResponse,
  type ImportDiff,
  type MergedRecord,
} from "../api";
import { formatMoney, formatQuantity } from "../format";
import { plural, ruleLabel, typeLabel } from "../labels";
import { ConfirmedUpload } from "./ConfirmedUpload";

export interface DiffViewProps {
  diff: ImportDiff;
  /** Lets the parent replace the diff shown here, for example after a confirmed-holdings upload. */
  onDiffChanged: (diff: ImportDiff) => void;
  onAccepted: (result: AcceptResponse) => void;
  onDiscarded: (result: DiscardResponse) => void;
}

/** The reports of this import that matched a transaction, with how each one matched (plan 5.3.4). */
function MatchTable({ rows, caption, testId }: { rows: MergedRecord[]; caption: string; testId: string }): JSX.Element {
  return (
    <div className="table-scroll">
      <table data-testid={testId}>
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th>File</th>
            <th>Matched by</th>
            <th>Date</th>
            <th>Type</th>
            <th>Name</th>
            <th>Amount</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.file_name}-${row.line_from}-${index}`}>
              <td>{row.file_name}</td>
              <td>{ruleLabel(row.rule)}</td>
              <td>{row.matched_date}</td>
              <td>{typeLabel(row.transaction.type)}</td>
              <td>{row.transaction.name ?? row.transaction.isin ?? "-"}</td>
              <td>{formatMoney(row.transaction.amount_eur)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The diff (plan 5.10): counts, the new, merged, already-known and held-back transactions, review
 * items with their file and reason, holdings before and after, and the optional confirmed-holdings
 * upload with the match table. Accept and Discard act on `diff.batch.id`. Every table scrolls
 * sideways inside its own box on a narrow screen.
 */
export function DiffView({ diff, onDiffChanged, onAccepted, onDiscarded }: DiffViewProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runAction<T>(action: () => Promise<T>, onDone: (result: T) => void): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      onDone(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That did not work. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  const { counts } = diff;
  const alreadyKnown = diff.already_known;

  return (
    <section className="panel" aria-label="Import diff" data-testid="diff-view">
      <h2>What this import found</h2>
      <p data-testid="diff-summary">
        {plural(counts.new, "new transaction")}, {counts.merged} merged, {counts.already_known} already known,{" "}
        {counts.held_back} held back, {counts.review_new} {counts.review_new === 1 ? "needs" : "need"} your review.
        {counts.duplicate_files > 0 && ` ${plural(counts.duplicate_files, "file")} already imported, skipped.`}
      </p>

      {diff.new.length > 0 && (
        <div className="table-scroll">
          <table data-testid="diff-new-transactions">
            <caption>New transactions</caption>
            <thead>
              <tr>
                <th>Date</th>
                <th>Type</th>
                <th>Name</th>
                <th>Quantity</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {diff.new.map((txn) => (
                <tr key={txn.id}>
                  <td>{txn.date}</td>
                  <td>{typeLabel(txn.type)}</td>
                  <td>{txn.name ?? txn.isin ?? "-"}</td>
                  <td>{formatQuantity(txn.quantity)}</td>
                  <td>{formatMoney(txn.amount_eur)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {diff.merged.length > 0 && (
        <MatchTable
          rows={diff.merged}
          caption="Merged with a report in another file of this import"
          testId="diff-merged-transactions"
        />
      )}

      {alreadyKnown.length > 0 && (
        <details data-testid="diff-already-known">
          <summary>Show the {plural(alreadyKnown.length, "transaction")} already known from earlier imports</summary>
          <MatchTable
            rows={alreadyKnown}
            caption="Already known from earlier imports"
            testId="diff-already-known-transactions"
          />
        </details>
      )}

      {diff.held_back.length > 0 && (
        <div className="table-scroll">
          <table data-testid="diff-held-back-transactions">
            <caption>Held back until the review item is settled</caption>
            <thead>
              <tr>
                <th>Date</th>
                <th>Name</th>
                <th>Quantity</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {diff.held_back.map((txn) => (
                <tr key={txn.id}>
                  <td>{txn.date}</td>
                  <td>{txn.name ?? txn.isin ?? "-"}</td>
                  <td>{formatQuantity(txn.quantity)}</td>
                  <td>{txn.reasons.map((reason) => reason.replace(/_/g, " ")).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {diff.review_new.length > 0 && (
        <div data-testid="diff-review-items">
          <h3>Review items</h3>
          <ul>
            {diff.review_new.map((item) => (
              <li key={item.id}>
                {item.file_name && <span className="review-file">{item.file_name}: </span>}
                {item.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {diff.holdings.length > 0 && (
        <div className="table-scroll">
          <table data-testid="diff-holdings">
            <caption>Holdings before and after this import</caption>
            <thead>
              <tr>
                <th>ISIN</th>
                <th>Name</th>
                <th>Before</th>
                <th>After</th>
                <th>Change</th>
              </tr>
            </thead>
            <tbody>
              {diff.holdings.map((change) => (
                <tr key={change.isin}>
                  <td>{change.isin}</td>
                  <td>{change.name ?? change.isin}</td>
                  <td>{formatQuantity(change.before)}</td>
                  <td>{formatQuantity(change.after)}</td>
                  <td>{formatQuantity(change.change)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmedUpload batchId={diff.batch.id} onUpdated={onDiffChanged} />
      {diff.confirmed && (
        <>
          {diff.confirmed.rows.length > 0 && (
            <div className="table-scroll">
              <table data-testid="diff-confirmed-rows">
                <caption>Your confirmed holdings against what this import computes</caption>
                <thead>
                  <tr>
                    <th>ISIN</th>
                    <th>Name</th>
                    <th>As of</th>
                    <th>Computed</th>
                    <th>Confirmed</th>
                    <th>Difference</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {diff.confirmed.rows.map((row) => (
                    <tr key={`${row.isin}-${row.as_of}`}>
                      <td>{row.isin}</td>
                      <td>{row.name ?? row.isin}</td>
                      <td>{row.as_of}</td>
                      <td>{formatQuantity(row.computed)}</td>
                      <td>{formatQuantity(row.confirmed)}</td>
                      <td>{formatQuantity(row.difference)}</td>
                      <td>{row.status.replace(/_/g, " ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p data-testid="confirmed-message">
            Confirmed holdings: {diff.confirmed.message}.{" "}
            {diff.confirmed.all_match ? "" : "Check the rows above before you accept."}
          </p>
        </>
      )}

      {error && (
        <p className="error" role="alert" data-testid="diff-error">
          {error}
        </p>
      )}

      <div className="actions">
        <button
          type="button"
          disabled={busy}
          onClick={() => void runAction(() => acceptBatch(diff.batch.id), onAccepted)}
        >
          Accept these transactions into my portfolio copy
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void runAction(() => discardBatch(diff.batch.id), onDiscarded)}
        >
          Discard
        </button>
      </div>
    </section>
  );
}
