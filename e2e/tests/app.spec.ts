import { expect, test } from "@playwright/test";

import { apiAs, signIn } from "../demo/helpers";

// End-to-end checks of the web app against the running cluster. No model is called.

test("an employee and an administrator see different applications", async ({ page }) => {
  await signIn(page, "marcus");
  const rail = page.getByRole("navigation", { name: "Main" });
  await expect(rail.getByRole("link", { name: "My agent" })).toBeVisible();
  await expect(rail.getByRole("link", { name: "Fleet" })).toHaveCount(0);
  await page.goto("/audit");
  await expect(page.getByRole("heading", { name: "My agent" })).toBeVisible(); // no such page for him
  expect((await page.request.get("/api/agents")).status()).toBe(401);

  await signIn(page, "dana");
  await expect(rail.getByRole("link", { name: "Fleet" })).toBeVisible();
  await expect(rail.getByRole("link", { name: "My agent" })).toHaveCount(0);
  await expect(page.getByTestId("fleet").getByRole("row")).toHaveCount(6); // header and five agents
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: /Every employee has an agent/ })).toBeVisible();
});

test("the administrator wakes an agent and puts it back to sleep", async ({ page }) => {
  await signIn(page, "dana");
  const row = page.locator('tr[data-agent="tom-becker"]');
  if (await row.getByRole("button", { name: "Sleep now" }).isVisible()) {
    await row.getByRole("button", { name: "Sleep now" }).click();
  }
  await expect(row).toContainText("Asleep", { timeout: 60_000 });
  await row.getByRole("button", { name: "Wake" }).click();
  await expect(row).toContainText("Awake", { timeout: 150_000 });
  await row.getByRole("button", { name: "Sleep now" }).click();
  await expect(row).toContainText("Asleep", { timeout: 60_000 });
});

test("a person sees only their own cases", async ({ page }) => {
  await signIn(page, "elena");
  await page.getByRole("link", { name: "Cases" }).click();
  await expect(page.getByRole("link", { name: "Care of Harold Whitaker" })).toBeVisible();
  await expect(page.getByText("Disputed claim CLM-2026-00672")).toHaveCount(0);
  await page.goto("/cases/dn-clm-2026-00672");
  await expect(page.getByRole("heading", { name: "Case not found" })).toBeVisible();

  await signIn(page, "priya");
  await page.goto("/cases/dn-clm-2026-00672");
  await expect(page.getByRole("heading", { name: /Disputed claim CLM-2026-00672/ })).toBeVisible();
  await expect(page.getByText("Tom Becker")).toBeVisible();
  await page.goto("/cases/ep-pat-0001");
  await expect(page.getByRole("heading", { name: "Case not found" })).toBeVisible();
});

test("policies are editable and the audit trail is searchable", async ({ page }) => {
  const before = await apiAs(page, "dana", "/admin/policies");
  await signIn(page, "dana");
  await page.getByRole("link", { name: "Policies" }).click();
  const save = page.getByRole("button", { name: "Save rules" });
  await expect(save).toBeDisabled();
  await page.getByRole("row").filter({ hasText: "Progress of a dispute" }).getByRole("combobox").nth(3)
    .selectOption("required");
  await expect(save).toBeEnabled();
  await save.click();
  await expect(save).toBeDisabled();
  const after = await apiAs(page, "dana", "/admin/policies");
  expect(after.rules.find((r: any) => r.note === "Progress of a dispute.").approval).toBe("required");
  await apiAs(page, "dana", "/admin/policies", before.rules, "PUT");

  await page.getByRole("link", { name: "Audit" }).click();
  await page.getByLabel("Kind").selectOption("policy");
  await expect(page.getByTestId("audit")).toContainText("changed the collaboration rules");
});

test("schedules can be added, paused and removed", async ({ page }) => {
  await signIn(page, "tom");
  await page.getByRole("link", { name: "Schedules" }).click();
  await page.getByLabel("Name").fill("E2E check");
  await page.getByLabel(/Cron/).fill("0 6 * * 1");
  await page.getByLabel("What the agent should do").fill("Report the month-end status.");
  await page.getByRole("button", { name: "Add job" }).click();
  const row = page.getByRole("row").filter({ hasText: "E2E check" });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Pause" }).click();
  await expect(row).toContainText("Paused");
  await row.getByRole("button", { name: "Remove" }).click();
  await expect(row).toHaveCount(0);
});
