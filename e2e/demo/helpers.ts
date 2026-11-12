import { type Page, expect } from "@playwright/test";

export const PEOPLE = {
  elena: "Dr. Elena Park",
  marcus: "Marcus Reyes",
  priya: "Priya Nair",
  tom: "Tom Becker",
  sofia: "Sofia Lindqvist",
  dana: "Dana Whitfield",
} as const;
export type Who = keyof typeof PEOPLE;

/** Sign in as one of the demo people through the sign-in screen. */
export async function signIn(page: Page, who: Who): Promise<void> {
  await page.goto("/");
  if (await page.getByRole("button", { name: "Sign out" }).isVisible().catch(() => false)) {
    await page.getByRole("button", { name: "Sign out" }).click();
  }
  await page.getByRole("button", { name: new RegExp(PEOPLE[who]) }).click();
  await expect(page.getByRole("navigation", { name: "Main" })).toContainText(PEOPLE[who]);
}

/** Call the API as a person, outside the browser. */
export async function apiAs(page: Page, who: Who, path: string, body?: unknown, method?: string): Promise<any> {
  const id = PEOPLE[who].replace("Dr. ", "").toLowerCase().replace(" ", "-");
  const login = await page.request.post("/api/auth/login", { data: { person_id: id } });
  const { token } = await login.json();
  const response = await page.request.fetch(`/api${path}`, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers: { Authorization: `Bearer ${token}` },
    data: body,
  });
  return response.status() === 204 ? null : response.json();
}

// --- Recording helpers ----------------------------------------------------------------

import { writeFileSync } from "node:fs";

interface Segment { from: number; to: number; speed: number }

/** Keeps the timeline of a recording: which stretches are waiting and can be sped up. */
export class Recording {
  private started = Date.now();
  private segments: Segment[] = [];
  constructor(private page: Page, private name: string) {}

  /** Call right after the page (and so the video) starts. */
  async begin(): Promise<void> {
    this.started = Date.now();
    await this.page.addInitScript(OVERLAY);
    await this.page.goto("/");
  }

  /** Show a caption at the bottom of the frame. */
  async caption(text: string, holdMs = 1800): Promise<void> {
    await this.page.evaluate((value) => (window as any).__caption(value), text);
    await this.page.waitForTimeout(holdMs);
  }

  /** Wait for something slow (an agent working); the renderer speeds this stretch up. */
  async wait<T>(label: string, work: () => Promise<T>, speed = 8): Promise<T> {
    await this.page.evaluate((value) => (window as any).__caption(value), `${label} (shown at ${speed}x speed)`);
    await this.page.waitForTimeout(400);
    const from = (Date.now() - this.started) / 1000;
    try {
      return await work();
    } finally {
      this.segments.push({ from, to: (Date.now() - this.started) / 1000, speed });
    }
  }

  /** Move the pointer to an element in a visible, unhurried way, then click it. */
  async click(target: ReturnType<Page["locator"]>): Promise<void> {
    await target.scrollIntoViewIfNeeded();
    const box = await target.boundingBox();
    if (box) await this.page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 22 });
    await this.page.waitForTimeout(250);
    await target.click();
    await this.page.waitForTimeout(500);
  }

  async type(target: ReturnType<Page["locator"]>, text: string): Promise<void> {
    await this.click(target);
    await target.pressSequentially(text, { delay: 22 });
    await this.page.waitForTimeout(400);
  }

  /** Switch person through the interface, without reloading the page. */
  async become(who: Who): Promise<void> {
    const signOut = this.page.getByRole("button", { name: "Sign out" });
    if (await signOut.isVisible().catch(() => false)) await this.click(signOut);
    await this.click(this.page.getByRole("button", { name: new RegExp(PEOPLE[who]) }));
    await expect(this.page.getByRole("navigation", { name: "Main" })).toContainText(PEOPLE[who]);
  }

  async open(section: string): Promise<void> {
    await this.click(this.page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: section }));
  }

  /** Send a chat message and wait until the agent has answered. */
  async ask(text: string, label: string): Promise<void> {
    await this.type(this.page.getByLabel("Message to your agent"), text);
    await this.click(this.page.getByRole("button", { name: "Send", exact: true }));
    await this.wait(label, async () => {
      await expect(this.page.getByTestId("live-answer")).toBeVisible({ timeout: 30_000 });
      await expect(this.page.getByTestId("live-answer")).toBeHidden({ timeout: 300_000 });
    });
    await this.page.waitForTimeout(600);
  }

  /** Wait until something is waiting on the approvals page, then open it. */
  async openApprovals(label: string): Promise<void> {
    await this.wait(label, () => expect(this.page.getByTestId("badge-approvals")).toBeVisible({ timeout: 400_000 }));
    await this.open("Approvals");
    await expect(this.page.getByTestId("approval").first()).toBeVisible();
  }

  async finish(): Promise<void> {
    await this.page.waitForTimeout(1500);
    const total = (Date.now() - this.started) / 1000;
    writeFileSync(`output/${this.name}.segments.json`, JSON.stringify({ total, segments: this.segments }));
  }
}

// A visible pointer and a caption bar, injected into every page of a recording.
const OVERLAY = `
window.addEventListener("DOMContentLoaded", () => {
  const style = document.createElement("style");
  style.textContent = \`
    #__pointer { position: fixed; z-index: 99999; width: 18px; height: 18px; margin: -9px 0 0 -9px;
      border-radius: 50%; background: rgba(11,110,110,.35); border: 2px solid #0b6e6e; pointer-events: none; }
    #__caption { position: fixed; z-index: 99998; left: 50%; bottom: 22px; transform: translateX(-50%);
      max-width: 980px; padding: 10px 20px; border-radius: 8px; background: rgba(22,36,46,.94); color: #fff;
      font: 500 17px/1.35 "IBM Plex Sans", system-ui, sans-serif; text-align: center; pointer-events: none; }
    #__caption:empty { display: none; }\`;
  document.head.appendChild(style);
  const pointer = Object.assign(document.createElement("div"), { id: "__pointer" });
  const caption = Object.assign(document.createElement("div"), { id: "__caption" });
  document.body.append(pointer, caption);
  document.addEventListener("mousemove", (event) => {
    pointer.style.left = event.clientX + "px";
    pointer.style.top = event.clientY + "px";
  }, true);
});
window.__caption = (text) => { const node = document.getElementById("__caption"); if (node) node.textContent = text; };
`;
