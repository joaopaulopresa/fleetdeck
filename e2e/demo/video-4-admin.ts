import { expect, test } from "@playwright/test";

import { Recording, apiAs } from "./helpers";

// Administration: provision a new employee's agent, change a policy, review a proposed skill.
test("video 4: administration", async ({ page }) => {
  await apiAs(page, "dana", "/agents/nora-castellanos", undefined, "DELETE").catch(() => null);
  const rules = (await apiAs(page, "dana", "/admin/policies")).rules;
  const film = new Recording(page, "video-4-admin");
  await film.begin();
  await film.become("dana");
  await film.caption("Nora joined Revenue Cycle today. The administrator provisions her agent.", 2400);
  await page.getByLabel("Employee").selectOption("nora-castellanos");
  await page.getByLabel("Role bundle").selectOption("biller");
  await page.waitForTimeout(700);
  await film.click(page.getByRole("button", { name: "Provision agent" }));
  const row = page.locator('tr[data-agent="nora-castellanos"]');
  await expect(row).toBeVisible({ timeout: 30_000 });
  await film.caption("A namespace, a volume, keys and a default-deny network policy now exist for her. The agent starts asleep.", 3400);
  await film.click(row.getByRole("button", { name: "Wake" }));
  await film.wait("Her agent starts for the first time", () => expect(row).toContainText("Awake", { timeout: 180_000 }), 4);

  await film.open("Policies");
  await film.caption("Policies decide who may ask whom for what, and whether the reply needs a person.", 2600);
  const rule = page.getByRole("row").filter({ hasText: "Progress of a dispute" });
  await rule.getByRole("combobox").nth(3).selectOption("required");
  await page.waitForTimeout(600);
  await film.click(page.getByRole("button", { name: "Save rules" }));
  await film.caption("Notices from Legal to Billing now need the lawyer's approval. It applies to the next request.", 3000);

  await film.become("marcus");
  await film.click(page.getByRole("button", { name: "New conversation" }));
  await film.ask(
    "Propose a shared skill named payer-call-notes for colleagues: when calling a payer about a claim, note the "
    + "call reference number, the representative's name and the date they promised to act.",
    "Marcus's agent proposes a skill for the shared library");
  await film.become("dana");
  await film.open("Skills");
  await expect(page.getByTestId("skill-proposal")).toBeVisible({ timeout: 30_000 });
  await film.caption("Nothing an agent writes becomes a shared skill by itself. The administrator reviews it.", 3000);
  await film.click(page.getByTestId("skill-proposal").getByLabel("biller"));
  await film.click(page.getByRole("button", { name: "Approve for the library" }));
  await film.caption("Approved for billers. Their agents receive it the next time they start.", 3000);
  await film.finish();

  await apiAs(page, "dana", "/admin/policies", rules, "PUT");
  await apiAs(page, "dana", "/agents/nora-castellanos", undefined, "DELETE");
});
