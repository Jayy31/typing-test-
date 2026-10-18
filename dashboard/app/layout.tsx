import type { Metadata, Viewport } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { BottomNav } from "@/components/nav";
import { ThemeToggle, themeScript } from "@/components/theme";
import { fixturesMode } from "@/lib/data";
import "./globals.css";

export const metadata: Metadata = {
  title: "Options Desk",
  description: "News-driven paper trading research for NSE stock options",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f9f9f7" },
    { media: "(prefers-color-scheme: dark)", color: "#0d0d0d" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-IN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-dvh">
        <header className="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-2.5">
            <Link href="/" className="text-base font-semibold">
              Options Desk
            </Link>
            <div className="flex items-center gap-2 text-xs text-ink-2">
              <ThemeToggle />
              <span className="rounded-full bg-grid px-2 py-0.5">
                Paper trading
              </span>
              {fixturesMode() && (
                <span className="rounded-full bg-warn-wash px-2 py-0.5 text-ink">
                  Sample data
                </span>
              )}
            </div>
          </div>
          <div className="mx-auto hidden max-w-3xl px-2 pb-1 md:block">
            <BottomNav />
          </div>
        </header>
        <main className="mx-auto max-w-3xl px-4 pt-4 pb-24 md:pb-10">
          {children}
        </main>
        <div className="md:hidden">
          <BottomNav />
        </div>
      </body>
    </html>
  );
}
