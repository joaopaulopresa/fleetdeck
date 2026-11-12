import { test } from "@playwright/test";

import { type Who, signIn } from "./helpers";

const OUT = "../docs/media/screens";

const pages: [string, Who, string][] = [
  ["sign-in", "dana", ""],
  ["fleet", "dana", "/"],
  ["policies", "dana", "/policies"],
  ["connectors", "dana", "/connectors"],
  ["skill-library", "dana", "/skill-library"],
  ["usage", "dana", "/usage"],
  ["audit", "dana", "/audit"],
  ["capacity", "dana", "/capacity"],
  ["demo-controls", "dana", "/demo"],
  ["chat", "marcus", "/"],
  ["approvals", "marcus", "/approvals"],
  ["requests", "marcus", "/requests"],
  ["cases", "marcus", "/cases"],
  ["schedules", "marcus", "/schedules"],
  ["skills", "marcus", "/skills"],
];

for (const [name, who, path] of pages) {
  test(`screen: ${name}`, async ({ page }) => {
    if (name === "sign-in") {
      await page.goto("/");
    } else {
      await signIn(page, who);
      await page.goto(path);
    }
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  });
}
