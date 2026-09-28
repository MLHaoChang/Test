// The value chart's y axis (QA P0 round 2): round tick values and their labels.
//
// A tick is a round number the chart picks for its own scale, never a figure from the API, so it is
// a plain JavaScript number here. Every figure the API sends is still shown only through format.ts,
// from its decimal string.

// Each step is one of these times a power of ten: 1, 2, 2.5 or 5 (then 10).
const STEP_FACTORS = [1, 2, 2.5, 5, 10];
// Aim for three intervals, which gives three or four ticks.
const INTERVALS = 3;

/**
 * Round tick values for a y axis that must show every value from `min` to `max`: from zero (or
 * below) up to the first round value at or above `max`, in three or four ticks, whole euros apart
 * at the least. The chart uses the first and last tick as its scale, so the top line of the plot
 * always has a label.
 */
export function valueTicks(min: number, max: number): number[] {
  const low = Math.min(0, min);
  const high = Math.max(low + 1, max);
  const rough = (high - low) / INTERVALS;
  const power = 10 ** Math.floor(Math.log10(rough));
  const factor = STEP_FACTORS.find((candidate) => candidate * power >= rough) ?? 10;
  const step = Math.max(1, factor * power);
  const first = Math.floor(low / step) * step;
  const count = Math.round((Math.ceil(high / step) * step - first) / step);
  return Array.from({ length: count + 1 }, (_, index) => first + index * step);
}

/** A tick label: a whole amount with thousands commas and the currency, "7,500 EUR". */
export function formatTick(value: number, currency: string): string {
  const whole = Math.round(Math.abs(value)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${value < 0 ? "-" : ""}${whole} ${currency}`;
}
