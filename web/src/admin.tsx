import { type FormEvent, useEffect, useState } from "react";

import {
  type Agent, type AgentRequest, type AuditEvent, type Bundle, type Capacity, type CaseSummary, type Permission,
  type Person, type PolicyRule, type Skill, type SystemInfo, type Usage, type UsageRow, api,
} from "./api";
import { useSession } from "./App";
import { AgentState, Bars, Empty, ErrorNote, Loading, Page, Tag, useAction, useLoad } from "./components";
import { REQUEST_TYPES, STATUS_WORDS, clock, count, gib, mib, usd, when } from "./format";

const ROLES = ["physician", "biller", "lawyer", "tax-analyst", "director"];

/** Every agent with its state; provision, wake, sleep, suspend and delete. */
export function Fleet() {
  const agents = useLoad(() => api<Agent[]>("/agents"), [], 2000);
  const people = useLoad(() => api<Person[]>("/admin/people"), [], 5000);
  const bundles = useLoad(() => api<Bundle[]>("/admin/bundles"), []);
  const [form, setForm] = useState({ person_id: "", bundle: "" });
  const { busy, error, run } = useAction();
  const act = (path: string, method = "POST") => void run(() => api(path, { method })).then(() => agents.reload());
  const without = people.data?.filter((person) => !person.agent_id && !person.is_admin) ?? [];
  const names = Object.fromEntries((people.data ?? []).map((person) => [person.id, person]));
  const provision = (event: FormEvent) => {
    event.preventDefault();
    void run(() => api("/agents", { body: { agent_id: form.person_id, person_id: form.person_id, bundle: form.bundle } }))
      .then((ok) => { if (ok) { setForm({ person_id: "", bundle: "" }); void agents.reload(); void people.reload(); } });
  };
  const states = (agents.data ?? []).reduce<Record<string, number>>((all, agent) => ({ ...all, [agent.state]: (all[agent.state] ?? 0) + 1 }), {});
  return (
    <Page title="Fleet" lede="One agent per employee, each in its own namespace with its own volume, keys and network policy. Idle agents are scaled to zero and wake on demand.">
      <ErrorNote message={agents.error ?? error} />
      <div className="tiles">
        <div className="tile"><b>{agents.data?.length ?? "–"}</b><span>agents</span></div>
        <div className="tile"><b>{states.active ?? 0}</b><span>awake now</span></div>
        <div className="tile"><b>{states.asleep ?? 0}</b><span>asleep, using no memory</span></div>
        <div className="tile"><b>{states.waking ?? 0}</b><span>waking</span></div>
      </div>
      {!agents.data ? <Loading /> : (
        <table data-testid="fleet">
          <thead><tr><th>Person</th><th>Role bundle</th><th>State</th><th>Last activity</th><th className="num">Last wake</th><th className="num">Wakes</th><th /></tr></thead>
          <tbody>
            {agents.data.map((agent) => (
              <tr key={agent.id} data-agent={agent.id}>
                <td>{names[agent.person_id ?? ""]?.name ?? agent.display_name}<div className="muted small">{names[agent.person_id ?? ""]?.department ?? "Service agent"}, namespace agent-{agent.id}</div></td>
                <td>{agent.bundle}</td>
                <td><AgentState state={agent.state} /></td>
                <td>{when(agent.last_activity_at)}</td>
                <td className="num">{agent.last_wake_seconds != null ? `${agent.last_wake_seconds.toFixed(1)} s` : "–"}</td>
                <td className="num">{agent.wake_count}</td>
                <td className="actions">
                  {agent.suspended ? (
                    <button disabled={busy} onClick={() => act(`/agents/${agent.id}/resume`)}>Resume</button>
                  ) : (
                    <>
                      {agent.state === "active"
                        ? <button disabled={busy} onClick={() => act(`/agents/${agent.id}/sleep`)}>Sleep now</button>
                        : <button disabled={busy || agent.state === "waking"} onClick={() => act(`/agents/${agent.id}/wake`)}>Wake</button>}
                      <button disabled={busy} onClick={() => act(`/agents/${agent.id}/suspend`)}>Suspend</button>
                    </>
                  )}
                  <button className="danger" disabled={busy} onClick={() => { if (confirm(`Delete ${agent.id} with its namespace, volume and keys?`)) act(`/agents/${agent.id}`, "DELETE"); }}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2>Provision an agent</h2>
      {without.length === 0 ? <Empty>Every employee already has an agent.</Empty> : (
        <form className="panel pad row" onSubmit={provision}>
          <label>Employee
            <select required value={form.person_id} onChange={(event) => setForm({ ...form, person_id: event.target.value })}>
              <option value="">Choose</option>
              {without.map((person) => <option key={person.id} value={person.id}>{person.name}, {person.title}</option>)}
            </select>
          </label>
          <label>Role bundle
            <select required value={form.bundle} onChange={(event) => setForm({ ...form, bundle: event.target.value })}>
              <option value="">Choose</option>
              {bundles.data?.map((bundle) => <option key={bundle.name} value={bundle.name}>{bundle.name}: {bundle.connectors.length} connectors, {bundle.skills.length} skills</option>)}
            </select>
          </label>
          <button className="primary" disabled={busy}>Provision agent</button>
        </form>
      )}
    </Page>
  );
}

/** Who may send which type of request to whom, and whether the reply needs approval. */
export function Policies() {
  const loaded = useLoad(() => api<{ request_types: string[]; rules: PolicyRule[] }>("/admin/policies"), []);
  const requests = useLoad(() => api<AgentRequest[]>("/admin/requests"), [], 5000);
  const [rules, setRules] = useState<PolicyRule[] | null>(null);
  const { busy, error, run } = useAction();
  useEffect(() => { if (loaded.data && rules === null) setRules(loaded.data.rules); }, [loaded.data, rules]);
  const change = (index: number, patch: Partial<PolicyRule>) => setRules((current) => current!.map((rule, at) => (at === index ? { ...rule, ...patch } : rule)));
  const dirty = JSON.stringify(rules) !== JSON.stringify(loaded.data?.rules);
  const save = () => void run(() => api("/admin/policies", { method: "PUT", body: rules })).then(() => loaded.reload());
  return (
    <Page title="Policies" lede="Agents exchange typed requests through the broker. A request is refused unless a rule here allows that role to send that type to the recipient's role. By default the recipient's person approves the reply."
      aside={<button className="primary" disabled={!dirty || busy} onClick={save}>Save rules</button>}>
      <ErrorNote message={loaded.error ?? error} />
      {!rules ? <Loading /> : (
        <table>
          <thead><tr><th>Request type</th><th>From role</th><th>To role</th><th>Reply</th><th>Recipient joins the case</th><th>Why</th><th /></tr></thead>
          <tbody>
            {rules.map((rule, index) => (
              <tr key={index}>
                <td><select value={rule.request_type} onChange={(event) => change(index, { request_type: event.target.value })}>{loaded.data!.request_types.map((type) => <option key={type} value={type}>{REQUEST_TYPES[type]}</option>)}</select></td>
                <td><select value={rule.from_role} onChange={(event) => change(index, { from_role: event.target.value })}>{ROLES.map((role) => <option key={role}>{role}</option>)}</select></td>
                <td><select value={rule.to_role} onChange={(event) => change(index, { to_role: event.target.value })}>{ROLES.map((role) => <option key={role}>{role}</option>)}</select></td>
                <td><select value={rule.approval} onChange={(event) => change(index, { approval: event.target.value })}><option value="required">Approved by the person</option><option value="auto">Released automatically</option></select></td>
                <td><input type="checkbox" checked={rule.grants_membership} onChange={(event) => change(index, { grants_membership: event.target.checked })} aria-label="Recipient joins the case" /></td>
                <td className="small muted">{rule.note}</td>
                <td><button className="danger" onClick={() => setRules(rules.filter((_, at) => at !== index))}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p style={{ marginTop: 10 }}><button onClick={() => setRules([...(rules ?? []), { request_type: "ask_information", from_role: "biller", to_role: "physician", approval: "required", grants_membership: false, note: "" }])}>Add a rule</button></p>
      <h2>Recent requests</h2>
      <p className="muted small">Who asked whom, about which case, and the outcome. Subjects and content stay with the people involved.</p>
      {!requests.data?.length ? <Empty>No requests have been sent yet.</Empty> : (
        <table>
          <thead><tr><th>When</th><th>Type</th><th>From</th><th>To</th><th>Case</th><th>Outcome</th></tr></thead>
          <tbody>
            {requests.data.slice(0, 25).map((item) => (
              <tr key={item.id}>
                <td>{when(item.created_at)}</td><td>{REQUEST_TYPES[item.type]}</td><td>{item.from_person}</td><td>{item.to_person}</td><td>{item.case_id}</td>
                <td><Tag value={item.status} label={STATUS_WORDS[item.status]} />{item.reason && <div className="small muted">{item.reason}</div>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Page>
  );
}

/** Connected systems and which role may use which tool. */
export function Connectors() {
  const systems = useLoad(() => api<SystemInfo[]>("/admin/systems"), []);
  return (
    <Page title="Connectors" lede="One connector per corporate system. Every call carries the agent's own token; the role and the case decide whether it goes through, and both outcomes are audited.">
      <ErrorNote message={systems.error} />
      {!systems.data ? <Loading /> : systems.data.map((system) => <SystemMatrix key={system.name} system={system} onSaved={() => void systems.reload()} />)}
    </Page>
  );
}

function SystemMatrix({ system, onSaved }: { system: SystemInfo; onSaved: () => void }) {
  const [rows, setRows] = useState<Permission[]>(system.permissions);
  const { busy, error, run } = useAction();
  const tools = [...new Set(system.permissions.map((row) => row.tool))];
  const scope = (tool: string, role: string) => rows.find((row) => row.tool === tool && row.role === role)?.scope ?? "";
  const set = (tool: string, role: string, value: string) => setRows([
    ...rows.filter((row) => !(row.tool === tool && row.role === role)),
    ...(value ? [{ tool, role, scope: value }] : []),
  ]);
  const key = (list: Permission[]) => JSON.stringify([...list].sort((a, b) => `${a.tool}${a.role}`.localeCompare(`${b.tool}${b.role}`)));
  const dirty = key(rows) !== key(system.permissions);
  return (
    <section style={{ marginBottom: 26 }} data-system={system.name}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: "18px 0 4px" }}>{system.title} <Tag value={system.data_class} label={`${system.data_class} data`} /></h2>
        <button className="primary" disabled={!dirty || busy} onClick={() => void run(() => api(`/admin/systems/${system.name}/permissions`, { method: "PUT", body: rows })).then((ok) => ok && onSaved())}>Save</button>
      </div>
      <p className="muted small">{system.description}</p>
      <ErrorNote message={error} />
      <table className="matrix">
        <thead><tr><th>Tool</th>{ROLES.map((role) => <th key={role}>{role}</th>)}</tr></thead>
        <tbody>
          {tools.map((tool) => (
            <tr key={tool}>
              <td>{tool.replace(/_/g, " ")}</td>
              {ROLES.map((role) => (
                <td key={role}>
                  <select value={scope(tool, role)} onChange={(event) => set(tool, role, event.target.value)} aria-label={`${tool} for ${role}`}>
                    <option value="">No access</option><option value="case">Own cases</option><option value="all">All records</option>
                  </select>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/** Skills proposed by agents, and the library of approved ones. */
export function SkillLibrary() {
  const skills = useLoad(() => api<Skill[]>("/admin/skills"), [], 5000);
  const { busy, error, run } = useAction();
  const [roles, setRoles] = useState<Record<string, string[]>>({});
  const queue = skills.data?.filter((skill) => skill.scope === "shared" && skill.status === "proposed") ?? [];
  const library = skills.data?.filter((skill) => skill.status === "approved" && skill.scope !== "personal") ?? [];
  const personal = skills.data?.filter((skill) => skill.scope === "personal") ?? [];
  const decide = (skill: Skill, verb: string) => void run(() => api(`/admin/skills/${skill.id}/${verb}`, { body: { roles: roles[skill.id] ?? null } })).then(() => skills.reload());
  const toggle = (id: string, role: string) => setRoles((current) => {
    const chosen = current[id] ?? [];
    return { ...current, [id]: chosen.includes(role) ? chosen.filter((item) => item !== role) : [...chosen, role] };
  });
  return (
    <Page title="Skills" lede="Agents can propose a procedure for colleagues. Nothing enters the shared library without review here, and nothing is installed from a public skills hub.">
      <ErrorNote message={skills.error ?? error} />
      <h2 style={{ marginTop: 0 }}>Review queue</h2>
      {!skills.data ? <Loading /> : queue.length === 0 ? <Empty>No skill is waiting for review.</Empty> : queue.map((skill) => (
        <div className="panel pad" key={skill.id} style={{ marginBottom: 12 }} data-testid="skill-proposal">
          <h3>{skill.name}</h3>
          <p className="muted small">{skill.description} Proposed by the agent of {skill.owner_person_id}.</p>
          <pre className="doc">{skill.body}</pre>
          <div className="row" style={{ marginTop: 12 }}>
            <span className="small">Give it to:</span>
            {ROLES.map((role) => (
              <label key={role} style={{ display: "flex", gap: 5, alignItems: "center" }}>
                <input type="checkbox" checked={(roles[skill.id] ?? []).includes(role)} onChange={() => toggle(skill.id, role)} />{role}
              </label>
            ))}
            <button className="primary" disabled={busy} onClick={() => decide(skill, "approve")}>Approve for the library</button>
            <button className="danger" disabled={busy} onClick={() => decide(skill, "reject")}>Reject</button>
          </div>
        </div>
      ))}
      <h2>Library</h2>
      <table>
        <thead><tr><th>Skill</th><th>Source</th><th>Roles</th></tr></thead>
        <tbody>
          {library.map((skill) => (
            <tr key={skill.id}>
              <td><details className="rowdetail"><summary>{skill.name}</summary><pre className="doc" style={{ marginTop: 8 }}>{skill.body}</pre></details><div className="muted small">{skill.description}</div></td>
              <td>{skill.scope === "bundle" ? "Role bundle, in the repository" : "Reviewed proposal"}</td>
              <td>{skill.roles.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>Personal skills</h2>
      {personal.length === 0 ? <Empty>No agent has proposed a personal skill.</Empty> : (
        <table>
          <thead><tr><th>Skill</th><th>Owner</th><th>Status</th></tr></thead>
          <tbody>{personal.map((skill) => <tr key={skill.id}><td>{skill.name}<div className="muted small">{skill.description}</div></td><td>{skill.owner_person_id}</td><td><Tag value={skill.status} /></td></tr>)}</tbody>
        </table>
      )}
    </Page>
  );
}

function UsageTable({ title, rows, label }: { title: string; rows: UsageRow[]; label: (row: UsageRow) => string }) {
  return (
    <div>
      <h2>{title}</h2>
      <table>
        <thead><tr><th>{title.replace("By ", "")}</th><th className="num">Calls</th><th className="num">Tokens in</th><th className="num">Tokens out</th><th className="num">Cost</th></tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}><td>{label(row)}</td><td className="num">{count(row.calls)}</td><td className="num">{count(row.input_tokens)}</td><td className="num">{count(row.output_tokens)}</td><td className="num">{usd(row.cost_usd)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Model usage and cost, attributed to people. Shown, not capped. */
export function UsagePage() {
  const [days, setDays] = useState(30);
  const usage = useLoad(() => api<Usage>(`/admin/usage?days=${days}`), [days], 8000);
  const { config } = useSession();
  const data = usage.data;
  return (
    <Page title="Usage and cost" lede="Every model call passes the gateway with the agent's own key, so cost is attributed to a person, a department, a model and a data class. Cost is shown, not capped."
      aside={<label>Period<select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={1}>Last day</option><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option></select></label>}>
      <ErrorNote message={usage.error} />
      {!data ? <Loading /> : (
        <>
          <div className="tiles">
            <div className="tile"><b>{usd(data.totals.cost_usd)}</b><span>provider cost</span></div>
            <div className="tile"><b>{count(data.totals.calls)}</b><span>model calls</span></div>
            <div className="tile"><b>{count(data.totals.input_tokens)}</b><span>tokens in</span></div>
            <div className="tile"><b>{count(data.totals.output_tokens)}</b><span>tokens out</span></div>
          </div>
          <h2>Cost by person</h2>
          <Bars rows={data.by_person.map((row) => ({ label: String(row.name ?? row.person_id ?? "Service agents"), value: row.cost_usd }))} format={(value) => usd(value)} />
          <div className="grid2">
            <UsageTable title="By department" rows={data.by_department} label={(row) => String(row.department ?? "None")} />
            <UsageTable title="By data class" rows={data.by_data_class} label={(row) => String(row.data_class)} />
            <UsageTable title="By model" rows={data.by_model} label={(row) => `${row.model} (${row.route} route)`} />
            <UsageTable title="By task type" rows={data.by_task_type} label={(row) => ({ chat: "Chat with the person", request: "Request from a colleague's agent", schedule: "Scheduled job" }[String(row.trigger)] ?? "Outside a run")} />
          </div>
          <h2>Cost by day</h2>
          <Bars rows={data.daily.map((row) => ({ label: String(row.day), value: row.cost_usd }))} format={(value) => usd(value)} />
          <p className="muted small" style={{ marginTop: 14 }}>
            Default route: {config.default_model}. Restricted route: {config.restricted_model}, a labelled stand-in for a model hosted by the company.
          </p>
        </>
      )}
    </Page>
  );
}

/** Search the audit trail. */
export function Audit() {
  const { config } = useSession();
  const [filters, setFilters] = useState({ kind: "", outcome: "", person: "", system: "", case: "", q: "" });
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value)).toString();
  const events = useLoad(() => api<AuditEvent[]>(`/audit?limit=200&${query}`), [query], 5000);
  const [open, setOpen] = useState<number | null>(null);
  const field = (name: keyof typeof filters, label: string, options?: string[]) => (
    <label>{label}
      {options ? (
        <select value={filters[name]} onChange={(event) => setFilters({ ...filters, [name]: event.target.value })}>
          <option value="">Any</option>{options.map((option) => <option key={option}>{option}</option>)}
        </select>
      ) : <input value={filters[name]} onChange={(event) => setFilters({ ...filters, [name]: event.target.value })} />}
    </label>
  );
  return (
    <Page title="Audit" lede="Every model call, connector call, request, approval and egress attempt, allowed or refused. Records are append-only: the database rejects changes and deletions.">
      <div className="panel pad row" style={{ marginBottom: 14 }}>
        {field("kind", "Kind", ["model_call", "tool_call", "request", "approval", "policy", "egress", "agent", "schedule", "sign_in"])}
        {field("outcome", "Outcome", ["allowed", "denied", "info"])}
        {field("person", "Person id")}
        {field("system", "System", ["patient-record", "billing", "erp", "docket", "contracts", "knowledge", "fleet", "model-gateway", "egress-proxy"])}
        {field("case", "Case id")}
        {field("q", "Text")}
      </div>
      <ErrorNote message={events.error} />
      {!events.data ? <Loading /> : events.data.length === 0 ? <Empty>No audit record matches these filters.</Empty> : (
        <table data-testid="audit">
          <thead><tr><th>Time</th><th>Kind</th><th>Outcome</th><th>Person</th><th>System</th><th>What happened</th></tr></thead>
          <tbody>
            {events.data.map((event) => [
              <tr key={event.id} className="clickable" onClick={() => setOpen(open === event.id ? null : event.id)}>
                <td title={event.at}>{clock(event.at)}</td><td>{event.kind.replace(/_/g, " ")}</td><td><Tag value={event.outcome} /></td>
                <td>{event.person_id ?? event.agent_id ?? ""}</td><td>{event.system ?? ""}</td><td>{event.summary || event.action}</td>
              </tr>,
              open === event.id && (
                <tr key={`${event.id}-detail`}>
                  <td colSpan={6} className="small">
                    <p>Action {event.action}{event.case_id && <>, case {event.case_id}</>}{event.run_id && <>, run {event.run_id}</>}</p>
                    {event.trace_id && config.langfuse_trace_url && (
                      <p><a href={`${config.langfuse_trace_url}${event.trace_id}`} target="_blank" rel="noreferrer">Open the trace in Langfuse</a></p>
                    )}
                    <pre className="doc">{JSON.stringify(event.detail, null, 2)}</pre>
                  </td>
                </tr>
              ),
            ])}
          </tbody>
        </table>
      )}
    </Page>
  );
}

/** Agents by state, cold starts, measured usage and the capacity estimate. */
export function CapacityPage() {
  const { config } = useSession();
  const capacity = useLoad(() => api<Capacity>("/admin/capacity"), [], 4000);
  const [idle, setIdle] = useState<number | null>(null);
  const { busy, error, run } = useAction();
  const data = capacity.data;
  useEffect(() => { if (data && idle === null) setIdle(data.idle_seconds); }, [data, idle]);
  return (
    <Page title="Capacity" lede="A sleeping agent uses no memory and no CPU; only its small volume remains. So a node has to hold the agents that are awake at the same time, not the headcount."
      aside={config.grafana_url ? <a className="button" href={config.grafana_url} target="_blank" rel="noreferrer">Open Grafana</a> : undefined}>
      <ErrorNote message={capacity.error ?? error} />
      {!data ? <Loading /> : (
        <>
          <div className="tiles">
            <div className="tile"><b>{data.agents.active ?? 0} of {data.agents.total}</b><span>agents awake</span></div>
            <div className="tile"><b>{data.cold_starts.median_seconds != null ? `${data.cold_starts.median_seconds.toFixed(1)} s` : "–"}</b><span>median wake, last {data.cold_starts.count}</span></div>
            <div className="tile"><b>{mib(data.memory_per_awake_agent_bytes)}</b><span>memory per awake agent</span></div>
            <div className="tile"><b>{data.node.memory_bytes ? gib(data.node.memory_bytes) : "–"}</b><span>node memory{data.node.cpu_cores ? `, ${data.node.cpu_cores} cores` : ""}</span></div>
          </div>
          <p className="muted small">Memory per awake agent is {data.memory_source}.</p>
          <div className="grid2">
            <div>
              <h2>Employees one node supports</h2>
              {data.estimate.always_on_agents == null ? <Empty>Node size is not available yet: Prometheus has not scraped the node.</Empty> : (
                <>
                  <table>
                    <thead><tr><th>Share of employees active at once</th><th className="num">Employees per node</th></tr></thead>
                    <tbody>
                      {data.estimate.by_active_share.map((row) => (
                        <tr key={row.active_share}><td>{row.active_share === 1 ? "100% (always-on pods, no scale to zero)" : `${Math.round(row.active_share * 100)}%`}</td><td className="num">{count(row.employees)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="muted small" style={{ marginTop: 8 }}>An estimate from this cluster's measurements: {data.estimate.formula}.</p>
                </>
              )}
              <h2>Idle limit</h2>
              <form className="panel pad row" onSubmit={(event) => { event.preventDefault(); void run(() => api("/admin/settings", { method: "PUT", body: { idle_seconds: idle } })).then(() => capacity.reload()); }}>
                <label>Put an agent to sleep after this many idle seconds
                  <input type="number" min={20} max={86400} value={idle ?? ""} onChange={(event) => setIdle(Number(event.target.value))} />
                </label>
                <button className="primary" disabled={busy || idle === data.idle_seconds}>Save</button>
              </form>
            </div>
            <div>
              <h2>Awake pods now</h2>
              {data.awake_pods.length === 0 ? <Empty>No agent pod is running, or Prometheus has no sample yet.</Empty> : (
                <table>
                  <thead><tr><th>Agent</th><th className="num">Memory</th><th className="num">CPU cores</th></tr></thead>
                  <tbody>{data.awake_pods.map((pod) => <tr key={pod.agent_id}><td>{pod.agent_id}</td><td className="num">{mib(pod.memory_bytes)}</td><td className="num">{pod.cpu_cores.toFixed(3)}</td></tr>)}</tbody>
                </table>
              )}
              <h2>Recent wake-ups</h2>
              {data.cold_starts.recent.length === 0 ? <Empty>No agent has been woken yet.</Empty> : (
                <table>
                  <thead><tr><th>Agent</th><th>Woken by</th><th className="num">Took</th><th>When</th></tr></thead>
                  <tbody>
                    {data.cold_starts.recent.map((wake, index) => (
                      <tr key={index}><td>{wake.agent_id}</td><td>{{ chat: "its person", request: "a colleague's request", schedule: "a scheduled job", admin: "an administrator" }[wake.trigger] ?? wake.trigger}</td><td className="num">{Number(wake.seconds).toFixed(1)} s</td><td>{when(wake.at)}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </>
      )}
    </Page>
  );
}

/** Controls that exist only because this is a demo with synthetic data. */
export function Demo() {
  const cases = useLoad(() => api<CaseSummary[]>("/admin/cases"), [], 6000);
  const [claim, setClaim] = useState("CLM-2026-01187");
  const [message, setMessage] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  return (
    <Page title="Demo controls" lede="The insurer is not part of this installation, so its answers are simulated here. Everything in the demo is synthetic.">
      <ErrorNote message={error} />
      {message && <div className="notice">{message}</div>}
      <h2 style={{ marginTop: 0 }}>Simulate the payer's decision on an appeal</h2>
      <form className="panel pad row" onSubmit={(event) => {
        event.preventDefault();
        void run(() => api("/admin/demo/appeal-decision", { body: { claim_id: claim, outcome: "upheld" } }))
          .then((ok) => ok && setMessage(`The payer upheld its denial of ${claim}. The claim is now "appeal denied".`));
      }}>
        <label>Claim with an open appeal<input value={claim} onChange={(event) => setClaim(event.target.value)} /></label>
        <button className="primary" disabled={busy}>Payer denies the appeal</button>
      </form>
      <h2>Reset the demo</h2>
      <div className="panel pad">
        <p>Restores the corporate systems, cases and documents to the seed and clears requests, approvals and conversations. Agents, usage history and the audit trail are kept.</p>
        <button className="danger" disabled={busy} onClick={() => { if (confirm("Reset the demo data to the seed?")) void run(() => api("/admin/demo/reset", { method: "POST" })).then((ok) => ok && setMessage("Demo data restored to the seed.")); }}>Reset demo data</button>
      </div>
      <h2>Case spaces</h2>
      <p className="muted small">Membership only. Administrators do not see what a case contains.</p>
      {!cases.data ? <Loading /> : (
        <table>
          <thead><tr><th>Case</th><th>Data class</th><th>Members</th><th className="num">Documents</th></tr></thead>
          <tbody>
            {cases.data.filter((item) => item.kind !== "patient_episode" || (item.members?.length ?? 0) > 0).map((item) => (
              <tr key={item.id}><td>{item.title}<div className="muted small">{item.id}</div></td><td><Tag value={item.data_class} /></td><td>{item.members?.join(", ") || "none"}</td><td className="num">{item.documents}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </Page>
  );
}
