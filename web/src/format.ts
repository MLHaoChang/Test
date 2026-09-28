// Plain-text formatting for money, quantities and dates (plan 5.10, WP12 "tests first": "money is
// shown from strings without float parsing").
//
// Every money and quantity value the API returns is already a decimal string (plan 3.3, 5.8): the
// registry stores them exactly, and the API's pydantic models declare them `str`, never `float`,
// so a stray JSON number would fail the response before it reached this page. Formatting a decimal
// string as a `Number` (or with `parseFloat`) can silently lose digits once the string has more
// than 15 significant digits, so every function below works on the string's digits directly and
// never calls `Number(...)` or `parseFloat(...)` on a money or quantity value.

/** Group the digits of a non-negative, non-exponential digit string into thousands with commas. */
function groupThousands(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** Split a plain decimal string ("-1401.00") into its sign, integer and fraction parts. */
function splitDecimal(value: string): { negative: boolean; whole: string; fraction: string } {
  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [whole, fraction = ""] = unsigned.split(".");
  return { negative, whole: whole || "0", fraction };
}

/**
 * Format a EUR (or other currency) amount from its decimal string, grouping thousands and always
 * showing two decimal places. Returns "-" for `null` (the API's way of saying "not known", for
 * example a transfer-in's cost before you enter it with `pg transfers set-cost`).
 */
export function formatMoney(value: string | null, currency = "EUR"): string {
  if (value === null) {
    return "-";
  }
  const { negative, whole, fraction } = splitDecimal(value);
  const cents = fraction.padEnd(2, "0").slice(0, Math.max(2, fraction.length));
  const sign = negative ? "-" : "";
  return `${sign}${groupThousands(whole)}.${cents} ${currency}`;
}

/**
 * Format a share quantity from its decimal string, grouping thousands but keeping every fraction
 * digit exactly as given (a savings-plan buy can have six decimal places). Returns "-" for `null`.
 */
export function formatQuantity(value: string | null): string {
  if (value === null) {
    return "-";
  }
  const { negative, whole, fraction } = splitDecimal(value);
  const sign = negative ? "-" : "";
  return fraction ? `${sign}${groupThousands(whole)}.${fraction}` : `${sign}${groupThousands(whole)}`;
}

/** An ISO (`YYYY-MM-DD`) date, unchanged: already unambiguous, so nothing to convert. */
export function formatDate(value: string | null): string {
  return value ?? "-";
}
