import { defineConfig } from "@playwright/test";

const baseURL = process.env.FLEETDECK_URL ?? "http://127.0.0.1:8090";
const viewport = { width: 1280, height: 720 };

// tests:   end-to-end checks of the web app against the running cluster.
// screens: still screenshots of the main pages, into ../docs/media/screens.
// demo:    the recorded storyline and feature videos (they call a real model).
export default defineConfig({
  outputDir: "output",
  workers: 1,
  reporter: [["list"]],
  use: { baseURL, viewport, locale: "en-US", timezoneId: "America/New_York" },
  projects: [
    { name: "tests", testDir: "tests", timeout: 180_000 },
    { name: "screens", testDir: "demo", testMatch: "screens.ts", timeout: 120_000 },
    {
      name: "demo", testDir: "demo", testMatch: /video-.*\.ts/, timeout: 1_500_000,
      use: { video: { mode: "on", size: viewport } },
    },
  ],
});
