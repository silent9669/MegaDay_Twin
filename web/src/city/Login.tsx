import { useState, type FormEvent } from "react";
import { ArrowRight, Eye, EyeOff, ShieldCheck } from "lucide-react";
import { requestJSON } from "../studio/account";
import "./city.css";
export function Login() {
  const [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [visible, setVisible] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await requestJSON("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const next = new URLSearchParams(location.search).get("next");
      location.assign(
        next?.startsWith("/") && !next.startsWith("//") && !next.includes("\\")
          ? next
          : "/city-demo",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign in failed.");
      setBusy(false);
    }
  }
  return (
    <main className="city-login">
      <div className="login-art" aria-hidden="true">
        <span className="login-wordmark">MegaDay Twin</span>
        <div className="login-buildings">
          {Array.from({ length: 9 }, (_, i) => (
            <i key={i} style={{ height: `${100 + ((i * 73) % 220)}px` }}>
              {Array.from({ length: 12 }, (_, w) => (
                <b key={w} />
              ))}
            </i>
          ))}
        </div>
        <div className="login-art-caption">
          <span>SENSE → PLAN → CONTROL</span>
          <strong>
            Your commerce.
            <br />A living city.
          </strong>
        </div>
      </div>
      <section className="login-form-area">
        <form onSubmit={(e) => void submit(e)}>
          <span className="eyebrow">MegaDay Twin / Workspace</span>
          <h1>Enter your city.</h1>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <label htmlFor="password">Password</label>
          <div className="login-password">
            <input
              id="password"
              name="password"
              type={visible ? "text" : "password"}
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              type="button"
              aria-label={visible ? "Hide password" : "Show password"}
              onClick={() => setVisible(!visible)}
            >
              {visible ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
          {error && (
            <p role="alert" className="studio-error">
              {error}
            </p>
          )}
          <button className="login-submit" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
            <ArrowRight size={18} />
          </button>
          <div className="login-foot">
            <ShieldCheck size={15} />
            <span>Your account. One shared workspace.</span>
          </div>
        </form>
      </section>
    </main>
  );
}
