"use client";
import { createBrowserClient } from "@supabase/ssr";
import { useState } from "react";

export function LoginForm() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sent" | "error">("idle");
  async function send(e: React.FormEvent) {
    e.preventDefault();
    const sb = createBrowserClient(process.env.NEXT_PUBLIC_SUPABASE_URL ?? "", process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "");
    const { error } = await sb.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback`, shouldCreateUser: false },
    });
    setState(error ? "error" : "sent");
  }
  return (
    <form onSubmit={send} className="mt-6 space-y-3">
      <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com"
        className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-base" aria-label="Email" />
      <button className="w-full rounded-lg bg-s1 px-3 py-2.5 font-medium text-white">Send sign-in link</button>
      {state === "sent" && <p className="text-sm text-good-ink">Check your inbox for the link.</p>}
      {state === "error" && <p className="text-sm text-bad">Could not send the link.</p>}
    </form>
  );
}
