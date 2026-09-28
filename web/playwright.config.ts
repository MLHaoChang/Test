import { defineConfig, devices } from "@playwright/test";

// Plan 7.6 step 30 and WP12: the Playwright web server (scripts/e2e_web_server.sh) seeds its own
// data directory, then runs `pg serve --port 8766`, so this test does not depend on the CLI steps
// (7.6 steps 4-28) having run first.
const PORT = 8766;
const BASE_URL = `http://127.0.0.1:${PORT}`;

// Plan 3.2: "The Playwright config also reads PW_CHROMIUM_PATH and passes it as executablePath
// when set." Left unset, Playwright resolves Chromium from PLAYWRIGHT_BROWSERS_PATH as usual.
const chromiumPath = process.env.PW_CHROMIUM_PATH;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "bash ../scripts/e2e_web_server.sh",
    url: `${BASE_URL}/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: "pipe",
    stderr: "pipe",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(chromiumPath ? { launchOptions: { executablePath: chromiumPath } } : {}),
      },
    },
  ],
});
