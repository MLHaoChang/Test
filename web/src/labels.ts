// Plain words for the ids the API sends, and counts that agree with their noun (QA P0 round 1,
// M3). The page shows what the CLI shows, in the same words: never an internal id such as
// "same_key" or "transfer_in".

const TYPE_LABELS: Record<string, string> = {
  buy: "purchase",
  sell: "sale",
  dividend: "dividend",
  interest: "interest",
  fee: "fee",
  tax: "tax",
  deposit: "deposit",
  withdrawal: "withdrawal",
  split: "split",
  transfer_in: "transfer in",
  transfer_out: "transfer out",
};

// How a report was matched to a transaction it had already been reported as (plan 5.3.4, rules a
// to c).
const RULE_LABELS: Record<string, string> = {
  same_report: "the same row or document, seen again",
  same_key: "same day, type and amount",
  near_date: "same type and amount, a nearby day",
};

function words(id: string): string {
  return id.replace(/_/g, " ");
}

/** A transaction type in plain words: "purchase", "transfer in". */
export function typeLabel(type: string): string {
  return TYPE_LABELS[type] ?? words(type);
}

/** How a report matched a known transaction, in plain words. */
export function ruleLabel(rule: string): string {
  return RULE_LABELS[rule] ?? words(rule);
}

/** "1 file", "0 files", "2 files". `pluralForm` is for a noun that does not just add an "s". */
export function plural(count: number, singular: string, pluralForm?: string): string {
  return `${count} ${count === 1 ? singular : (pluralForm ?? `${singular}s`)}`;
}
