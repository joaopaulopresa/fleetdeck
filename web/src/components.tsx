import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";

/** Load data, optionally refreshing on an interval. */
export function useLoad<T>(loader: () => Promise<T>, deps: unknown[], everyMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const reload = useCallback(async () => {
    try {
      setData(await loaderRef.current());
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The server could not be reached.");
    }
  }, []);
  useEffect(() => {
    void reload();
    if (!everyMs) return;
    const timer = setInterval(() => void reload(), everyMs);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, everyMs, ...deps]);
  return { data, error, reload };
}

/** Run an action and surface its error. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      return true;
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The server could not be reached.");
      return false;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run };
}

export function Page({ title, lede, children, aside }: { title: string; lede?: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "start" }}>
        <div>
          <h1>{title}</h1>
          {lede && <p className="lede">{lede}</p>}
        </div>
        {aside}
      </div>
      {children}
    </>
  );
}

export const ErrorNote = ({ message }: { message: string | null }) =>
  message ? <div className="error" role="alert">{message}</div> : null;

export const Loading = () => <p className="muted">Loading…</p>;

export const Empty = ({ children }: { children: ReactNode }) => <div className="empty">{children}</div>;

export const Tag = ({ value, label }: { value: string; label?: string }) => (
  <span className={`tag ${value}`}>{label ?? value.replace(/_/g, " ")}</span>
);

const STATE_WORDS: Record<string, string> = {
  asleep: "Asleep", waking: "Waking", active: "Awake", suspended: "Suspended",
  provisioning: "Provisioning", error: "Did not start",
};

export function AgentState({ state, since }: { state: string; since?: number | null }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (state !== "waking") return;
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [state]);
  const elapsed = state === "waking" && since ? ` ${Math.max(0, Math.round((now - since) / 1000))} s` : "";
  return (
    <span className={`state ${state}`} data-testid="agent-state">
      <i aria-hidden="true" />
      {STATE_WORDS[state] ?? state}
      {elapsed}
    </span>
  );
}

/** Horizontal bars for one measure across a few named items, with the value written out. */
export function Bars({ rows, format }: { rows: { label: string; value: number }[]; format: (value: number) => string }) {
  const max = Math.max(...rows.map((row) => row.value), 0);
  if (!rows.length) return <Empty>No usage recorded in this period yet.</Empty>;
  return (
    <div className="bars" role="table" aria-label="Bar chart">
      {rows.map((row) => (
        <div key={row.label} role="row" style={{ display: "contents" }} title={`${row.label}: ${format(row.value)}`}>
          <span role="cell">{row.label}</span>
          <span className="track" role="presentation">
            <div className="fill" style={{ width: max ? `${(row.value / max) * 100}%` : 0 }} />
          </span>
          <span role="cell" className="num">{format(row.value)}</span>
        </div>
      ))}
    </div>
  );
}
