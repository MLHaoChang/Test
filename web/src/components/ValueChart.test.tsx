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
