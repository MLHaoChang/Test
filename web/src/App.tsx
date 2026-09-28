import { useEffect, useState } from "react";

import {
  type AcceptResponse,
  ApiError,
  type DiscardResponse,
  getHoldings,
  getImportDiff,
  getPortfolioStatus,
  getValue,
  type HoldingsResponse,
  type ImportDiff,
  type PortfolioStatus,
  type ValueResponse,
} from "./api";
import { Badge } from "./components/Badge";
import { DiffView } from "./components/DiffView";
import { HoldingsTable } from "./components/HoldingsTable";
import { ImportPanel } from "./components/ImportPanel";
import { ValueChart } from "./components/ValueChart";

/**
 * The whole minimal import page (plan 5.10): the badge, the upload panel, the diff (once a batch is
 * staged), and the holdings table with the value chart (once anything has been accepted). No other
 * navigation: the P1 screens replace this page's shell later.
 */
export function App(): JSX.Element {
  const [status, setStatus] = useState<PortfolioStatus | null>(null);
  const [diff, setDiff] = useState<ImportDiff | null>(null);
  const [holdings, setHoldings] = useState<HoldingsResponse | null>(null);
  const [value, setValue] = useState<ValueResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  async function refreshHoldingsAndValue(): Promise<void> {
    const [nextHoldings, nextValue] = await Promise.all([getHoldings(), getValue()]);
    setHoldings(nextHoldings);
    setValue(nextValue);
  }

  // The reminder and the last import change with every accept (and a discard may leave the
  // portfolio as empty as before), so the status is read again after each (QA P0 round 1, M2).
  async function refreshStatus(): Promise<void> {
    try {
      setStatus(await getPortfolioStatus());
    } catch {
      // The status shown so far stays; the next page load reads it again.
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function load(): Promise<void> {
      try {
        const nextStatus = await getPortfolioStatus();
        if (cancelled) {
          return;
        }
        setStatus(nextStatus);
        const [nextDiff] = await Promise.all([
          nextStatus.staged_batch !== null ? getImportDiff(nextStatus.staged_batch) : Promise.resolve(null),
          refreshHoldingsAndValue(),
        ]);
        if (cancelled) {
          return;
        }
        setDiff(nextDiff);
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof ApiError ? err.message : "Could not reach the portfolio API.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleAccepted = (_result: AcceptResponse): void => {
    setDiff(null);
    void refreshHoldingsAndValue();
    void refreshStatus();
  };

  const handleDiscarded = (_result: DiscardResponse): void => {
    setDiff(null);
    void refreshStatus();
  };

  // The API words the empty-portfolio reminder for the CLI ("pg import FILE..."); on the page the
  // way to import is right below it. No reminder while a batch waits: that is an import going on.
  const reminder =
    status?.reminder.due && diff === null
      ? status.reminder.days_since_last_import === null
        ? "Nothing has been imported yet. Choose your Trade Republic files below."
        : status.reminder.message
      : null;

  return (
    <div className="app">
      <Badge />
      {reminder && (
        <p className="reminder" data-testid="import-reminder">
          {reminder}
        </p>
      )}
      {loadError && (
        <p className="error" role="alert" data-testid="app-error">
          {loadError}
        </p>
      )}
      <ImportPanel onImported={setDiff} disabled={loading || diff !== null} />
      {diff && (
        <DiffView diff={diff} onDiffChanged={setDiff} onAccepted={handleAccepted} onDiscarded={handleDiscarded} />
      )}
      {loading ? (
        <p data-testid="app-loading">Loading your portfolio...</p>
      ) : (
        holdings &&
        holdings.holdings.length > 0 && (
          <>
            <HoldingsTable holdings={holdings.holdings} currency={holdings.currency} />
            {value && <ValueChart series={value.series} currency={value.currency} from={value.from} to={value.to} />}
          </>
        )
      )}
    </div>
  );
}
