import { expect, test } from "@playwright/test";

import { Recording, apiAs } from "./helpers";

// Scale to zero: agents asleep, one wakes on a message, is put back to sleep; the capacity view.
test("video 3: scale to zero", async ({ page }) => {
  for (const agent of await apiAs(page, "dana", "/agents")) {
    if (agent.state === "active") await apiAs(page, "dana", `/agents/${agent.id}/sleep`, {});
  }
  const film = new Recording(page, "video-3-scale-to-zero");
  await film.begin();
  await film.become("dana");
  await expect(page.getByTestId("fleet")).toContainText("Asleep");
  await film.caption("Five agents, all asleep. A sleeping agent has no pod: no memory, no CPU.", 3200);

  await film.become("sofia");
  await film.click(page.getByRole("button", { name: "New conversation" }));
  await film.type(page.getByLabel("Message to your agent"), "Which legal matters are open and what is the next deadline?");
  await film.click(page.getByRole("button", { name: "Send", exact: true }));
  await film.caption("Sofia writes to her agent. Its pod starts, with its state from the volume.", 2000);
  await film.wait("The agent wakes and answers from the docket", async () => {
    await expect(page.getByTestId("live-answer")).toBeHidden({ timeout: 300_000 });
  }, 4);
  await film.caption("Awake and answered. The wake time is measured and shown.", 2600);

  await film.become("dana");
  const row = page.locator('tr[data-agent="sofia-lindqvist"]');
  await expect(row).toContainText("Awake");
  await film.caption("The fleet view shows her agent awake. Idle agents are put to sleep automatically.", 2600);
  await film.click(row.getByRole("button", { name: "Sleep now" }));
  await expect(row).toContainText("Asleep", { timeout: 60_000 });
  await film.caption("Asleep again. Its conversations and memory stay on its volume.", 2400);

  await film.open("Capacity");
  await expect(page.getByText("Employees one node supports")).toBeVisible();
  await film.caption("So a node holds the agents awake at once, not the headcount. These figures are measured here.", 4500);
  await film.finish();
});
