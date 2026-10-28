import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: ".",
  timeout: 30000,
  retries: 1,
  use: { baseURL: "http://localhost:4242", headless: true, screenshot: "on", video: "retain-on-failure", trace: "retain-on-failure" },
  reporter: [["list"], ["json", { outputFile: `${process.env.MCODE_DIR}/fe_testing/playwright-results.json` }]],
});
