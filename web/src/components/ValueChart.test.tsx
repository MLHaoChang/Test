import { readFileSync } from "node:fs";
import path from "node:path";

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ValueResponse } from "../api";
import { ValueChart } from "./ValueChart";

const VALUE: ValueResponse = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../tests/fixtures/golden/expected/value.json"), "utf-8"),
) as ValueResponse;

describe("ValueChart", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a message instead of a chart when there is no data yet", () => {
    render(<ValueChart series={[]} currency="EUR" from={null} to={null} />);
    expect(screen.getByTestId("value-chart-empty")).toBeInTheDocument();
  });

  it("renders the golden value.json series over its stated range", () => {
    render(<ValueChart series={VALUE.series} currency={VALUE.currency} from={VALUE.from} to={VALUE.to} />);

    const chart = screen.getByTestId("value-chart");
    expect(chart).toHaveAttribute("data-from", "2024-01-02");
    expect(chart).toHaveAttribute("data-to", "2024-12-31");

    expect(screen.getByTestId("value-chart-latest")).toHaveTextContent("6,146.85 EUR");
    expect(screen.getByTestId("value-chart-latest")).toHaveTextContent("2024-12-31");
  });

  it("labels the y axis with round amounts in EUR, one per gridline (QA P0 round 2)", () => {
    // A day's value could be read only by hovering: the axis had no values at all.
    render(<ValueChart series={VALUE.series} currency={VALUE.currency} from={VALUE.from} to={VALUE.to} />);

    const labels = screen.getAllByTestId("value-chart-tick").map((tick) => tick.textContent);
    expect(labels).toEqual(["7,500 EUR", "5,000 EUR", "2,500 EUR", "0 EUR"]);
    const chart = screen.getByTestId("value-chart");
    expect(chart.querySelectorAll("line.value-chart-grid")).toHaveLength(4);
    // Each label sits at the height of its gridline: the top one at the top of the plot.
    const tops = screen.getAllByTestId("value-chart-tick").map((tick) => parseFloat(tick.style.top));
    const gridYs = Array.from(chart.querySelectorAll("line.value-chart-grid")).map(
      (line) => (Number(line.getAttribute("y1")) / 220) * 100,
    );
    tops.forEach((top, index) => expect(top).toBeCloseTo(gridYs[index], 3));
  });

  it("labels the chart, for assistive technology, with the same figure it shows visually", () => {
    render(<ValueChart series={VALUE.series} currency={VALUE.currency} from={VALUE.from} to={VALUE.to} />);
    expect(screen.getByRole("img")).toHaveAccessibleName(/6,146\.85 EUR/);
  });

  it("shows the hovered day's exact value on the crosshair line", () => {
    // jsdom lays out nothing, so getBoundingClientRect is stubbed to a plausible chart box: this
    // is the only way a mouse-position test can work without a real browser (Playwright covers the
    // real rendered chart end to end).
    vi.spyOn(SVGSVGElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      width: 640,
      height: 220,
      right: 640,
      bottom: 220,
      x: 0,
      y: 0,
      toJSON: () => "",
    });

    render(<ValueChart series={VALUE.series} currency={VALUE.currency} from={VALUE.from} to={VALUE.to} />);
    const plot = screen.getByTestId("value-chart").querySelector("rect");
    expect(plot).not.toBeNull();

    // The rightmost edge of the plot area is the last day of the series (2024-12-31).
    fireEvent.mouseMove(plot as Element, { clientX: 640, clientY: 100 });

    expect(screen.getByTestId("value-chart-tooltip")).toHaveTextContent("2024-12-31");
    expect(screen.getByTestId("value-chart-tooltip")).toHaveTextContent("6,146.85 EUR");
  });
});
