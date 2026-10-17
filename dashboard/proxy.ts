/** Auth gate (Next 16 "proxy", formerly middleware): every page needs a Supabase session for
 * DASHBOARD_ALLOWED_EMAIL. Row-level security enforces the same rule in the database. */
import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

export async function proxy(req: NextRequest) {
  if (process.env.DASHBOARD_FIXTURES === "1") return NextResponse.next();
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const open = req.nextUrl.pathname.startsWith("/login") || req.nextUrl.pathname.startsWith("/auth");
  if (!url || !key) return open ? NextResponse.next() : NextResponse.redirect(new URL("/login?e=config", req.url));
  let res = NextResponse.next({ request: req });
  const sb = createServerClient(url, key, {
    cookies: {
      getAll: () => req.cookies.getAll(),
      setAll: (all) => {
        all.forEach(({ name, value }) => req.cookies.set(name, value));
        res = NextResponse.next({ request: req });
        all.forEach(({ name, value, options }) => res.cookies.set(name, value, options));
      },
    },
  });
  const { data } = await sb.auth.getUser();
  const allowed = (process.env.DASHBOARD_ALLOWED_EMAIL ?? "").trim().toLowerCase();
  const email = data.user?.email?.toLowerCase();
  if (!open && (!email || (allowed && email !== allowed))) {
    if (email) await sb.auth.signOut();
    return NextResponse.redirect(new URL(email ? "/login?e=denied" : "/login", req.url));
  }
  return res;
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
