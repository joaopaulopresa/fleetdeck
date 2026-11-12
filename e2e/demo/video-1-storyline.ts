import { expect, test } from "@playwright/test";

import { Recording, apiAs } from "./helpers";

const CLAIM = "CLM-2026-01187";

// The storyline: an insurer denial that becomes a dispute, across three departments.
// Every agent answer in this recording is a real model run on the local cluster.
test("video 1: the storyline", async ({ page }) => {
  await apiAs(page, "dana", "/admin/demo/reset", {});
  const film = new Recording(page, "video-1-storyline");
  await film.begin();

  // 1. Billing picks up the denial.
  await film.caption("Meridian Health Group: every employee has a personal agent, isolated and asleep when idle.", 2600);
  await film.become("marcus");
  await film.caption("Marcus, a medical biller, asks his agent to work a claim the insurer denied.");
  await film.ask(`Work the denied claim ${CLAIM}.`, "Marcus's agent wakes, reads the claim and asks the physician's agent");
  await film.caption("It needs a clinical justification, so it sent a typed request to Dr. Park's agent.", 2600);

  // 2. The physician's agent drafts the justification; she approves what leaves.
  await film.become("elena");
  await film.openApprovals("Dr. Park's agent wakes on the request and reads the chart on the restricted model route");
  await film.caption("Her agent wrote the justification from the patient record. Only this summary may leave the clinic.", 3200);
  await film.click(page.getByRole("button", { name: "Send reply" }));
  await film.caption("Dr. Park approves it. The chart itself never leaves.", 2000);

  // 3. The appeal, then the second denial and the hand-over to Legal.
  await film.become("marcus");
  await film.openApprovals("Marcus's agent receives the reply and prepares the appeal");
  await film.caption("The appeal quotes the contract and the physician's justification. It is a draft until Marcus confirms.", 3000);
  await film.click(page.getByRole("button", { name: "Confirm", exact: true }).first());
  await film.become("dana");
  await film.open("Demo controls");
  await film.caption("The insurer is simulated here: it denies the appeal.");
  await film.click(page.getByRole("button", { name: "Payer denies the appeal" }));
  await film.become("marcus");
  await film.ask(`The payer has denied our appeal of ${CLAIM}. Check the claim and do what our rules require.`,
    "Marcus's agent checks the escalation rule, writes a dossier and hands the dispute to Legal");
  await film.open("Approvals");
  await film.caption("The dossier holds what a legal case may see: no patient record.", 2600);
  await film.click(page.getByRole("button", { name: "Confirm", exact: true }).first());

  // 4. Legal opens the matter and drafts the formal notice.
  await film.become("priya");
  await film.openApprovals("Priya's agent, now a member of the case, reads the dossier and the contract");
  await film.wait("Priya's agent prepares the legal matter and computes the notice deadline", () =>
    expect(page.getByRole("button", { name: "Send reply" })).toBeVisible({ timeout: 400_000 }));
  await film.caption("The deadline comes from the docket's rules, computed by code, not by the model.", 3000);
  await film.click(page.getByRole("button", { name: "Confirm", exact: true }).first());
  await film.click(page.getByRole("button", { name: "Send reply" }));
  await film.open("My agent");
  await film.ask(`Send Northwind a formal notice of dispute for claim ${CLAIM}. Prepare it for my review.`,
    "Priya's agent drafts the notice from the contract clauses and notifies Tax");
  await film.open("Approvals");
  await film.caption("The notice is a draft. Priya reviews it and sends it.", 2400);
  await film.click(page.getByRole("button", { name: "Confirm", exact: true }).first());

  // 5. Tax adjusts the month-end treatment.
  await film.become("tom");
  await film.openApprovals("Tom's agent wakes on the notice and prepares the ledger change");
  await film.caption("Tom confirms that the revenue is disputed. The tax engine recomputes.", 2400);
  await film.click(page.getByRole("button", { name: "Confirm", exact: true }).first());
  await film.open("My agent");
  await film.ask("What is the month-end treatment for 2026-09 now?", "Tom's agent runs the tax engine");
  await film.caption("Reserve and provision are the tax engine's figures, with their source.", 2800);

  // 6. The director's question.
  await film.become("sofia");
  await film.ask("How much do we have in disputed denials, and at what stage?", "Sofia's agent asks the billing system");
  await expect(page.getByTestId("thread")).toContainText("$109,950.00");
  await film.caption("The answer comes from the systems, with the claims behind every figure.", 3500);
  await film.finish();
});
