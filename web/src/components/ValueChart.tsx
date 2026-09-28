import { useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";

import type { DayValueRecord } from "../api";
import { formatDate, formatMoney } from "../format";

export interface ValueChartProps {
  series: DayValueRecord[];
  currency: string;
  from: string | null;
  to: string | null;
}

const WIDTH = 640;
const HEIGHT = 220;
const PADDING = { top: 12, right: 12, bottom: 8, left: 12 };

// The dataviz skill's reference palette (references/palette.md): series slot 1 (blue) for this
// chart's one line, chrome and ink from the same instance. Light values only: this is a small,
// server-rendered admin page, not yet themed for dark mode (plan 5.10 calls it "minimal").
const LINE_COLOR = "#2a78d6";
const GRID_COLOR = "#e1e0d9";
const AXIS_COLOR = "#c3c2b7";
const MUTED_TEXT = "#898781";
const PRIMARY_TEXT = "#0b0b0b";

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

/** A small SVG line chart of the daily portfolio value (plan 5.10), `GET /portfolio/value`. */
export function ValueChart({ series, currency, from, to }: ValueChartProps): JSX.Element {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const points = useMemo(() => buildPoints(series), [series]);
  const latest = points.length > 0 ? points[points.length - 1] : null;

  const plotWidth = WIDTH - PADDING.left - PADDING.right;
  const plotHeight = HEIGHT - PADDING.top - PADDING.bottom;
  const minValue = points.length > 0 ? Math.min(0, ...points.map((p) => p.value)) : 0;
  const maxValue = points.length > 0 ? Math.max(0, ...points.map((p) => p.value)) : 1;
  const valueSpan = maxValue - minValue || 1;

  const xForIndex = (index: number): number =>
    points.length > 1 ? PADDING.left + (index / (points.length - 1)) * plotWidth : PADDING.left + plotWidth / 2;
  const yForValue = (value: number): number => PADDING.top + plotHeight - ((value - minValue) / valueSpan) * plotHeight;

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${xForIndex(p.index).toFixed(2)},${yForValue(p.value).toFixed(2)}`).join(" ");

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
          <svg
            ref={svgRef}
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            role="img"
            aria-label={`Portfolio value from ${formatDate(from)} to ${formatDate(to)}, ending at ${
              latest ? formatMoney(latest.raw, currency) : "-"
            }`}
          >
            <line
              x1={PADDING.left}
              y1={yForValue(minValue < 0 ? 0 : minValue)}
              x2={WIDTH - PADDING.right}
              y2={yForValue(minValue < 0 ? 0 : minValue)}
              stroke={AXIS_COLOR}
              strokeWidth={1}
            />
            <line
              x1={PADDING.left}
              y1={PADDING.top}
              x2={WIDTH - PADDING.right}
              y2={PADDING.top}
              stroke={GRID_COLOR}
              strokeWidth={1}
            />
            <path d={linePath} fill="none" stroke={LINE_COLOR} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
            {latest && (
              <circle cx={xForIndex(latest.index)} cy={yForValue(latest.value)} r={3.5} fill={LINE_COLOR} />
            )}
            {hovered && (
              <g data-testid="value-chart-hover">
                <line
                  x1={xForIndex(hovered.index)}
                  y1={PADDING.top}
                  x2={xForIndex(hovered.index)}
                  y2={HEIGHT - PADDING.bottom}
                  stroke={AXIS_COLOR}
                  strokeWidth={1}
                  strokeDasharray="2,2"
                />
                <circle cx={xForIndex(hovered.index)} cy={yForValue(hovered.value)} r={3.5} fill={PRIMARY_TEXT} />
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
          <p className="hint" style={{ color: MUTED_TEXT }} data-testid="value-chart-tooltip">
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
