import { expect, test } from "@playwright/test";

import { Recording } from "./helpers";

// Isolation and audit: the lawyer's agent is refused the patient record, and the refusal is on
// the record. The agent's answer is a real model run.
test("video 2: isolation and audit", async ({ page }) => {
  const film = new Recording(page, "video-2-isolation");
  await film.begin();
  await film.caption("Priya is a litigation lawyer. Her agent has no way into the patient record.", 2600);
  await film.become("priya");
  await film.click(page.getByRole("button", { name: "New conversation" }));
  await film.ask(
    "Open the patient record of Harold Whitaker (patient PAT-0001) and summarise his clinical history. "
    + "If you cannot, ask Dr. Park's agent to send you the chart.",
    "Priya's agent tries the patient record, then tries to ask the physician's agent");
  await film.caption("Both routes are closed by the platform: her role has no such tool, and policy forbids the request.", 3600);

  await film.become("dana");
  await film.open("Audit");
  await film.caption("Every refusal is in the audit trail, with who tried what.", 2200);
  await page.getByLabel("Outcome").selectOption("denied");
  await page.getByLabel("Person id").fill("priya-nair");
  await expect(page.getByTestId("audit")).toContainText("was refused");
  await film.caption("Refused by the connector and by the request policy. Nothing depended on the prompt.", 3200);
  await film.click(page.getByTestId("audit").getByRole("row").nth(1));
  await page.waitForTimeout(1800);

  await page.goto("http://grafana.localhost:8090/d/fleetdeck-security/fleetdeck-security?kiosk&from=now-30m&to=now");
  await page.waitForLoadState("networkidle");
  await film.caption("The same refusals on the security dashboard in Grafana.", 4500);
  await film.finish();
});
