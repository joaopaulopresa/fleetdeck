import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";

import { type Config, type Person, api, getToken, setToken } from "./api";
import { ErrorNote, Loading, useLoad } from "./components";
import * as admin from "./admin";
import * as employee from "./employee";

interface SessionValue { person: Person; config: Config }
const SessionContext = createContext<SessionValue | null>(null);
export const useSession = () => {
  const value = useContext(SessionContext);
  if (!value) throw new Error("not signed in");
  return value;
};

const Logo = () => (
  <svg viewBox="0 0 32 32" aria-hidden="true">
    <rect width="32" height="32" rx="7" fill="#0b6e6e" />
    <circle cx="10" cy="11" r="3" fill="#fff" />
    <circle cx="22" cy="11" r="3" fill="#fff" opacity=".45" />
    <circle cx="10" cy="21" r="3" fill="#fff" opacity=".45" />
    <circle cx="22" cy="21" r="3" fill="#fff" />
  </svg>
);

function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const { data: people, error } = useLoad(() => api<Person[]>("/auth/people"), []);
  const [failure, setFailure] = useState<string | null>(null);
  const signIn = async (person: Person) => {
    try {
      const result = await api<{ token: string }>("/auth/login", { body: { person_id: person.id } });
      setToken(result.token);
      onSignedIn();
    } catch {
      setFailure("Sign-in failed. Try again.");
    }
  };
  return (
    <div className="signin">
      <div className="brand" style={{ color: "var(--ink)", padding: 0, marginBottom: 26 }}><Logo />Fleetdeck</div>
      <h1>Every employee has an agent. Pick one to be.</h1>
      <p className="lede">
        This is the demo sign-on for Meridian Health Group, a fictional clinic network. Each person
        below has a personal agent in its own isolated space; the platform administrator runs the fleet.
      </p>
      <ErrorNote message={error ?? failure} />
      {!people ? <Loading /> : (
        <ul className="people">
          {people.map((person) => (
            <li key={person.id}>
              <button onClick={() => void signIn(person)}>
                <span className="name">{person.name}</span>
                <span className="dept">{person.department}</span>
                <span className="muted small">{person.title}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Shell({ session, onSignOut }: { session: SessionValue; onSignOut: () => void }) {
  const { person } = session;
  const hasAgent = !person.is_admin || Boolean(person.agent_id);
  const { data: approvals } = useLoad(
    () => (person.is_admin ? Promise.resolve([]) : api<unknown[]>("/me/approvals")), [person.id], 4000);
  const link = (to: string, label: string, badge?: number) => (
    <NavLink to={to} end={to === "/"}>
      {label}
      {badge ? <span className="badge" data-testid={`badge-${label.toLowerCase()}`}>{badge}</span> : null}
    </NavLink>
  );
  return (
    <SessionContext.Provider value={session}>
      <div className="shell">
        <nav className="rail" aria-label="Main">
          <div className="brand"><Logo />Fleetdeck</div>
          {!person.is_admin && (
            <>
              {link("/", "My agent")}
              {link("/approvals", "Approvals", approvals?.length)}
              {link("/requests", "Requests")}
              {link("/cases", "Cases")}
              {link("/schedules", "Schedules")}
              {link("/skills", "Skills")}
            </>
          )}
          {person.is_admin && (
            <>
              <div className="rail-group">Administration</div>
              {link("/", "Fleet")}
              {link("/policies", "Policies")}
              {link("/connectors", "Connectors")}
              {link("/skill-library", "Skills")}
              {link("/usage", "Usage and cost")}
              {link("/audit", "Audit")}
              {link("/capacity", "Capacity")}
              {link("/demo", "Demo controls")}
            </>
          )}
          <div className="who">
            <strong>{person.name}</strong>
            {person.title}
            <br />
            <button onClick={onSignOut}>Sign out</button>
          </div>
        </nav>
        <main>
          <Routes>
            {person.is_admin ? (
              <>
                <Route path="/" element={<admin.Fleet />} />
                <Route path="/policies" element={<admin.Policies />} />
                <Route path="/connectors" element={<admin.Connectors />} />
                <Route path="/skill-library" element={<admin.SkillLibrary />} />
                <Route path="/usage" element={<admin.UsagePage />} />
                <Route path="/audit" element={<admin.Audit />} />
                <Route path="/capacity" element={<admin.CapacityPage />} />
                <Route path="/demo" element={<admin.Demo />} />
              </>
            ) : (
              <>
                <Route path="/" element={hasAgent ? <employee.Chat /> : <Navigate to="/cases" />} />
                <Route path="/approvals" element={<employee.Approvals />} />
                <Route path="/requests" element={<employee.Requests />} />
                <Route path="/cases" element={<employee.Cases />} />
                <Route path="/cases/:caseId" element={<employee.CasePage />} />
                <Route path="/schedules" element={<employee.Schedules />} />
                <Route path="/skills" element={<employee.Skills />} />
              </>
            )}
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </main>
      </div>
    </SessionContext.Provider>
  );
}

export function App() {
  const [session, setSession] = useState<SessionValue | null>(null);
  const [checking, setChecking] = useState(Boolean(getToken()));
  const navigate = useNavigate();
  const load = async () => {
    try {
      const [person, config] = await Promise.all([api<Person>("/me"), api<Config>("/config")]);
      document.title = `Fleetdeck, ${person.name}`;
      setSession({ person, config });
    } catch {
      setToken(null);
      setSession(null);
    } finally {
      setChecking(false);
    }
  };
  useEffect(() => {
    if (getToken()) void load();
  }, []);
  if (checking) return <div className="signin"><Loading /></div>;
  if (!session) return <SignIn onSignedIn={() => { navigate("/"); void load(); }} />;
  const signOut = () => {
    setToken(null);
    setSession(null);
    document.title = "Fleetdeck";
    navigate("/");
  };
  return <Shell session={session} onSignOut={signOut} />;
}
