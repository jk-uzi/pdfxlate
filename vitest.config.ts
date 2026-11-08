import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // The renderer helpers touch `window` (sidebar width) and `document`
    // (message slots), so the DOM environment is the default.
    environment: "jsdom",
    include: ["tests/frontend/**/*.test.ts"],
  },
});
