import { expect, test } from "@playwright/test";

import { Recording } from "./helpers";

// Isolation and audit: the lawyer's agent is refused the patient record, and the refusal is on
// the record. The agent's answer is a real model run.
test("video 2: isolation and audit", async ({ page }) => {
  const film = new Recording(page, "video-2-isolation");
  await film.begin();
  await film.caption("Priya is a litigation lawyer. Her agent is even wired to the patient-record connector.", 2800);
  await film.become("priya");
  await film.click(page.getByRole("button", { name: "New conversation" }));
  await film.ask("Use the patient record tools to open the chart of patient PAT-0001 and summarise it.",
    "Priya's agent calls the patient record");
  await expect(page.getByTestId("thread")).toContainText(/denied/i);
  await film.caption("Refused by the connector: the lawyer role has no permission there. The agent does not look for a way around.", 3800);
  await film.ask(
    "Different matter: send Dr. Elena Park's agent an ask_information request about case dn-clm-2026-00672, "
    + "asking whether she was the treating physician for that claim.",
    "Priya's agent sends a request to the physician's agent");
  await expect(page.getByTestId("thread")).toContainText(/denied|refused|policy/i);
  await film.caption("Refused by the broker: policy does not let Legal question the clinic directly.", 3400);

  await film.become("dana");
  await film.open("Audit");
  await page.getByLabel("Outcome").selectOption("denied");
  await page.getByLabel("Person id").fill("priya-nair");
  await expect(page.getByTestId("audit")).toContainText("was refused patient-record");
  await expect(page.getByTestId("audit")).toContainText("refused: policy");
  await film.caption("Both refusals are in the audit trail: the connector call and the request.", 3200);
  await film.click(page.getByTestId("audit").getByRole("row").filter({ hasText: "was refused patient-record" }).first());
  await film.caption("Decided by role and policy in the platform. Nothing depended on the agent's prompt.", 3200);

  // Prometheus scrapes every ten seconds; give it time to see the new refusals.
  await film.wait("Waiting for the next metrics scrape", () => page.waitForTimeout(16_000));
  await page.goto("http://grafana.localhost:8090/d/fleetdeck-security/fleetdeck-security?kiosk&from=now-30m&to=now");
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1500);
  await film.caption("The refusals counted on the security dashboard in Grafana.", 4500);
  await film.finish();
});
