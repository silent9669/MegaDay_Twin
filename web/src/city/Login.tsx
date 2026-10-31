import { useState, type FormEvent } from "react";
import { ArrowRight, Eye, EyeOff } from "lucide-react";
import { requestJSON } from "../studio/account";
import "./city.css";
import { Brand } from "./Brand";
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
        <div className="login-art-image" />
      </div>
      <section className="login-form-area">
        <form aria-label="Sign in" aria-busy={busy} onSubmit={(e) => void submit(e)}>
          <div className="login-brand">
            <Brand />
          </div>
          <h1>Sign in</h1>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            required
            readOnly={busy}
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
              readOnly={busy}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "login-error" : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              type="button"
              aria-label={visible ? "Hide password" : "Show password"}
              aria-pressed={visible}
              onClick={() => setVisible(!visible)}
            >
              {visible ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
            </button>
          </div>
          {error && (
            <p id="login-error" role="alert" className="login-error">
              {error}
            </p>
          )}
          <button className="login-submit" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
            <ArrowRight size={18} aria-hidden="true" />
          </button>
        </form>
      </section>
    </main>
  );
}
