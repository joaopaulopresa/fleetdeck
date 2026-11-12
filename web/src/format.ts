// Small formatting helpers shared by the pages.

export const usd = (value: number, digits = 4) =>
  `$${value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

export const count = (value: number) => value.toLocaleString("en-US");

export const mib = (bytes: number) => `${Math.round(bytes / 1048576).toLocaleString("en-US")} MiB`;

export const gib = (bytes: number) => `${(bytes / 1073741824).toFixed(1)} GiB`;

export function when(iso: string | null): string {
  if (!iso) return "never";
  const date = new Date(iso);
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds >= 0 && seconds < 60) return "just now";
  if (seconds >= 0 && seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** "mcp__patient_record__get_patient_summary" -> "Patient record: get patient summary" */
export function toolLabel(tool: string): string {
  const parts = tool.split("__");
  const words = (text: string) => text.replace(/[_-]/g, " ");
  if (parts[0] === "mcp" && parts.length >= 3) {
    const system = words(parts[1] ?? "");
    return `${system.charAt(0).toUpperCase()}${system.slice(1)}: ${words(parts.slice(2).join(" "))}`;
  }
  return words(tool);
}

export const REQUEST_TYPES: Record<string, string> = {
  ask_information: "Question",
  request_review: "Review",
  hand_over_task: "Hand-over",
  notify: "Notice",
};

export const STATUS_WORDS: Record<string, string> = {
  delivered: "With their agent",
  awaiting_approval: "Waiting for approval",
  answered: "Answered",
  declined: "Declined",
  denied: "Refused",
  failed: "Failed",
};
