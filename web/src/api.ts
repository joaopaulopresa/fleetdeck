// Typed access to the control-plane API.

export interface Person { id: string; name: string; email: string; title: string; role: string; department: string; is_admin: boolean; agent_id?: string | null }
export interface Agent { id: string; person_id: string | null; display_name: string; bundle: string; tier: string; state: string; suspended: boolean; created_at: string; last_activity_at: string | null; last_wake_seconds: number | null; wake_count: number }
export interface Session { id: string; kind: string; title: string; case_id: string | null; data_class: string }
export interface Message { id: number; role: string; content: string; run_id: string | null }
export interface Approval { id: string; kind: string; case_id: string | null; system: string; ref: string; title: string; body: string; status: string; created_at: string; decided_at: string | null; detail: Record<string, unknown> }
export interface AgentRequest { id: string; type: string; from_person: string | null; to_person: string | null; case_id: string; subject?: string; body?: string; status: string; reason: string; reply_body?: string | null; approval?: string; created_at: string; answered_at: string | null }
export interface CaseSummary { id: string; kind: string; title: string; summary: string; data_class: string; status: string; members?: string[]; documents?: number }
export interface DocumentInfo { id: string; case_id: string | null; title: string; kind: string; data_class: string; status: string; created_at: string; body?: string }
export interface CaseDetail extends Omit<CaseSummary, "members" | "documents"> { members: { person_id: string; name: string; title: string; role_in_case: string }[]; documents: DocumentInfo[] }
export interface Schedule { id: string; name: string; cron: string; prompt: string; enabled: boolean; next_run_at: string | null; last_run_at: string | null; last_run_id: string | null }
export interface Skill { id: string; name: string; description: string; body: string; scope: string; owner_person_id: string | null; roles: string[]; status: string; created_at: string | null }
export interface PolicyRule { request_type: string; from_role: string; to_role: string; approval: string; grants_membership: boolean; note: string }
export interface Permission { tool: string; role: string; scope: string }
export interface SystemInfo { name: string; title: string; description: string; data_class: string; permissions: Permission[] }
export interface Bundle { name: string; description: string; connectors: string[]; skills: string[]; schedules: string[]; disabled_toolsets: string[] }
export interface AuditEvent { id: number; at: string; kind: string; action: string; outcome: string; person_id: string | null; agent_id: string | null; case_id: string | null; system: string | null; run_id: string | null; trace_id: string | null; summary: string; detail: Record<string, unknown> }
export interface UsageRow { calls: number; input_tokens: number; output_tokens: number; cost_usd: number; [key: string]: string | number | null }
export interface Usage { days: number; totals: UsageRow; by_person: UsageRow[]; by_department: UsageRow[]; by_model: UsageRow[]; by_task_type: UsageRow[]; by_data_class: UsageRow[]; daily: UsageRow[] }
export interface Capacity {
  agents: Record<string, number>; idle_seconds: number; sleeps: number; measured: boolean; memory_source: string;
  cold_starts: { count: number; median_seconds: number | null; max_seconds: number | null; recent: { agent_id: string; at: string; seconds: number; trigger: string }[] };
  node: { memory_bytes: number | null; cpu_cores: number | null; memory_used_bytes: number | null };
  awake_pods: { agent_id: string; memory_bytes: number; cpu_cores: number }[];
  memory_per_awake_agent_bytes: number;
  estimate: { always_on_agents: number | null; by_active_share: { active_share: number; employees: number }[]; formula?: string };
}
export interface Config { company: string; langfuse_trace_url: string; grafana_url: string; default_model: string; restricted_model: string; idle_seconds: number }
export type RunEvent =
  | { type: "status"; status: string; wake_seconds?: number }
  | { type: "delta"; text: string }
  | { type: "tool"; phase: string; tool: string; error: boolean }
  | { type: "completed"; output: string }
  | { type: "failed"; error: string };

const TOKEN_KEY = "fleetdeck.token";
export const getToken = () => sessionStorage.getItem(TOKEN_KEY);
export const setToken = (token: string | null) =>
  token ? sessionStorage.setItem(TOKEN_KEY, token) : sessionStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const token = getToken();
  const response = await fetch(`/api${path}`, {
    method: options.method ?? (options.body === undefined ? "GET" : "POST"),
    headers: {
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "The request could not be completed.";
    throw new ApiError(response.status, detail);
  }
  return data as T;
}

/** Follow a run's events until it completes or fails. */
export async function followRun(runId: string, onEvent: (event: RunEvent) => void): Promise<void> {
  const response = await fetch(`/api/runs/${runId}/events`, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!response.ok || !response.body) throw new ApiError(response.status, "Could not follow the run.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const line = buffer.slice(0, end).trim();
      buffer = buffer.slice(end + 2);
      if (line.startsWith("data:")) onEvent(JSON.parse(line.slice(5)) as RunEvent);
    }
  }
}
