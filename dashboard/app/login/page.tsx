import { LoginForm } from "./form";

export default async function Login({ searchParams }: { searchParams: Promise<{ e?: string }> }) {
  const { e } = await searchParams;
  return (
    <div className="mx-auto max-w-sm pt-10">
      <h1 className="text-xl font-semibold">Sign in</h1>
      <p className="mt-1 text-sm text-ink-2">A sign-in link is sent to the owner&apos;s email. No other email can open this dashboard.</p>
      {e === "denied" && <p className="mt-3 rounded-lg bg-bad-wash px-3 py-2 text-sm text-bad">This email is not allowed.</p>}
      {e === "config" && <p className="mt-3 rounded-lg bg-warn-wash px-3 py-2 text-sm">Supabase is not configured (NEXT_PUBLIC_SUPABASE_URL / ANON_KEY).</p>}
      <LoginForm />
    </div>
  );
}
