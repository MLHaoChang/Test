// The API client (plan 5.10): thin, typed wrappers over the endpoints of plan 5.8 that the import
// page calls. Every money, quantity and price field below is `string`, matching the API's own
// pydantic models (api/schemas.py): this page never parses one as a number, only ever displays it
// (format.ts) or sends it back unchanged.

export interface BatchRef {
  id: number;
  status: string;
  created_at: string;
  accepted_at: string | null;
}

export interface TransactionBrief {
  id: number;
  type: string;
  isin: string | null;
  name: string | null;
  date: string;
  amount_eur: string | null;
  quantity: string | null;
}

export interface ReviewItem {
  id: number;
  kind: string;
  status: string;
  message: string;
  file_name: string | null;
  transaction: TransactionBrief | null;
}

export interface HoldingChange {
  isin: string;
  name: string | null;
  before: string;
  after: string;
  change: string;
}

export interface ConfirmedRow {
  isin: string;
  name: string | null;
  as_of: string;
  computed: string;
  confirmed: string;
  difference: string;
  status: string;
}

export interface ConfirmedComparison {
  rows: ConfirmedRow[];
  counts: Record<string, number>;
  all_match: boolean;
  message: string;
}

export interface DiffCounts {
  files: number;
  duplicate_files: number;
  candidates: number;
  new: number;
  merged: number;
  already_known: number;
  held_back: number;
  completed: number;
  review_new: number;
  review_closed: number;
}

export interface StagedFile {
  file_name: string;
  status: string;
  doc_type: string | null;
}

/** One transaction as shown in a diff's `new` or `held_back` list (a few fields of the full record). */
export interface DiffTransaction {
  id: number;
  type: string;
  isin: string | null;
  name: string | null;
  date: string;
  amount_eur: string | null;
  quantity: string | null;
}

/** One transaction held out of holdings and value until the review item about it is settled. */
export interface HeldBackTransaction extends DiffTransaction {
  reasons: string[];
}

/** One candidate that matched an already-known transaction instead of becoming a new one. */
export interface MergedRecord {
  file_name: string;
  source_kind: string;
  line_from: number;
  line_to: number;
  rule: string;
  candidate_date: string;
  matched_date: string;
  transaction: DiffTransaction;
}

/** The diff of one import batch (`importer.reconcile.ReconciliationDiff.to_dict`, plan 5.3.6). */
export interface ImportDiff {
  batch: BatchRef;
  as_of: string;
  counts: DiffCounts;
  files: StagedFile[];
  new: DiffTransaction[];
  merged: MergedRecord[];
  already_known: MergedRecord[];
  held_back: HeldBackTransaction[];
  holdings: HoldingChange[];
  review_new: ReviewItem[];
  review_closed: ReviewItem[];
  confirmed: ConfirmedComparison | null;
}

export interface PriceEvidence {
  close: string;
  date: string | null;
  currency: string | null;
  source: string | null;
  symbol: string | null;
  adjustment: string | null;
}

export interface FxEvidence {
  currency: string | null;
  rate: string;
  date: string | null;
}

export interface MappingInfo {
  status: string;
  source: string | null;
  symbol: string | null;
  currency: string | null;
}

export interface HoldingFlag {
  kind: string;
  detail: string;
}

export interface HoldingRecord {
  isin: string;
  name: string;
  quantity: string;
  cost_eur: string | null;
  value_eur: string | null;
  pricing_quantity: string;
  price: PriceEvidence | null;
  fx: FxEvidence | null;
  mapping: MappingInfo;
  flags: HoldingFlag[];
}

export interface HoldingsResponse {
  as_of: string;
  currency: string;
  value_eur: string | null;
  cost_basis_eur: string | null;
  complete: boolean;
  holdings: HoldingRecord[];
}

export interface DayValueRecord {
  date: string;
  value_eur: string | null;
  cost_basis_eur: string | null;
  complete: boolean;
  flags: string[];
}

export interface ValueResponse {
  currency: string;
  from: string | null;
  to: string | null;
  stale_after_days: number;
  days: number;
  complete_days: number;
  latest: DayValueRecord | null;
  series: DayValueRecord[];
}

export interface Reminder {
  due: boolean;
  days_since_last_import: number | null;
  message: string;
}

export interface PortfolioStatus {
  portfolio: { name: string; base_currency: string; source: string };
  today: string;
  last_import_at: string | null;
  reminder: Reminder;
  staged_batch: number | null;
  open_review_items: number;
  latest_value: DayValueRecord | null;
}

export interface AcceptResponse {
  batch: BatchRef;
  accepted: number;
  held_back: number;
  review_opened: ReviewItem[];
  lots: number;
  disposals: number;
}

export interface DiscardResponse {
  batch: BatchRef;
  removed_transactions: number;
}

/** One machine-readable error from the API's `{"error": {"code", "message"}}` envelope (plan 5.8). */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let message = response.statusText || `The request to ${path} failed.`;
    let code = "http_error";
    try {
      const body = (await response.json()) as { error?: { code?: string; message?: string } };
      if (body.error?.message) {
        message = body.error.message;
      }
      if (body.error?.code) {
        code = body.error.code;
      }
    } catch {
      // The error body was not JSON (for example a proxy error page); the fallback message stands.
    }
    throw new ApiError(message, code, response.status);
  }
  return (await response.json()) as T;
}

export function getPortfolioStatus(): Promise<PortfolioStatus> {
  return apiFetch<PortfolioStatus>("/portfolio");
}

export function getHoldings(): Promise<HoldingsResponse> {
  return apiFetch<HoldingsResponse>("/portfolio/holdings");
}

export function getValue(): Promise<ValueResponse> {
  return apiFetch<ValueResponse>("/portfolio/value");
}

export function getImportDiff(batchId: number): Promise<ImportDiff> {
  return apiFetch<ImportDiff>(`/portfolio/imports/${batchId}`);
}

/** `POST /portfolio/imports`: stage a batch from the chosen files and return its diff. */
export function uploadImportFiles(files: File[]): Promise<ImportDiff> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  return apiFetch<ImportDiff>("/portfolio/imports", { method: "POST", body: form });
}

/** `POST /portfolio/imports/{id}/confirmed-holdings`: compare the batch against your own count. */
export function uploadConfirmedHoldings(batchId: number, file: File): Promise<ImportDiff> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<ImportDiff>(`/portfolio/imports/${batchId}/confirmed-holdings`, {
    method: "POST",
    body: form,
  });
}

export function acceptBatch(batchId: number): Promise<AcceptResponse> {
  return apiFetch<AcceptResponse>(`/portfolio/imports/${batchId}/accept`, { method: "POST" });
}

export function discardBatch(batchId: number): Promise<DiscardResponse> {
  return apiFetch<DiscardResponse>(`/portfolio/imports/${batchId}/discard`, { method: "POST" });
}
