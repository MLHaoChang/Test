// Loaded by every Vitest test (vite.config.ts's test.setupFiles): extends `expect` with the
// jest-dom matchers (toBeInTheDocument, toHaveTextContent, ...) used across the component tests.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// vite.config.ts sets test.globals = false (explicit imports everywhere, rather than ambient
// test globals), so @testing-library/react's own auto-cleanup - which only registers itself when
// it finds a global afterEach - never fires on its own. Without this, the component tree from one
// test is still mounted when the next test in the same file renders, and queries like
// getByTestId start matching more than one element.
afterEach(() => {
  cleanup();
});
