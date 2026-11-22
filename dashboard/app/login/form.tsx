"use client";
import { createBrowserClient } from "@supabase/ssr";
import { useRouter } from "next/navigation";
import { useState } from "react";

type State = "idle" | "busy" | "sent" | "error" | "bad_login" | "rate_limited";

// Password sign-in (no email, no rate limit) with the email link as a fallback. Only the owner's
// account exists (shouldCreateUser: false) and proxy.ts still rejects any other email.
export function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<State>("idle");
  const router = useRouter();
  const client = () => createBrowserClient(process.env.NEXT_PUBLIC_SUPABASE_URL ?? "", process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "");

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    setState("busy");
    const { error } = await client().auth.signInWithPassword({ email, password });
    if (error) return setState(error.status === 429 ? "rate_limited" : "bad_login");
    router.replace("/");
    router.refresh();
  }

  async function sendLink() {
    if (!email) return;
    setState("busy");
    const { error } = await client().auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback`, shouldCreateUser: false },
    });
    setState(error ? (error.status === 429 ? "rate_limited" : "error") : "sent");
  }

  return (
    <form onSubmit={signIn} className="mt-6 space-y-3">
      <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com"
        autoComplete="username" className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-base" aria-label="Email" />
      <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password"
        autoComplete="current-password" className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-base" aria-label="Password" />
      <button disabled={state === "busy" || !password} className="w-full rounded-lg bg-s1 px-3 py-2.5 font-medium text-white disabled:opacity-60">Sign in</button>
      <button type="button" onClick={sendLink} disabled={state === "busy"} className="w-full text-sm text-muted underline">
        Email me a sign-in link instead
      </button>
      {state === "sent" && <p className="text-sm text-good-ink">Check your inbox for the link (newest email, click once).</p>}
      {state === "bad_login" && <p className="text-sm text-bad">Wrong email or password.</p>}
      {state === "rate_limited" && <p className="text-sm text-bad">Too many attempts. Wait a few minutes and try again.</p>}
      {state === "error" && <p className="text-sm text-bad">Could not send the link.</p>}
    </form>
  );
}
