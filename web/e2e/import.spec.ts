import { readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

// The same golden files the CLI's own end-to-end scenario imports (plan 7.6 steps 5 and 7): this
// test drives the identical import through the page instead of the CLI, so both surfaces are
// checked against the same fixed portfolio.
//
// web/package.json declares "type": "module", so Playwright runs this file as native ESM, where
// __dirname does not exist (unlike a Vitest test, which shims it): the directory has to come from
// import.meta.url instead.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.resolve(HERE, "../../tests/fixtures/golden/inputs");
const PDF_DIR = path.join(FIXTURES, "pdf");
const ROUND_ONE_FILES = [
  path.join(FIXTURES, "tr_transactions_2024.csv"),
  ...readdirSync(PDF_DIR)
    .filter((name) => name.endsWith(".pdf"))
    .sort()
    .map((name) => path.join(PDF_DIR, name)),
];
const CONFIRMED_HOLDINGS = path.join(FIXTURES, "confirmed_holdings_2024-12-31.csv");

// The tests below share one `pg serve` and one data directory (scripts/e2e_web_server.sh starts
// it once for the whole file, seeded with nothing accepted yet), and Playwright runs a file's
// tests in declaration order with workers: 1 (playwright.config.ts), so they run in the order
// written here, each depending on the portfolio state the one before it left behind:
//
// 1. discard leaves the portfolio exactly as empty as it started (a discarded batch's
//    transactions are removed, not just hidden), so
// 2. the import test below can still expect a pristine "0 accepted" portfolio before it accepts
//    anything itself, and
// 3. the phone-width test finds the holdings table the import test accepted.

test("discarding a staged batch leaves nothing accepted", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("holdings-table")).toHaveCount(0);

  await page.getByTestId("import-file-input").setInputFiles(ROUND_ONE_FILES);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByTestId("diff-view")).toBeVisible();

  await page.getByRole("button", { name: "Discard" }).click();

  await expect(page.getByTestId("diff-view")).toHaveCount(0);
  await expect(page.getByTestId("holdings-table")).toHaveCount(0);
  // Importing is available again immediately: nothing is left staged.
  await expect(page.getByTestId("import-file-input")).toBeEnabled();
  // The input no longer shows the uploaded files, so choosing the same files again, without a
  // reload, is a new choice and enables Upload (QA P0 round 1, M1).
  await expect(page.getByTestId("import-file-input")).toHaveValue("");
  await page.getByTestId("import-file-input").setInputFiles(ROUND_ONE_FILES);
  await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeEnabled();
  // Still nothing accepted, so the reminder to import stays, worded for this page.
  await expect(page.getByTestId("import-reminder")).toHaveText(
    "Nothing has been imported yet. Choose your Trade Republic files below.",
  );
});

test("import, diff, accept, holdings and the value chart", async ({ page }) => {
  await page.goto("/");

  // The badge is on the page before anything is uploaded, and stays exact.
  await expect(page.getByTestId("no-real-orders-badge")).toHaveText("NO REAL ORDERS · portfolio read-only");

  // Upload the golden CSV export and PDF documents (plan 7.6 step 5's files).
  await page.getByTestId("import-file-input").setInputFiles(ROUND_ONE_FILES);
  await page.getByRole("button", { name: "Upload", exact: true }).click();

  await expect(page.getByTestId("diff-summary")).toContainText("14 new transactions");
  await expect(page.getByTestId("diff-summary")).toContainText("2 need your review");
  await expect(page.getByTestId("diff-review-items")).toContainText(
    "unbekannt_kosteninformation.pdf: No parser recognises this document layout",
  );

  // Check the rebuilt holdings against what the Trade Republic app would show (plan 7.6 step 7).
  await page.getByTestId("confirmed-holdings-input").setInputFiles(CONFIRMED_HOLDINGS);
  await page.getByRole("button", { name: "Upload confirmed holdings" }).click();
  await expect(page.getByTestId("confirmed-message")).toContainText("5 of 5 match");

  // Accept: the transactions become the portfolio's own copy (plan 7.6 step 9).
  await page.getByRole("button", { name: "Accept these transactions into my portfolio copy" }).click();

  // The diff and its actions are gone once the batch is accepted; the holdings and chart replace it.
  await expect(page.getByTestId("diff-view")).toHaveCount(0);
  await expect(page.getByTestId("holdings-table")).toBeVisible();
  // Something is accepted now: the reminder to import is gone without a reload (QA P0 round 1, M2),
  // and the file input is empty again (M1).
  await expect(page.getByTestId("import-reminder")).toHaveCount(0);
  await expect(page.getByTestId("import-file-input")).toHaveValue("");

  const rows = page.getByTestId("holdings-table").locator("tbody tr");
  await expect(rows).toHaveCount(5);
  const sapRow = page.getByTestId("holdings-row-DE0007164600");
  await expect(sapRow.locator("td").nth(2)).toHaveText("3");
  const nvdaRow = page.getByTestId("holdings-row-US67066G1040");
  await expect(nvdaRow.locator("td").nth(2)).toHaveText("20"); // post 10-for-1 split (plan 1.2)

  const chart = page.getByTestId("value-chart");
  await expect(chart).toHaveAttribute("data-from", "2024-01-02");
  await expect(chart).toHaveAttribute("data-to", "2024-12-31");
  await expect(chart).toContainText("6,146.85 EUR");

  // Reload: the same holdings and chart come back from the server, not from in-page state alone.
  await page.reload();
  await expect(page.getByTestId("holdings-table")).toBeVisible();
  await expect(page.getByTestId("holdings-row-DE0007164600").locator("td").nth(2)).toHaveText("3");
  await expect(page.getByTestId("value-chart")).toHaveAttribute("data-to", "2024-12-31");

  // Import the same files again: everything is already known (plan 7.6 step 11's idempotency check).
  await page.getByTestId("import-file-input").setInputFiles(ROUND_ONE_FILES);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByTestId("diff-summary")).toContainText("0 new transactions");
  await expect(page.getByTestId("diff-summary")).toContainText("10 files already imported, skipped");

  // Nowhere on the page does the app call itself a live-trading system (spec section 7).
  const bodyText = (await page.locator("body").innerText()).toLowerCase();
  expect(bodyText).not.toContain("live trading");
});

test("the page does not scroll sideways on a phone", async ({ page }) => {
  // Runs after the import test above, so the holdings table and the chart are on the page. A table
  // wider than the screen scrolls inside its own box instead (QA P0 round 1, M4).
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByTestId("holdings-table")).toBeVisible();

  const widths = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth,
    screen: window.innerWidth,
  }));
  expect(widths.page).toBeLessThanOrEqual(widths.screen);
});
