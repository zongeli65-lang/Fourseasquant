import { defineConfig } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const testWorkDirectory = join(process.cwd(), "work", "browser-tests");
mkdirSync(testWorkDirectory, { recursive: true });

const testDatabase = join(
  testWorkDirectory,
  `fourseasquant-browser-${process.pid}-${Date.now()}.db`,
);
const webPort = process.env.FOURSEASQUANT_WEB_PORT ?? "5173";
const baseURL = `http://127.0.0.1:${webPort}`;

export default defineConfig({
  testDir: "tests/browser",
  timeout: 15_000,
  fullyParallel: false,
  workers: 1,
  reporter: "line",
  globalTeardown: "tests/browser/global-teardown.ts",
  use: {
    baseURL,
    channel: "chrome",
    headless: true,
  },
  webServer: {
    command: "npm run dev",
    url: baseURL,
    reuseExistingServer:
      process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER === "1",
    timeout: 30_000,
    gracefulShutdown: {
      signal: "SIGTERM",
      timeout: 5_000,
    },
    env: {
      FOURSEASQUANT_DB_PATH: testDatabase,
      FOURSEASQUANT_RELOAD: "0",
      FOURSEASQUANT_ENABLE_FAILURE_SIMULATION: "1",
      FOURSEASQUANT_ENABLE_STARTUP_CATCHUP: "0",
    },
  },
});
