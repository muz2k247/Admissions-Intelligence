import { useState } from "react";

export default function SignIn({ onSignIn }) {
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleSignIn() {
    setError(null);
    setBusy(true);
    try {
      await onSignIn();
    } catch (e) {
      setError(e?.message || "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="screen screen--center">
      <div className="card signin">
        <h1>Curator Review</h1>
        <p className="muted">
          Sign in to review and correct extracted admissions data. Only
          allowlisted curators can save corrections.
        </p>
        <button className="btn btn--primary" onClick={handleSignIn} disabled={busy}>
          {busy ? "Signing in…" : "Sign in with Google"}
        </button>
        {error && <p className="error" role="alert">{error}</p>}
      </div>
    </div>
  );
}
