import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  type Agent, type AgentRequest, type Approval, type CaseDetail, type CaseSummary, type DocumentInfo,
  type Message, type Person, type Schedule, type Session, type Skill, api, followRun,
} from "./api";
import { useSession } from "./App";
import { AgentState, Empty, ErrorNote, Loading, Page, Tag, useAction, useLoad } from "./components";
import { REQUEST_TYPES, STATUS_WORDS, toolLabel, when } from "./format";

const ROUTE_WORDS: Record<string, string> = {
  general: "General data: default model",
  confidential: "Confidential data: default model",
  restricted: "Restricted data: in-house model route",
};

interface Live { text: string; tools: { tool: string; done: boolean }[]; status: string; since: number }

/** The conversation with my own agent. Sending a message wakes it if it sleeps. */
export function Chat() {
  const { person } = useSession();
  const agent = useLoad(() => api<Agent>("/me/agent"), [], 3000);
  const sessions = useLoad(() => api<Session[]>("/me/sessions"), [], 5000);
  const [sessionId, setSessionId] = useState<string | null | undefined>(undefined);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [live, setLive] = useState<Live | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  // Open the most recent conversation once the list has loaded.
  useEffect(() => {
    if (sessionId === undefined && sessions.data) setSessionId(sessions.data[0]?.id ?? null);
  }, [sessions.data, sessionId]);

  const loadMessages = async (id: string) => setMessages(await api<Message[]>(`/me/sessions/${id}/messages`));
  useEffect(() => {
    if (!sessionId) { setMessages([]); return; }
    void loadMessages(sessionId);
    // Replies from colleagues' agents continue this conversation in the background.
    const timer = setInterval(() => { if (!live) void loadMessages(sessionId); }, 4000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, live === null]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages.length, live?.text, live?.tools.length]);

  const send = async (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || live) return;
    setDraft("");
    setFailure(null);
    setMessages((current) => [...current, { id: -Date.now(), role: "user", content: text, run_id: null }]);
    setLive({ text: "", tools: [], status: "waking", since: Date.now() });
    try {
      const started = await api<{ run_id: string; session_id: string }>("/me/chat", { body: { text, session_id: sessionId } });
      setSessionId(started.session_id);
      await followRun(started.run_id, (item) => {
        setLive((current) => {
          if (!current) return current;
          if (item.type === "status") return { ...current, status: item.status };
          if (item.type === "delta") return { ...current, text: current.text + item.text };
          if (item.type === "tool") {
            const tools = item.phase === "started"
              ? [...current.tools, { tool: item.tool, done: false }]
              : current.tools.map((entry) => (entry.tool === item.tool ? { ...entry, done: true } : entry));
            return { ...current, tools };
          }
          return current;
        });
        if (item.type === "failed") setFailure(`Your agent could not finish: ${item.error}`);
      });
      await loadMessages(started.session_id);
    } catch (caught) {
      setFailure(caught instanceof Error ? caught.message : "The message could not be sent.");
    } finally {
      setLive(null);
      void sessions.reload();
      void agent.reload();
    }
  };

  const current = sessions.data?.find((item) => item.id === sessionId);
  const state = live?.status === "waking" ? "waking" : agent.data?.state ?? "asleep";
  return (
    <Page title="My agent" lede={`${person.name}'s personal agent. It sleeps when idle and wakes when you write, when a colleague's agent sends a request, or when a scheduled job is due.`}>
      <ErrorNote message={agent.error ?? failure} />
      <div className="chat">
        <section>
          <div className="chat-head">
            <AgentState state={state} since={live?.since} />
            {agent.data?.last_wake_seconds != null && state !== "waking" && (
              <span className="muted small">last wake took {agent.data.last_wake_seconds.toFixed(1)} s</span>
            )}
            {current && <Tag value={current.data_class} label={ROUTE_WORDS[current.data_class]} />}
            <button className="quiet" onClick={() => { setSessionId(null); setMessages([]); }} disabled={Boolean(live)}>
              New conversation
            </button>
          </div>
          <div className="thread" data-testid="thread">
            {messages.length === 0 && !live && (
              <Empty>Write to your agent below. If it is asleep it starts first; that takes a few seconds.</Empty>
            )}
            {renderThread(messages)}
            {live && (
              <>
                {live.tools.length > 0 && (
                  <div className="tools">
                    {live.tools.map((entry, index) => (
                      <span key={index} className={`tool ${entry.done ? "" : "live"}`}>{toolLabel(entry.tool)}</span>
                    ))}
                  </div>
                )}
                <div className="msg" data-testid="live-answer">
                  {live.text || (live.status === "waking" ? "Your agent is waking up…" : "Working…")}
                </div>
              </>
            )}
            <div ref={bottom} />
          </div>
          <form className="composer" onSubmit={(event) => void send(event)}>
            <textarea
              value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Ask your agent to do something"
              aria-label="Message to your agent" rows={2}
              onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) void send(event); }}
            />
            <button className="primary" disabled={Boolean(live) || !draft.trim()}>Send</button>
          </form>
        </section>
        <aside className="side">
          <h3 style={{ marginTop: 0 }}>Conversations</h3>
          {sessions.data?.length ? (
            <ul>
              {sessions.data.slice(0, 8).map((item) => (
                <li key={item.id}>
                  <button className="quiet" style={{ textAlign: "left", fontWeight: item.id === sessionId ? 600 : 400 }}
                    onClick={() => setSessionId(item.id)} disabled={Boolean(live)}>
                    {item.title.slice(0, 60) || "Untitled"}
                  </button>
                </li>
              ))}
            </ul>
          ) : <p className="muted small">None yet.</p>}
        </aside>
      </div>
    </Page>
  );
}

function renderThread(messages: Message[]) {
  const blocks: React.ReactNode[] = [];
  let tools: Message[] = [];
  const flush = () => {
    if (!tools.length) return;
    blocks.push(
      <div className="tools" key={`tools-${tools[0]!.id}`}>
        {tools.map((item) => <span className="tool" key={item.id}>{toolLabel(item.content)}</span>)}
      </div>,
    );
    tools = [];
  };
  for (const message of messages) {
    if (message.role === "tool") { tools.push(message); continue; }
    flush();
    const fromColleague = message.role === "user" && /^(Request from|Reply from|Scheduled job:)|declined:/.test(message.content);
    blocks.push(
      <div key={message.id} className={`msg ${fromColleague ? "system" : message.role}`}>{message.content}</div>,
    );
  }
  flush();
  return blocks;
}

/** What my agent prepared and I must confirm: drafts in systems and replies to colleagues. */
export function Approvals() {
  const pending = useLoad(() => api<Approval[]>("/me/approvals"), [], 4000);
  const decided = useLoad(async () => [
    ...(await api<Approval[]>("/me/approvals?status=approved")),
    ...(await api<Approval[]>("/me/approvals?status=declined")),
  ], [], 8000);
  return (
    <Page title="Approvals" lede="Nothing your agent writes takes effect, and no reply leaves, until you confirm it here. You can edit the text first.">
      <ErrorNote message={pending.error} />
      {!pending.data ? <Loading /> : pending.data.length === 0 ? (
        <Empty>Nothing is waiting for you.</Empty>
      ) : pending.data.map((item) => (
        <ApprovalCard key={item.id} approval={item} onDone={() => { void pending.reload(); void decided.reload(); }} />
      ))}
      {decided.data && decided.data.length > 0 && (
        <>
          <h2>Decided</h2>
          <table>
            <thead><tr><th>What</th><th>System</th><th>Decision</th><th>When</th></tr></thead>
            <tbody>
              {decided.data.sort((a, b) => (b.decided_at ?? "").localeCompare(a.decided_at ?? "")).map((item) => (
                <tr key={item.id}>
                  <td>{item.title}</td><td>{item.system}</td>
                  <td><Tag value={item.status} label={item.status === "approved" ? "Confirmed" : "Declined"} /></td>
                  <td>{when(item.decided_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Page>
  );
}

function ApprovalCard({ approval, onDone }: { approval: Approval; onDone: () => void }) {
  const [body, setBody] = useState(approval.body);
  const { busy, error, run } = useAction();
  const edited = body !== approval.body;
  const decide = (verb: "approve" | "decline") =>
    run(() => api(`/approvals/${approval.id}/${verb}`, { body: verb === "approve" ? { body: edited ? body : null } : {} })).then((ok) => ok && onDone());
  const isReply = approval.kind === "reply";
  return (
    <div className="panel pad" style={{ marginBottom: 14 }} data-testid="approval">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3>{approval.title}</h3>
        <span className="actions">
          <Tag value="pending" label={isReply ? "Reply held" : `Draft in ${approval.system}`} />
          {approval.case_id && <Link to={`/cases/${approval.case_id}`} className="small">case {approval.case_id}</Link>}
        </span>
      </div>
      <p className="muted small">
        {isReply
          ? "Your agent wrote this reply to a colleague's request. Only this text leaves; edit it down to what they need."
          : "Your agent prepared this. Confirming makes it real in the system; declining discards it."}
      </p>
      <ErrorNote message={error} />
      <textarea rows={Math.min(18, Math.max(4, body.split("\n").length + 1))} value={body}
        onChange={(event) => setBody(event.target.value)} aria-label="Text to confirm" />
      <div className="actions" style={{ marginTop: 10 }}>
        <button className="primary" disabled={busy} onClick={() => void decide("approve")}>
          {isReply ? (edited ? "Send edited reply" : "Send reply") : (edited ? "Confirm with edits" : "Confirm")}
        </button>
        <button className="danger" disabled={busy} onClick={() => void decide("decline")}>
          {isReply ? "Do not reply" : "Discard draft"}
        </button>
      </div>
    </div>
  );
}

/** Requests my agent sent to, or received from, colleagues' agents. */
export function Requests() {
  const { person } = useSession();
  const requests = useLoad(() => api<AgentRequest[]>("/me/requests"), [], 4000);
  const [open, setOpen] = useState<string | null>(null);
  const name = (id: string | null) => (id ?? "").split("-").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
  return (
    <Page title="Requests" lede="Agents do not chat with each other. They exchange typed requests about a case; a policy decides whether a request may be sent, and the reply is approved by the colleague who owns the answer.">
      <ErrorNote message={requests.error} />
      {!requests.data ? <Loading /> : requests.data.length === 0 ? (
        <Empty>No requests yet. Your agent sends one when a task needs a colleague, for example a clinical justification for an appeal.</Empty>
      ) : (
        <table>
          <thead><tr><th>Direction</th><th>Type</th><th>Subject</th><th>Case</th><th>Status</th><th>Sent</th></tr></thead>
          <tbody>
            {requests.data.map((item) => {
              const sent = item.from_person === person.id;
              return [
                <tr key={item.id} className="clickable" onClick={() => setOpen(open === item.id ? null : item.id)}>
                  <td>{sent ? `To ${name(item.to_person)}` : `From ${name(item.from_person)}`}</td>
                  <td>{REQUEST_TYPES[item.type] ?? item.type}</td>
                  <td>{item.subject}</td>
                  <td><Link to={`/cases/${item.case_id}`} onClick={(event) => event.stopPropagation()}>{item.case_id}</Link></td>
                  <td><Tag value={item.status} label={STATUS_WORDS[item.status]} /></td>
                  <td>{when(item.created_at)}</td>
                </tr>,
                open === item.id && (
                  <tr key={`${item.id}-detail`}>
                    <td colSpan={6}>
                      <h3>Request</h3>
                      <pre className="doc">{item.body}</pre>
                      {item.status === "denied" && <p className="error" style={{ marginTop: 10 }}>Refused: {item.reason}</p>}
                      {item.reply_body && item.status === "answered" && (<><h3 style={{ marginTop: 14 }}>Approved reply</h3><pre className="doc">{item.reply_body}</pre></>)}
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      )}
    </Page>
  );
}

/** The cases I am a member of. Other cases do not exist for me. */
export function Cases() {
  const cases = useLoad(() => api<CaseSummary[]>("/me/cases"), [], 6000);
  return (
    <Page title="Cases" lede="A case holds its documents, requests and history for its members only. You see the cases you belong to; the database hides the rest.">
      <ErrorNote message={cases.error} />
      {!cases.data ? <Loading /> : cases.data.length === 0 ? <Empty>You are not a member of any case.</Empty> : (
        <table>
          <thead><tr><th>Case</th><th>Kind</th><th>Data class</th><th>Summary</th></tr></thead>
          <tbody>
            {cases.data.map((item) => (
              <tr key={item.id}>
                <td><Link to={`/cases/${item.id}`}>{item.title}</Link><div className="muted small">{item.id}</div></td>
                <td>{item.kind.replace(/_/g, " ")}</td>
                <td><Tag value={item.data_class} /></td>
                <td>{item.summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Page>
  );
}

export function CasePage() {
  const { caseId } = useParams();
  const detail = useLoad(() => api<CaseDetail>(`/cases/${caseId}`), [caseId], 5000);
  const [document, setDocument] = useState<DocumentInfo | null>(null);
  const openDocument = async (id: string) => setDocument(await api<DocumentInfo>(`/documents/${id}`));
  if (detail.error) return <Page title="Case not found"><Empty>There is no such case among the cases you belong to. <Link to="/cases">Back to my cases</Link></Empty></Page>;
  if (!detail.data) return <Loading />;
  const item = detail.data;
  return (
    <Page title={item.title} lede={item.summary}>
      <p><Tag value={item.data_class} /> <span className="muted small">{item.id}</span></p>
      <div className="grid2">
        <div>
          <h2>Members</h2>
          <table>
            <tbody>
              {item.members.map((member) => (
                <tr key={member.person_id}><td>{member.name}<div className="muted small">{member.title}</div></td><td>{member.role_in_case}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <h2>Documents</h2>
          {item.documents.length === 0 ? <Empty>No documents in this case yet.</Empty> : (
            <table>
              <tbody>
                {item.documents.map((entry) => (
                  <tr key={entry.id} className="clickable" onClick={() => void openDocument(entry.id)}>
                    <td>{entry.title}<div className="muted small">{entry.kind}, {when(entry.created_at)}</div></td>
                    <td><Tag value={entry.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {document && (
        <>
          <h2>{document.title}</h2>
          <div className="panel pad"><pre className="doc">{document.body}</pre></div>
        </>
      )}
    </Page>
  );
}

/** Recurring jobs. The central scheduler wakes my agent when one is due. */
export function Schedules() {
  const schedules = useLoad(() => api<Schedule[]>("/me/schedules"), [], 6000);
  const [form, setForm] = useState({ name: "", cron: "30 11 * * 1-5", prompt: "" });
  const { busy, error, run } = useAction();
  const add = (event: FormEvent) => {
    event.preventDefault();
    void run(() => api("/me/schedules", { body: form })).then((ok) => {
      if (ok) { setForm({ ...form, name: "", prompt: "" }); void schedules.reload(); }
    });
  };
  const act = (action: () => Promise<unknown>) => void run(action).then(() => schedules.reload());
  return (
    <Page title="Schedules" lede="Recurring jobs are kept by the platform, not inside your agent, so the agent can sleep until a job is due. Times are in UTC.">
      <ErrorNote message={schedules.error ?? error} />
      {!schedules.data ? <Loading /> : schedules.data.length === 0 ? <Empty>No recurring jobs. Add one below.</Empty> : (
        <table>
          <thead><tr><th>Job</th><th>When (cron)</th><th>Next run</th><th>Last run</th><th /></tr></thead>
          <tbody>
            {schedules.data.map((item) => (
              <tr key={item.id}>
                <td>{item.name}<div className="muted small">{item.prompt}</div></td>
                <td>{item.cron}</td>
                <td>{item.enabled ? when(item.next_run_at) : <Tag value="declined" label="Paused" />}</td>
                <td>{when(item.last_run_at)}</td>
                <td className="actions">
                  <button disabled={busy} onClick={() => act(() => api(`/me/schedules/${item.id}/run`, { method: "POST" }))}>Run now</button>
                  <button disabled={busy} onClick={() => act(() => api(`/me/schedules/${item.id}`, { method: "PATCH", body: { enabled: !item.enabled } }))}>{item.enabled ? "Pause" : "Resume"}</button>
                  <button className="danger" disabled={busy} onClick={() => act(() => api(`/me/schedules/${item.id}`, { method: "DELETE" }))}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2>Add a recurring job</h2>
      <form className="panel pad" onSubmit={add}>
        <div className="row">
          <label>Name<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
          <label>Cron (minute hour day month weekday)<input required value={form.cron} onChange={(event) => setForm({ ...form, cron: event.target.value })} /></label>
        </div>
        <label style={{ marginTop: 10 }}>What the agent should do<textarea required rows={2} value={form.prompt} onChange={(event) => setForm({ ...form, prompt: event.target.value })} /></label>
        <button className="primary" style={{ marginTop: 10 }} disabled={busy}>Add job</button>
      </form>
    </Page>
  );
}

/** The procedures my agent can load: from my role, from the shared library, and my own. */
export function Skills() {
  const { person } = useSession();
  const skills = useLoad(() => api<Skill[]>("/me/skills"), [], 6000);
  const { busy, error, run } = useAction();
  const decide = (skill: Skill, verb: string) => void run(() => api(`/me/skills/${skill.id}/${verb}`, { method: "POST" })).then(() => skills.reload());
  const scopeWords = (skill: Skill, who: Person) =>
    skill.scope === "bundle" ? "From your role" : skill.scope === "shared" ? "Shared library" : skill.owner_person_id === who.id ? "Personal" : "Shared";
  return (
    <Page title="Skills" lede="A skill is a written procedure your agent loads when a task calls for it. Your agent can propose new ones; a personal skill is yours to approve, and anything for colleagues is reviewed by an administrator.">
      <ErrorNote message={skills.error ?? error} />
      {!skills.data ? <Loading /> : (
        <table>
          <thead><tr><th>Skill</th><th>Where it comes from</th><th>Status</th><th /></tr></thead>
          <tbody>
            {skills.data.map((skill) => (
              <tr key={skill.id}>
                <td>
                  <details className="rowdetail"><summary>{skill.name}</summary><pre className="doc" style={{ marginTop: 8 }}>{skill.body}</pre></details>
                  <div className="muted small">{skill.description}</div>
                </td>
                <td>{scopeWords(skill, person)}</td>
                <td><Tag value={skill.status} label={skill.status === "approved" ? "Active" : undefined} /></td>
                <td className="actions">
                  {skill.status === "proposed" && skill.scope === "personal" && (
                    <>
                      <button className="primary" disabled={busy} onClick={() => decide(skill, "approve")}>Approve</button>
                      <button className="danger" disabled={busy} onClick={() => decide(skill, "reject")}>Reject</button>
                    </>
                  )}
                  {skill.status === "proposed" && skill.scope === "shared" && <span className="muted small">With an administrator</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Page>
  );
}
