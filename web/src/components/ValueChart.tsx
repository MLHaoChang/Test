import { useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";

import type { DayValueRecord } from "../api";
import { formatTick, valueTicks } from "../axis";
import { formatDate, formatMoney } from "../format";

export interface ValueChartProps {
  series: DayValueRecord[];
  currency: string;
  from: string | null;
  to: string | null;
}

const WIDTH = 640;
const HEIGHT = 220;
const PADDING = { top: 12, right: 12, bottom: 12, left: 8 };

// Colors come from the page's tokens in index.css, which follow the dataviz skill's reference
// palette (references/palette.md) in light and dark mode: the line in the accent, gridlines as
// solid hairlines one step off the surface, the zero baseline one step darker, axis text in muted
// ink. Strokes do not scale with the drawing, so a hairline stays 1px and the line 2px at any width.

interface Point {
  index: number;
  date: string;
  value: number;
  raw: string;
}

function buildPoints(series: DayValueRecord[]): Point[] {
  const points: Point[] = [];
  series.forEach((day, index) => {
    if (day.value_eur !== null) {
      // Number(...) here only ever feeds the chart's pixel geometry (an SVG coordinate has to be a
      // number); every value shown as text still goes through formatMoney on the original string,
      // so no displayed figure ever passes through a float round trip.
      points.push({ index, date: day.date, value: Number(day.value_eur), raw: day.value_eur });
    }
  });
  return points;
}

/**
 * A small SVG line chart of the daily portfolio value (plan 5.10), `GET /portfolio/value`.
 *
 * The y axis has three or four round amounts, each on a gridline (QA P0 round 2: a day's value could
 * be read only by hovering). The labels sit in their own column beside the drawing, as HTML text, so
 * they keep their size when the drawing shrinks to fit a phone.
 */
export function ValueChart({ series, currency, from, to }: ValueChartProps): JSX.Element {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const points = useMemo(() => buildPoints(series), [series]);
  const latest = points.length > 0 ? points[points.length - 1] : null;
  const ticks = useMemo(() => {
    const values = points.map((p) => p.value);
    return values.length > 0 ? valueTicks(Math.min(...values), Math.max(...values)) : [0, 1];
  }, [points]);

  const plotWidth = WIDTH - PADDING.left - PADDING.right;
  const plotHeight = HEIGHT - PADDING.top - PADDING.bottom;
  const minValue = ticks[0];
  const maxValue = ticks[ticks.length - 1];
  const valueSpan = maxValue - minValue || 1;

  const xForIndex = (index: number): number =>
    points.length > 1 ? PADDING.left + (index / (points.length - 1)) * plotWidth : PADDING.left + plotWidth / 2;
  const yForValue = (value: number): number => PADDING.top + plotHeight - ((value - minValue) / valueSpan) * plotHeight;

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${xForIndex(p.index).toFixed(2)},${yForValue(p.value).toFixed(2)}`).join(" ");

  // Top to bottom, the order the eye reads the axis in.
  const axis = [...ticks].reverse().map((tick) => ({ tick, y: yForValue(tick), label: formatTick(tick, currency) }));
  const widestLabel = axis.reduce((widest, { label }) => (label.length > widest.length ? label : widest), "");

  const handleMove = (event: MouseEvent<SVGRectElement>): void => {
    const svg = svgRef.current;
    if (!svg || points.length === 0) {
      return;
    }
    const rect = svg.getBoundingClientRect();
    const relativeX = ((event.clientX - rect.left) / rect.width) * WIDTH;
    const fraction = points.length > 1 ? (relativeX - PADDING.left) / plotWidth : 0;
    const nearest = Math.round(fraction * (points.length - 1));
    setHoverIndex(Math.min(points.length - 1, Math.max(0, nearest)));
  };

  const hovered = hoverIndex !== null ? points[hoverIndex] : null;

  return (
    <div
      className="value-chart"
      data-testid="value-chart"
      data-from={from ?? ""}
      data-to={to ?? ""}
      data-points={points.length}
    >
      <h3>Portfolio value</h3>
      {points.length === 0 ? (
        <p data-testid="value-chart-empty">No value data yet. Accept an import to see your portfolio's value.</p>
      ) : (
        <>
          <p className="hint">
            {formatDate(from)} to {formatDate(to)}
          </p>
          <div className="value-chart-plot">
            <div className="value-chart-y-axis" aria-hidden="true">
              {/* Invisible, in the flow: gives the column the width of its widest label. */}
              <span className="value-chart-tick-sizer">{widestLabel}</span>
              {axis.map(({ tick, y, label }) => (
                <span
                  key={tick}
                  className="value-chart-tick"
                  data-testid="value-chart-tick"
                  style={{ top: `${(y / HEIGHT) * 100}%` }}
                >
                  {label}
                </span>
              ))}
            </div>
            <svg
              ref={svgRef}
              viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
              role="img"
              aria-label={`Portfolio value from ${formatDate(from)} to ${formatDate(to)}, ending at ${
                latest ? formatMoney(latest.raw, currency) : "-"
              }`}
            >
              {axis.map(({ tick, y }) => (
                <line
                  key={tick}
                  className={tick === 0 ? "value-chart-grid value-chart-baseline" : "value-chart-grid"}
                  x1={PADDING.left}
                  y1={y}
                  x2={WIDTH - PADDING.right}
                  y2={y}
                />
              ))}
              <path className="value-chart-line" d={linePath} />
              {latest && (
                <circle className="value-chart-dot" cx={xForIndex(latest.index)} cy={yForValue(latest.value)} r={4} />
              )}
              {hovered && (
                <g data-testid="value-chart-hover">
                  <line
                    className="value-chart-crosshair"
                    x1={xForIndex(hovered.index)}
                    y1={PADDING.top}
                    x2={xForIndex(hovered.index)}
                    y2={HEIGHT - PADDING.bottom}
                  />
                  <circle
                    className="value-chart-hover-dot"
                    cx={xForIndex(hovered.index)}
                    cy={yForValue(hovered.value)}
                    r={4}
                  />
                </g>
              )}
              <rect
                x={PADDING.left}
                y={PADDING.top}
                width={plotWidth}
                height={plotHeight}
                fill="transparent"
                onMouseMove={handleMove}
                onMouseLeave={() => setHoverIndex(null)}
              />
            </svg>
          </div>
          <p className="hint" data-testid="value-chart-tooltip">
            {hovered ? `${formatDate(hovered.date)}: ${formatMoney(hovered.raw, currency)}` : "Hover the line for a day's value."}
          </p>
          <p data-testid="value-chart-latest">
            Latest value: {latest ? formatMoney(latest.raw, currency) : "-"} on {latest ? formatDate(latest.date) : "-"}
          </p>
        </>
      )}
    </div>
  );
}
